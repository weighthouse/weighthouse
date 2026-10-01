"""Single source of product identity.

Every user-visible or wire-visible occurrence of the product's name or slug
lives here (backend) or in ``frontend/src/brand.ts`` (frontend); nothing else
in the codebase carries identity. That centralization is deliberate
engineering hygiene (charter decision D12): renaming or rebranding means
editing these modules plus the design tokens and rebuilding — there is no
runtime theming, per-deployment branding, or selectable skin.

The manifest filename is deliberately brand-neutral (``.modelmanifest.json``):
manifests are the durable library contract and must survive any future
rebranding unchanged. The legacy HuggingHack manifest name is kept for
read-compatibility with libraries produced by the upstream project this code
descends from; it is never written.

A cross-language consistency test asserts this module and ``brand.ts`` agree;
a boundary lint test asserts the slug never leaks outside the brand modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    #: Human-facing product name (UI titles, messages).
    display_name: str = "ModelHarbor"
    #: Machine-facing slug (cookies, thread names, user agents, package hints).
    slug: str = "modelharbor"
    #: One-line product description shown in the API surface.
    description: str = "Self-hosted model library: browse, acquire, track, and place model weights."

    #: Durable library manifest filename. Brand-neutral by design.
    manifest_filename: str = ".modelmanifest.json"
    #: Upstream HuggingHack manifest filename; read-compat only, never written.
    legacy_manifest_filename: str = ".hugginghack.json"

    #: Namespace for transient partial files (chunked uploads, S3 restore).
    #: Derived from the slug so the product identity stays visible while the
    #: file is in flight; legacy upstream partial names are ignored, never
    #: written.
    part_suffix: str = f".{slug}-part"

    #: Browser session cookie name.
    session_cookie: str = f"{slug}-session"
    #: SQLite filename used when no DATABASE_URL is configured.
    sqlite_filename: str = f"{slug}.sqlite3"
    #: Hugging Face API "library" hint sent with hub calls.
    hf_library_name: str = slug
    #: Write-permission probe filename used during storage setup.
    fs_probe_filename: str = ".write-test"


BRAND = Brand()
