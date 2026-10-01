"""Card text leaves the Hub already clean (security audit D8).

`read_model_card` is the single source of card text: it is stored into
`model_card_snapshots`, served to agent keys on the card route, and
handed to MCP. These tests pin the property at that choke point — the
browser renderer still sanitizes at display, but no consumer of the raw
API ever receives executable markup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cardsanitize import clean_model_card
from app.hub_service import HubService

HOSTILE_CARD = """---
license: mit
---

# the hostile card

## Usage

```python
print("code fences survive")
```

Inline math $$x^2$$ and a [normal link](https://huggingface.co) survive too.

<script>alert('payload')</script>
<img src="https://huggingface.co/legit.png" onerror="steal()">
<a href="javascript:alert('xss')">click me</a>
<a href="data:text/html,<script>alert(1)</script>">data link</a>
<iframe src="https://evil.example"></iframe>
<style>body { display: none }</style>
"""


def card_settings(tmp_path: Path) -> object:
    """Only the fields HubService reads; hub_cache_path derives from
    data_dir, which lands in the throwaway test space."""
    import dataclasses

    import app.config as config

    return dataclasses.replace(
        config.settings,
        hf_token=None,
        data_dir=tmp_path,
        model_storage=tmp_path,
    )


@pytest.fixture()
def card_path(tmp_path: Path) -> Path:
    card = tmp_path / "README.md"
    card.write_text(HOSTILE_CARD, encoding="utf-8")
    return card


def test_clean_is_idempotent_and_preserves_markdown():
    once = clean_model_card(HOSTILE_CARD)
    twice = clean_model_card(once)
    assert once == twice
    assert "# the hostile card" in once
    assert "## Usage" in once
    assert 'print("code fences survive")' in once
    assert "[normal link](https://huggingface.co)" in once
    assert "$$x^2$$" in once
    assert "license: mit" in once


def test_clean_strips_executable_markup():
    cleaned = clean_model_card(HOSTILE_CARD)
    lowered = cleaned.lower()
    assert "<script" not in lowered
    assert "alert(" not in lowered, "dropped tags must not leak their payloads as text"
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    assert "data:text/html" not in lowered
    assert "<iframe" not in lowered
    assert "<style" not in lowered
    # The useful link text and image survive as elements.
    assert "click me" in cleaned
    assert "<img" in cleaned and "legit.png" in cleaned


def test_read_model_card_sends_clean_text_downstream(
    card_path: Path, tmp_path: Path, monkeypatch
):
    import app.hub_service as hub_module

    def fake_download(**_kwargs) -> Path:
        return card_path

    monkeypatch.setattr(hub_module, "hf_hub_download", fake_download)
    service = HubService(card_settings(tmp_path))

    card = service.read_model_card("acme/tiny", "main")
    assert card is not None
    lowered = card.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered
    assert "onerror" not in lowered
    assert "# the hostile card" in card


def test_read_model_card_reports_absence_not_failure(tmp_path: Path, monkeypatch):
    import app.hub_service as hub_module

    def failing_download(**_kwargs):
        raise RuntimeError("upstream gone")

    monkeypatch.setattr(hub_module, "hf_hub_download", failing_download)
    service = HubService(card_settings(tmp_path))
    assert service.read_model_card("acme/tiny", "main") is None
