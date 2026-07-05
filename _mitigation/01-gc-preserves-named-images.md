# 01 — gc preserves build-once images

**Mitigates:** `02-security-audit.md` M4 (also referenced in `03-code-quality.md`
and `00-README.md`). Self-inflicted regression from the image/GC work.

## The problem
`DockerBackend.prune_images(only_unused=True)` treated any managed image not
currently backing a container as reclaimable. A named `image_build` image with
no running container — i.e. exactly the state right after a teardown — was
force-removed by `gc`, destroying the reusable artifact and contradicting both
the build-once-run-many intent and the tool's "Safe" docstring.

## The fix
`cos/src/cos/core/backend.py` — `prune_images` now skips managed images that
carry a `cos.name` label (the marker `build_image` sets). Those are intentional
build-once artifacts and are reclaimed **only** via explicit `image_remove`. `gc`
prunes just the disposable cache (`cos-gen:*` / `cos-build:*`, which have no
`cos.name`).

```python
for img in self.client.images.list(filters={"label": f"{L.MANAGED}=true"}):
    if (img.labels or {}).get(L.NAME):
        continue  # intentional build-once image — not cache; skip
    candidates[img.id] = img
```

## Verification
- New live test `test_gc_preserves_named_build_image` — builds a named image,
  runs `gc`, asserts it survives.
- Existing `test_gc_reclaims_stopped_network_and_unused_image` updated to build a
  **cache** image (base+provision) and assert `gc` still reclaims a `cos-gen:*`
  tag — so cache reclamation is still covered.

## Residual
Truly-orphaned named images accumulate until `image_remove`'d — acceptable
(intentional artifacts shouldn't vanish under the user). A future `cos image
prune --named --older-than` could offer opt-in reclamation.
