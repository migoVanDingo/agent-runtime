"""Vertex AI Gemini provider.

Same `google-genai` SDK as the `gemini` provider, just constructed with
`Client(vertexai=True, project=..., location=...)` instead of an API key.
Authenticates via standard GCP credentials (`google.auth.default()`) — the
same SA used by `arc-plugin-gcs` works.

Key extra capability over `gemini`: this provider **auto-attaches `gs://`
URIs from tool results as `file_data` Parts in the next request**. The
video sub-agent (per 0022) relies on this — its `gcs_stat` call returns a
JSON tool result; the provider sees `{uri: "gs://...", content_type:
"video/mp4"}` and pipes the URI into Vertex's multimodal request.

Authentication errors map to clear user-actionable messages pointing at
`gcloud services enable aiplatform.googleapis.com` or
`roles/aiplatform.user`.
"""
from __future__ import annotations

import time
from typing import Any

from arc.config import ProviderConfig
from arc.providers._gemini_translation import (
    append_file_data_to_last_user_message,
    find_auto_attach_file,
    messages_to_contents,
    response_to_llm_response,
    tools_to_gemini,
)
from arc.runtime.hooks import LLMRequest, LLMResponse


class VertexGeminiProvider:
    """Vertex-backed Gemini provider. Parallel to GeminiProvider."""

    name = "vertex_gemini"

    def __init__(self, cfg: ProviderConfig) -> None:
        project = cfg.params.get("project_id") or cfg.params.get("vertex_project_id")
        if not project:
            raise ValueError(
                "vertex_gemini provider requires params.project_id "
                "(or params.vertex_project_id) — set it in provider config "
                "or in the sub-agent spec's params"
            )
        region = (
            cfg.params.get("region")
            or cfg.params.get("vertex_region")
            or "us-central1"
        )

        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "vertex_gemini provider needs google-genai installed: "
                "`pip install google-genai`"
            ) from exc

        self._cfg = cfg
        self._project = project
        self._region = region
        try:
            self._client = genai.Client(
                vertexai=True,
                project=project,
                location=region,
            )
        except Exception as exc:
            # Most common failures here are auth-related — surface with
            # actionable hints.
            raise RuntimeError(
                f"vertex_gemini client init failed (project={project}, "
                f"region={region}): {type(exc).__name__}: {exc}\n"
                f"  Common causes:\n"
                f"    - Vertex AI API not enabled: "
                f"`gcloud services enable aiplatform.googleapis.com`\n"
                f"    - Service account missing role: "
                f"`gcloud projects add-iam-policy-binding {project} "
                f"--member='serviceAccount:<sa>' --role='roles/aiplatform.user'`\n"
                f"    - GOOGLE_APPLICATION_CREDENTIALS not set or invalid"
            ) from exc

    # ── Public entry point ─────────────────────────────────────────────────

    def chat(self, req: LLMRequest) -> LLMResponse:
        """Send a request, retry per policy, return a translated response.

        Auto-attach: scans messages for tool results carrying
        {uri: "gs://...", content_type: "video/*|image/*|audio/*"}; when found,
        appends a `file_data` Part to the last user-role message before sending.
        """
        from google.genai import types

        contents = messages_to_contents(req.messages)
        gemini_tools = tools_to_gemini(req.tools) if req.tools else None

        # Auto-attach gs:// URIs surfaced by recent tool results (e.g., from
        # gcs_stat). Vertex fetches the file via IAM with the same SA arc
        # uses for auth.
        attach = find_auto_attach_file(req.messages)
        if attach is not None:
            uri, mime_type = attach
            append_file_data_to_last_user_message(contents, uri, mime_type)

        gen_config = types.GenerateContentConfig(
            system_instruction=req.system or None,
            temperature=req.params.get("temperature"),
            max_output_tokens=req.params.get("max_tokens"),
            top_p=req.params.get("top_p"),
            tools=gemini_tools,
        )

        resp = self._call_with_retry(req.model, contents, gen_config)
        return response_to_llm_response(resp)

    # ── Retry loop ─────────────────────────────────────────────────────────

    def _call_with_retry(self, model: str, contents: Any, config: Any) -> Any:
        """Exponential backoff per `config.provider.retry`.

        Same shape as GeminiProvider's retry. Auth/quota errors (403/429)
        get clearer messages so the user can fix the GCP-side issue.
        """
        cfg = self._cfg.retry
        backoff = cfg.backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                # Don't retry on permission errors — they won't fix themselves.
                msg = str(exc)
                if "403" in msg or "PermissionDenied" in type(exc).__name__:
                    raise RuntimeError(
                        f"Vertex 403 PermissionDenied — service account "
                        f"likely missing 'roles/aiplatform.user' on project "
                        f"{self._project!r}. Original: {exc}"
                    ) from exc
                if "429" in msg or "ResourceExhausted" in type(exc).__name__:
                    raise RuntimeError(
                        f"Vertex 429 ResourceExhausted — project quota hit "
                        f"on {self._project!r} ({self._region}). Check "
                        f"GCP console quotas for Vertex AI. Original: {exc}"
                    ) from exc
                if attempt >= cfg.max_attempts:
                    break
                time.sleep(min(backoff, cfg.backoff_max_seconds))
                backoff *= 2

        raise RuntimeError(
            f"Vertex Gemini call failed after {cfg.max_attempts} attempts: {last_exc}"
        ) from last_exc
