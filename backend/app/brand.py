"""Single source of product identity for the backend.

``frontend/src/brand.ts`` agrees with this module (a consistency test
enforces it), and the slug appears in no other module (a boundary test
enforces that).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    #: Human-facing product name (UI titles, messages).
    display_name: str = "Weighthouse"
    #: Machine-facing slug (cookies, thread names, user agents, package hints).
    slug: str = "weighthouse"
    #: One-line product description shown in the API surface.
    description: str = "Self-hosted model library: browse, acquire, track, and place model weights."

    #: Browser session cookie name.
    session_cookie: str = f"{slug}-session"
    #: Hugging Face API "library" hint sent with hub calls.
    hf_library_name: str = slug
    #: Write-permission probe filename used during storage setup.
    fs_probe_filename: str = ".write-test"


BRAND = Brand()
