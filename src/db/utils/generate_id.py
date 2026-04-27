"""Generate prefixed ULID identifiers.

Format: {PREFIX}{ULID}
Example: ARTI01ARZ3NDEKTSV4RRFFQ69G5FAV

ULIDs are time-ordered, URL-safe, and collision-resistant.
Uses the ulid-py package (already in requirements).
"""
import ulid as _ulid

from db.utils.id_prefix import IdPrefix


def generate_id(prefix: IdPrefix) -> str:
    """Return a new prefixed ULID string."""
    return f"{prefix}{_ulid.new().str}"
