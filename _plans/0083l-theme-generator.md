# 0083l — Theme generator

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §5.3.
> Depends on: **0083i** (theme system), **0083j** (settings store).

## Goal

Implement `/theme generate` which opens a `ThemeGeneratorScreen` with three
approaches for creating a new theme:

1. **Palette input** — user enters hex codes for key colors; the rest derive automatically.
2. **From description** — natural-language prompt to the LLM (direct Anthropic
   API call, not through the agent pipeline — per decision Q1 in the design doc).
3. **From image** — pick an image file; extract dominant colors (uses `colorthief`
   if installed; gracefully absent otherwise).

Generated themes write a `.tcss` file to `~/.arc/themes/<name>.tcss` and
immediately become selectable via `/theme <name>`.

## Files to create / modify

| File | Action |
|------|--------|
| `src/ui/theme_generator.py` | **Create** — LLM + color derivation logic |
| `src/ui/screens/theme_picker.py` | **Create** — `ThemeGeneratorScreen` modal |
| `src/ui/commands/builtin.py` | **Modify** — wire `/theme generate` to real modal |

## Detailed implementation

### `src/ui/theme_generator.py`

```python
"""Theme generation logic for arc-tui.

Three approaches:
  1. palette_from_hex()     — derive a full theme from user-supplied key colors
  2. palette_from_llm()     — ask the LLM for a palette via direct Anthropic call
  3. palette_from_image()   — extract dominant colors from an image file

All three return a ThemePalette dict that write_theme_file() converts to TCSS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_USER_THEME_DIR = Path.home() / ".arc" / "themes"

# The 11 CSS variable names every theme must define (see _vars.tcss).
_PALETTE_KEYS = [
    "bg", "bg-elevated", "surface",
    "text", "text-dim",
    "primary", "accent",
    "success", "warning", "error",
    "border",
]


@dataclass
class ThemePalette:
    """A complete set of color values for a theme."""
    bg:          str = "#1e1e1e"
    bg_elevated: str = "#252526"
    surface:     str = "#2d2d30"
    text:        str = "#d4d4d4"
    text_dim:    str = "#858585"
    primary:     str = "#007acc"
    accent:      str = "#00ff87"
    success:     str = "#4ec9b0"
    warning:     str = "#dcdcaa"
    error:       str = "#f48771"
    border:      str = "#3e3e42"


def _validate_hex(color: str) -> str:
    """Normalise a hex color to lowercase 7-char form. Raises ValueError if invalid."""
    c = color.strip()
    if not c.startswith("#"):
        c = "#" + c
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
        raise ValueError(f"Invalid hex color: {color!r}. Expected format: #RRGGBB")
    return c.lower()


def _lighten(hex_color: str, factor: float = 0.15) -> str:
    """Return a lightened version of a hex color."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def _darken(hex_color: str, factor: float = 0.15) -> str:
    """Return a darkened version of a hex color."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    r = max(0, int(r * (1 - factor)))
    g = max(0, int(g * (1 - factor)))
    b = max(0, int(b * (1 - factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def palette_from_hex(
    bg: str,
    primary: str,
    accent: str,
    text: str = "",
) -> ThemePalette:
    """Derive a complete palette from 3-4 user-supplied hex colors.

    Missing colors are derived algorithmically:
    - bg-elevated and surface are lighter shades of bg
    - text defaults to a near-white if bg is dark
    - text-dim is a mid-grey
    - success/warning/error are standard semantic colors (not derived from palette)
    - border matches surface
    """
    bg_v        = _validate_hex(bg)
    primary_v   = _validate_hex(primary)
    accent_v    = _validate_hex(accent)
    bg_elev     = _lighten(bg_v, 0.05)
    surface     = _lighten(bg_v, 0.10)
    text_v      = _validate_hex(text) if text else "#d4d4d4"
    text_dim    = _darken(text_v, 0.40)

    return ThemePalette(
        bg=bg_v,
        bg_elevated=bg_elev,
        surface=surface,
        text=text_v,
        text_dim=text_dim,
        primary=primary_v,
        accent=accent_v,
        success="#4ec9b0",
        warning="#dcdcaa",
        error="#f48771",
        border=surface,
    )


def palette_from_image(image_path: str) -> ThemePalette:
    """Extract dominant colors from an image file using colorthief.

    Falls back gracefully if colorthief is not installed.
    Raises ImportError with an install hint if the package is missing.
    """
    try:
        from colorthief import ColorThief
    except ImportError:
        raise ImportError(
            "colorthief is required for image-based theme generation. "
            "Install with: pip install colorthief"
        )
    ct = ColorThief(image_path)
    # Get 6-color palette from image.
    palette = ct.get_palette(color_count=6, quality=1)
    if not palette:
        raise ValueError(f"Could not extract palette from image: {image_path!r}")

    def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    # Map image colors to theme roles heuristically:
    # darkest → bg, lightest → text, most saturated → primary/accent
    def _luminance(rgb: tuple[int, int, int]) -> float:
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    sorted_by_lum = sorted(palette, key=_luminance)
    bg = _rgb_to_hex(sorted_by_lum[0])
    text_col = _rgb_to_hex(sorted_by_lum[-1])
    primary = _rgb_to_hex(sorted_by_lum[len(sorted_by_lum) // 2])
    accent = _rgb_to_hex(sorted_by_lum[min(len(sorted_by_lum) - 2, len(sorted_by_lum) // 2 + 1)])

    return palette_from_hex(bg=bg, primary=primary, accent=accent, text=text_col)


async def palette_from_llm(description: str) -> ThemePalette:
    """Ask the LLM (direct Anthropic call) for a color palette.

    Per design decision Q1: this bypasses the agent pipeline entirely.
    Uses a direct anthropic.Anthropic() client call to avoid polluting
    the conversation history.

    Returns a ThemePalette or raises ValueError if the LLM response
    cannot be parsed.
    """
    import json
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic SDK is required for LLM-based theme generation.")

    prompt = f"""Generate a terminal color theme based on this description: {description!r}

Return ONLY a JSON object with these exact keys and hex color values (#RRGGBB):
{{
  "bg": "#...",
  "bg_elevated": "#...",
  "surface": "#...",
  "text": "#...",
  "text_dim": "#...",
  "primary": "#...",
  "accent": "#...",
  "success": "#...",
  "warning": "#...",
  "error": "#...",
  "border": "#..."
}}

Guidelines:
- bg should be the darkest background color
- text should be readable on bg (contrast ratio >= 4.5:1)
- primary and accent should be vibrant and distinct
- success = green-ish, warning = yellow-ish, error = red-ish
- Return ONLY the JSON — no explanation, no markdown, no code blocks."""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5",   # fast + cheap for this one-shot task
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if the model included them despite instructions.
    raw_text = re.sub(r"^```json?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON response: {raw_text[:200]!r}") from exc

    # Validate all keys are present and hex-valid.
    required = {
        "bg", "bg_elevated", "surface", "text", "text_dim",
        "primary", "accent", "success", "warning", "error", "border",
    }
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    for k, v in data.items():
        data[k] = _validate_hex(v)

    return ThemePalette(**data)


def write_theme_file(name: str, palette: ThemePalette) -> Path:
    """Write a ThemePalette to ~/.arc/themes/<name>.tcss.

    Returns the path of the written file.
    Raises ValueError if the name contains path-unsafe characters.
    """
    if not re.fullmatch(r"[a-z0-9_\-]+", name):
        raise ValueError(
            f"Theme name {name!r} must contain only lowercase letters, digits, hyphens, underscores."
        )

    _USER_THEME_DIR.mkdir(parents=True, exist_ok=True)
    path = _USER_THEME_DIR / f"{name}.tcss"

    # Map dataclass field names (bg_elevated) to CSS var names (bg-elevated).
    lines = [
        f"/* Generated theme: {name} */",
        f'$bg:           {palette.bg};',
        f'$bg-elevated:  {palette.bg_elevated};',
        f'$surface:      {palette.surface};',
        f'$text:         {palette.text};',
        f'$text-dim:     {palette.text_dim};',
        f'$primary:      {palette.primary};',
        f'$accent:       {palette.accent};',
        f'$success:      {palette.success};',
        f'$warning:      {palette.warning};',
        f'$error:        {palette.error};',
        f'$border:       {palette.border};',
    ]
    path.write_text("\n".join(lines) + "\n")
    return path
```

### `src/ui/screens/theme_picker.py`

```python
"""ThemeGeneratorScreen — modal for creating a custom theme.

Three tabs: Palette Input, From Description, From Image.
On success, writes a .tcss file to ~/.arc/themes/ and applies the theme.
"""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button, Input, Label, Static, TabbedContent, TabPane,
    )
    from textual.containers import Vertical
    from textual import on
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from ui.theme_generator import (
    palette_from_hex, palette_from_image, palette_from_llm, write_theme_file,
)


class ThemeGeneratorScreen(ModalScreen):
    """Modal for generating a new custom theme."""

    CSS = """
    ThemeGeneratorScreen {
        align: center middle;
    }
    #gen-dialog {
        width: 70;
        height: 28;
        background: $bg-elevated;
        border: round $primary;
        padding: 1;
    }
    #gen-status {
        color: $text-dim;
        height: 2;
        margin-top: 1;
    }
    .input-row {
        margin-bottom: 1;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="gen-dialog"):
            yield Static("[bold]Generate Theme[/bold]")
            with TabbedContent():
                with TabPane("Hex Palette", id="tab-hex"):
                    yield Label("Background color (#RRGGBB):")
                    yield Input(placeholder="#1e1e1e", id="hex-bg")
                    yield Label("Primary color (#RRGGBB):")
                    yield Input(placeholder="#007acc", id="hex-primary")
                    yield Label("Accent color (#RRGGBB):")
                    yield Input(placeholder="#00ff87", id="hex-accent")
                    yield Label("Theme name (lowercase, no spaces):")
                    yield Input(placeholder="mytheme", id="hex-name")
                    yield Button("Generate", id="btn-hex-gen", variant="primary")

                with TabPane("From Description", id="tab-llm"):
                    yield Label("Describe the theme (e.g. 'misty forest at dawn'):")
                    yield Input(placeholder="...", id="llm-description")
                    yield Label("Theme name:")
                    yield Input(placeholder="mytheme", id="llm-name")
                    yield Button("Generate with AI", id="btn-llm-gen", variant="primary")

                with TabPane("From Image", id="tab-image"):
                    yield Label("Image file path:")
                    yield Input(placeholder="/path/to/image.png", id="img-path")
                    yield Label("Theme name:")
                    yield Input(placeholder="mytheme", id="img-name")
                    yield Button("Extract Colors", id="btn-img-gen", variant="primary")

            yield Static("", id="gen-status")

    def _set_status(self, msg: str, error: bool = False) -> None:
        style = "red" if error else "green"
        self.query_one("#gen-status", Static).update(f"[{style}]{msg}[/{style}]")

    @on(Button.Pressed, "#btn-hex-gen")
    async def on_hex_generate(self) -> None:
        bg      = self.query_one("#hex-bg", Input).value
        primary = self.query_one("#hex-primary", Input).value
        accent  = self.query_one("#hex-accent", Input).value
        name    = self.query_one("#hex-name", Input).value.strip()
        if not name:
            self._set_status("Please enter a theme name.", error=True)
            return
        try:
            palette = palette_from_hex(bg=bg, primary=primary, accent=accent)
            path = write_theme_file(name, palette)
            # Apply the new theme immediately.
            from ui.theme_loader import get_theme_loader
            get_theme_loader().apply(self.app, name)
            self._set_status(f"Theme '{name}' created and applied! ({path})")
        except (ValueError, Exception) as exc:
            self._set_status(f"Error: {exc}", error=True)

    @on(Button.Pressed, "#btn-llm-gen")
    async def on_llm_generate(self) -> None:
        description = self.query_one("#llm-description", Input).value.strip()
        name        = self.query_one("#llm-name", Input).value.strip()
        if not description or not name:
            self._set_status("Please fill in both fields.", error=True)
            return
        self._set_status("Generating with AI...")
        try:
            palette = await palette_from_llm(description)
            path = write_theme_file(name, palette)
            from ui.theme_loader import get_theme_loader
            get_theme_loader().apply(self.app, name)
            self._set_status(f"Theme '{name}' generated and applied! ({path})")
        except Exception as exc:
            self._set_status(f"Error: {exc}", error=True)

    @on(Button.Pressed, "#btn-img-gen")
    async def on_image_generate(self) -> None:
        img_path = self.query_one("#img-path", Input).value.strip()
        name     = self.query_one("#img-name", Input).value.strip()
        if not img_path or not name:
            self._set_status("Please fill in both fields.", error=True)
            return
        try:
            palette = palette_from_image(img_path)
            path = write_theme_file(name, palette)
            from ui.theme_loader import get_theme_loader
            get_theme_loader().apply(self.app, name)
            self._set_status(f"Theme '{name}' extracted and applied! ({path})")
        except ImportError as exc:
            self._set_status(str(exc), error=True)
        except Exception as exc:
            self._set_status(f"Error: {exc}", error=True)

    def action_dismiss(self) -> None:
        self.dismiss()
```

### Wire `/theme generate` in `builtin.py`

Replace the stub from Phase 0083h:

```python
async def _cmd_theme(app: "ArcApp", args: str) -> None:
    from ui.theme_loader import get_theme_loader
    loader = get_theme_loader()
    parts = args.strip().split()
    if not parts:
        app.notify("Themes: " + ", ".join(loader.available()), timeout=5)
        return
    name = parts[0]
    if name == "generate":
        from ui.screens.theme_picker import ThemeGeneratorScreen
        await app.push_screen(ThemeGeneratorScreen())
        return
    ok = loader.apply(app, name)
    if ok:
        app.notify(f"Theme: {name}")
        # Persist the selection.
        from ui.settings_store import get_settings_store
        get_settings_store().set("theme", name)
    else:
        app.notify(f"Unknown theme: {name}", severity="error")
```

## Verification

```bash
# 1. palette_from_hex round-trip
python - <<'EOF'
from ui.theme_generator import palette_from_hex, write_theme_file
import tempfile, pathlib

palette = palette_from_hex(bg="#282a36", primary="#bd93f9", accent="#50fa7b")
assert palette.bg == "#282a36"
assert palette.primary == "#bd93f9"

# Write to temp location (override the default ~/.arc/themes path for testing)
import ui.theme_generator as tg
orig = tg._USER_THEME_DIR
tg._USER_THEME_DIR = pathlib.Path(tempfile.mkdtemp())
try:
    path = write_theme_file("test-theme", palette)
    assert path.exists()
    content = path.read_text()
    assert "$bg:" in content
    assert "#282a36" in content
    print(f"write_theme_file: ok, wrote {path}")
finally:
    tg._USER_THEME_DIR = orig
EOF

# 2. palette_from_llm (requires API key)
python - <<'EOF'
import asyncio, os
if not os.getenv("ANTHROPIC_API_KEY"):
    print("SKIP: no API key")
else:
    from ui.theme_generator import palette_from_llm
    async def test():
        p = await palette_from_llm("ocean depths at midnight")
        print(f"LLM palette: bg={p.bg} primary={p.primary} accent={p.accent}")
    asyncio.run(test())
EOF

# 3. Manual: launch arc-tui, type /theme generate
#    - TabPane shows three tabs
#    - Hex input: enter #282a36, #bd93f9, #50fa7b, name "my-dracula-variant"
#    - Click Generate → status shows success, UI repaints
#    - /theme list includes "my-dracula-variant"

# 4. Existing tests pass
pytest -x -q
```

## Done when

- [ ] `src/ui/theme_generator.py` created with `palette_from_hex`, `palette_from_image`, `palette_from_llm`, `write_theme_file`.
- [ ] `src/ui/screens/theme_picker.py` created: `ThemeGeneratorScreen` with three tabs.
- [ ] `/theme generate` opens the generator modal.
- [ ] Hex palette input generates a valid TCSS file, applies the theme, and adds it to the theme list.
- [ ] LLM generation bypasses the agent pipeline (direct `anthropic.Anthropic()` call).
- [ ] `colorthief` absence raises a helpful `ImportError` with install instructions.
- [ ] Generated theme files appear in `~/.arc/themes/` and in `/theme list`.
- [ ] `pytest` green.

## Out of scope for this phase

- Color contrast validation / accessibility checking.
- Undo / discard generated theme before applying.
- Preview panel showing the theme colors before writing to disk.
