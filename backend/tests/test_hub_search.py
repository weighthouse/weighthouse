"""Hub search facts: the glance line's data is merged from two list
variants — the full call (everything except counts) and the expand call
(counts only). One extra request per page, never one per repo, and a
repo the Hub does not count stays unknown."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.hub_service import HubService, _parameter_count


def _info(repo_id: str, siblings=(), safetensors=None):
    return SimpleNamespace(
        id=repo_id,
        author=repo_id.split("/")[0] if "/" in repo_id else None,
        pipeline_tag=None,
        library_name=None,
        tags=[],
        downloads=0,
        downloads_all_time=0,
        likes=0,
        trending_score=0.0,
        last_modified=None,
        created_at=None,
        private=False,
        gated=False,
        sha=None,
        siblings=[SimpleNamespace(rfilename=name) for name in siblings],
        safetensors=safetensors,
    )


def test_serialize_counts_gguf_files_as_quantizations():
    item = HubService.serialize_model(
        _info("acme/big", siblings=["a.gguf", "quants/A-00001.gguf", "weights.bin", "README.md"])
    )
    assert item["quant_count"] == 2

    none = HubService.serialize_model(_info("acme/plain", siblings=["model.safetensors"]))
    assert none["quant_count"] is None


@pytest.mark.parametrize(
    ("safetensors", "expected"),
    [
        # authoritative total wins; a stray "total" inside the buckets
        # would double-count, so it is excluded from the fallback sum
        ({"total": 100, "parameters": {"BF16": 60, "F16": 40, "total": 100}}, 100),
        ({"parameters": {"BF16": 60, "F16": 40}}, 100),
        ({"parameters": {}}, None),
        (None, None),
    ],
)
def test_parameter_count_reads_the_shape_it_is_given(safetensors, expected):
    assert _parameter_count(_info("acme/m", safetensors=safetensors)) == expected


def test_parameter_count_reads_the_object_shape():
    st = SimpleNamespace(total=7_615_616_512, parameters={"BF16": 7_615_616_512})
    assert _parameter_count(_info("acme/m", safetensors=st)) == 7_615_616_512
    no_total = SimpleNamespace(total=None, parameters={"I32": 6_000_000_000, "F16": 1_000_000_000})
    assert _parameter_count(_info("acme/m", safetensors=no_total)) == 7_000_000_000


@pytest.fixture()
def service(tmp_path: Path):
    settings = Settings(
        model_storage=(tmp_path / "models").resolve(),
        data_dir=(tmp_path / "data").resolve(),
    )
    svc = HubService(settings)
    svc.range_client.close()
    return svc


def test_search_merges_counts_from_the_expand_call(service, monkeypatch):
    full = [
        _info("acme/gguf-repo", siblings=["q4.gguf", "q8.gguf"]),
        _info("acme/counted", siblings=["model.safetensors"]),
        _info("acme/unknown", siblings=["model.safetensors"]),
    ]
    expanded = [
        _info("acme/counted", safetensors={"total": 1_234, "parameters": {"BF16": 1_234}}),
        # "acme/unknown" is simply not counted by the Hub
    ]

    def fake_list_models(**kwargs):
        if kwargs.get("full"):
            return iter(full)
        assert kwargs.get("expand") == ["safetensors"]
        return iter(expanded)

    monkeypatch.setattr(service.api, "list_models", fake_list_models)
    items = service.search_models(search="acme", limit=30)

    by_id = {item["id"]: item for item in items}
    assert by_id["acme/gguf-repo"]["quant_count"] == 2
    assert by_id["acme/counted"]["parameter_count"] == 1_234
    assert by_id["acme/unknown"]["parameter_count"] is None  # unknown, not guessed


def test_search_still_returns_items_when_the_expand_call_fails(service, monkeypatch):
    def fake_list_models(**kwargs):
        if kwargs.get("full"):
            return iter([_info("acme/only")])
        raise RuntimeError("hub unavailable")

    monkeypatch.setattr(service.api, "list_models", fake_list_models)
    items = service.search_models()
    assert [item["id"] for item in items] == ["acme/only"]
    assert items[0]["parameter_count"] is None
