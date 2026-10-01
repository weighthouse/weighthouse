"""Rename-robustness guardrails.

These tests keep the product renameable:

1. The brand slug must appear only in the brand modules
   (``backend/app/brand.py`` and ``frontend/src/brand.ts``) and in
   packaging fixtures that name the product on purpose (README,
   LICENSE, docker-compose defaults, launchers). Source code must
   derive identity from ``BRAND`` so a rebrand touches two files.
2. The durable manifest filename must stay brand-neutral: no slug,
   and never the upstream name.
3. Both brand modules must agree, so a rebrand cannot make the
   frontend and the backend drift apart.

Exclusions are explicit. ``frontend/index.html`` carries the display
name because the Vite shell is static; a rebrand replaces its strings.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.brand import BRAND

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


def backend_python_sources() -> list[Path]:
    return sorted(
        [
            *BACKEND_ROOT.glob("app/*.py"),
            *BACKEND_ROOT.glob("tests/*.py"),
        ]
    )


def test_backend_slug_lives_only_in_brand_module():
    allowed = BACKEND_ROOT / "app" / "brand.py"
    offenders = [
        path
        for path in backend_python_sources()
        if path != allowed
        and re.search(re.escape(BRAND.slug), path.read_text(encoding="utf-8"), re.IGNORECASE)
    ]
    assert offenders == [], f"slug leaked outside brand.py: {offenders}"


def test_frontend_slug_lives_only_in_brand_module():
    allowed = FRONTEND_SRC / "brand.ts"
    offenders = [
        path
        for path in FRONTEND_SRC.rglob("*")
        if path.is_file()
        and path != allowed
        and re.search(re.escape(BRAND.slug), path.read_text(encoding="utf-8"), re.IGNORECASE)
    ]
    assert offenders == [], f"slug leaked outside brand.ts: {offenders}"


def test_public_assets_do_not_carry_the_slug():
    public = REPO_ROOT / "frontend" / "public"
    for path in public.rglob("*"):
        if path.is_file():
            assert BRAND.slug.lower() not in path.name.lower(), f"asset name: {path.name}"
            assert BRAND.slug.lower() not in path.read_text(encoding="utf-8").lower(), (
                f"asset content: {path.name}"
            )


def test_manifest_filename_is_brand_neutral():
    for name in (BRAND.manifest_filename, BRAND.legacy_manifest_filename):
        assert name.startswith(".")
        assert name.endswith(".json")
        assert BRAND.slug not in name
    assert BRAND.manifest_filename == ".modelmanifest.json"
    assert BRAND.legacy_manifest_filename == ".hugginghack.json"


def _brand_ts():
    text = (FRONTEND_SRC / "brand.ts").read_text(encoding="utf-8")
    # String values may sit on the same line or wrap onto the next line,
    # as the description does.
    strings = dict(
        re.findall(r"^  (\w+):[ \t]*(?:\n[ \t]*)?'([^']*)'", text, re.MULTILINE)
    )
    slug = re.search(r"^const slug = '([^']*)'", text, re.MULTILINE)
    derived = dict(re.findall(r"^  (\w+): `\$\{slug\}([^`]*)`", text, re.MULTILINE))
    return strings, slug, derived


def test_backend_and_frontend_brand_modules_agree():
    strings, slug_match, derived = _brand_ts()
    assert slug_match is not None, "brand.ts must define the slug constant"
    assert slug_match.group(1) == BRAND.slug

    expected = {
        "displayName": BRAND.display_name,
        "description": BRAND.description,
        "manifestFilename": BRAND.manifest_filename,
        "legacyManifestFilename": BRAND.legacy_manifest_filename,
        "markPath": "/brand-mark.svg",
    }
    for key, value in expected.items():
        assert strings.get(key) == value, f"brand.ts {key} disagrees with backend"

    assert derived.get("unauthorizedEvent") == ":unauthorized"


def test_backend_brand_derivations_compose_from_the_slug():
    assert BRAND.session_cookie == f"{BRAND.slug}-session"
    assert BRAND.sqlite_filename == f"{BRAND.slug}.sqlite3"
    assert BRAND.hf_library_name == BRAND.slug
    assert BRAND.part_suffix == f".{BRAND.slug}-part"
