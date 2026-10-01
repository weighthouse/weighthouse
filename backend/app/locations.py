"""Named storage locations.

Charter decision D3 deliberately does not bake in a storage topology:
no assumption of a NAS, no assumption of two tiers. Locations are how
"the library lives in N places" becomes concrete without rebuilding the
product around a fixed idea of a tier. A location is a *name you chose*
plus an absolute root path — nothing more. Any serving-tier labels live
in the names themselves ("nvme-rig", "nas-archive"), not in a schema the
app enforces.

Locations are an administrator-declared allowlist (`LOCATIONS_JSON`).
Nobody types paths into a UI; every root the app will ever touch is
declared in configuration up front, which keeps path confinement
structural. The `MODEL_STORAGE` root is always registered — implicitly
as ``primary``, or under the declared name of whatever entry points at
the same root.

Parsing fails loudly with precise messages: a typo that silently drops
a location would be worse than a startup that refuses to begin.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

LOCATION_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DEFAULT_LOCATION_NAME = "primary"
ENV_NAME = "LOCATIONS_JSON"
MAX_LOCATIONS = 16
MAX_JSON_BYTES = 100_000


class LocationError(ValueError):
    """Raised when the locations configuration is unusable."""


@dataclass(frozen=True)
class StorageLocation:
    """One declared storage root: a name and an absolute path."""

    name: str
    root: Path

    def to_payload(self) -> dict[str, str]:
        return {"name": self.name, "root": str(self.root)}


def parse_locations(
    raw: str | None,
    *,
    default_root: Path,
) -> tuple[list[StorageLocation], str]:
    """Parse the declared allowlist, always including the default root.

    The ``MODEL_STORAGE`` root is always registered implicitly as
    ``primary`` and is not re-declarable under another name (declaring
    it again is an error, not a rename — one rule, no surprises).
    Returns the locations (primary first) and the primary name. Raises
    ``LocationError`` with a precise message when the configuration is
    unusable; callers are expected to let that reach startup rather
    than silently dropping a root.
    """
    text = str(raw or "").strip()
    if len(text) > MAX_JSON_BYTES:
        raise LocationError(f"{ENV_NAME} is too large.")
    default_resolved = Path(default_root).expanduser().resolve()
    declared: list[StorageLocation] = []
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise LocationError(f"{ENV_NAME} is invalid JSON: {error.msg}.") from error
        if not isinstance(payload, list):
            raise LocationError(f"{ENV_NAME} must contain a JSON array.")
        if len(payload) > MAX_LOCATIONS:
            raise LocationError(f"At most {MAX_LOCATIONS} locations may be configured.")
        seen_names: set[str] = set()
        seen_roots: dict[Path, str] = {}
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise LocationError(f"Location {index} must be an object with name and root.")
            name = str(item.get("name") or "").strip().lower()
            if not LOCATION_NAME_PATTERN.fullmatch(name):
                raise LocationError(
                    f"Location {index} has an invalid name "
                    "(1-40 lowercase letters, numbers, underscores, or hyphens)."
                )
            if name == DEFAULT_LOCATION_NAME:
                raise LocationError(
                    f"'{DEFAULT_LOCATION_NAME}' is reserved for the default storage root."
                )
            if name in seen_names:
                raise LocationError(f"Location name '{name}' is declared more than once.")
            raw_root = str(item.get("root") or "").strip()
            if not raw_root:
                raise LocationError(f"Location '{name}' is missing its root path.")
            root = Path(raw_root).expanduser()
            if not root.is_absolute():
                raise LocationError(f"Location '{name}' must use an absolute path.")
            resolved = root.resolve()
            if resolved == default_resolved:
                raise LocationError(
                    f"Location '{name}' points at the default storage root, which is "
                    f"already registered as '{DEFAULT_LOCATION_NAME}'."
                )
            if resolved in seen_roots:
                raise LocationError(
                    f"Locations '{seen_roots[resolved]}' and '{name}' point at the same root."
                )
            seen_names.add(name)
            seen_roots[resolved] = name
            declared.append(StorageLocation(name=name, root=resolved))
    declared.insert(0, StorageLocation(name=DEFAULT_LOCATION_NAME, root=default_resolved))
    return declared, DEFAULT_LOCATION_NAME


def find_location(
    locations: list[StorageLocation],
    name: str | None,
) -> StorageLocation | None:
    if not name:
        return None
    wanted = str(name).strip().lower()
    return next((loc for loc in locations if loc.name == wanted), None)
