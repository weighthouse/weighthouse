"""Guardrails for the brand modules.

The slug appears only in ``backend/app/brand.py`` and
``frontend/src/brand.ts``, and the two modules agree (a consistency
test). The manifest filename contains neither the slug nor the
upstream name, and public asset files carry neither.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.brand import BRAND
from app.library_files import (
    LEGACY_MANIFEST_FILENAME,
    MANIFEST_FILENAME,
    PART_SUFFIX,
)

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
    allowed = {
        BACKEND_ROOT / "app" / "brand.py",
        # Carries the partial-file suffix, whose name follows the product.
        BACKEND_ROOT / "app" / "library_files.py",
        # This guard names the slug to assert on it.
        Path(__file__).resolve(),
    }
    offenders = [
        path
        for path in backend_python_sources()
        if path not in allowed
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
    for name in (MANIFEST_FILENAME, LEGACY_MANIFEST_FILENAME):
        assert name.startswith(".")
        assert name.endswith(".json")
        assert BRAND.slug not in name
    assert MANIFEST_FILENAME == ".modelmanifest.json"
    assert LEGACY_MANIFEST_FILENAME == ".hugginghack.json"


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
        "markPath": "/brand-mark.svg",
    }
    for key, value in expected.items():
        assert strings.get(key) == value, f"brand.ts {key} disagrees with backend"

    assert derived.get("unauthorizedEvent") == ":unauthorized"


def test_backend_brand_derivations_compose_from_the_slug():
    assert BRAND.session_cookie == f"{BRAND.slug}-session"
    assert BRAND.hf_library_name == BRAND.slug
    assert PART_SUFFIX == ".weighthouse-part"


def test_library_files_do_not_carry_the_slug():
    import app.library_files as library_files

    # The canonical names are brand-neutral, by design.
    assert BRAND.slug not in library_files.LIBRARY_DB_FILENAME
    assert BRAND.slug not in library_files.MANIFEST_FILENAME
    assert BRAND.slug not in library_files.LEGACY_MANIFEST_FILENAME
    assert library_files.LEGACY_DB_FILENAME == "modelharbor.sqlite3"
    # Only the partial-file suffix carries the product slug; everything
    # the slug would have pointed at before this version is legacy.
    assert library_files.PART_SUFFIX == f".{BRAND.slug}-part"
    for name in library_files.LEGACY_PART_SUFFIXES:
        assert BRAND.slug not in name
