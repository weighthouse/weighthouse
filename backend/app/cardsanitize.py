"""Server-side sanitation for untrusted model-card markdown.

The frontend renders cards through a strict allowlist pipeline; this
module gives every other consumer of the text (agent API keys, MCP
tools, future surfaces) the same guarantee at the source. The Hub
README arrives as attacker-influenced content, so `read_model_card`
cleans it once, here, before it is stored or served.

Markdown source survives untouched: headings, links, fenced code, and
math are plain text at the HTML level, so the only thing this removes
is executable or injection-grade markup — exactly the surface a card
cannot legitimately use.
"""

import nh3

# Mirrors the frontend's render schema (frontend/src/modelCard.ts):
# link protocols [http, https, irc, ircs, mailto, xmpp] and an
# attribute set with no event handlers, no styles, no srcset.
_URL_SCHEMES = {"http", "https", "mailto", "irc", "ircs", "xmpp"}

_TAGS = set(nh3.ALLOWED_TAGS)  # GitHub-flavored defaults; script, style,
# iframe, object, embed, form, and friends are not members.

_ATTRIBUTES = dict(nh3.ALLOWED_ATTRIBUTES)
# The card renderer resolves links through the card's own revision, so
# href plus title is all a link needs; images keep the geometry and
# description attributes the frontend allowlist already accepts.
_ATTRIBUTES["a"] = {"href", "title"}
_ATTRIBUTES["img"] = {"src", "longdesc", "align", "height", "width", "alt"}

_cleaner = nh3.Cleaner(tags=_TAGS, attributes=_ATTRIBUTES, url_schemes=_URL_SCHEMES)

def clean_model_card(text: str) -> str:
    """Remove HTML-level attack surface from a model card.

    The input is markdown-as-HTML: allowed tags pass, disallowed ones
    are dropped (their text content kept), and URL-bearing attributes
    keep only allowlisted schemes. Cleaning an already-clean card is a
    stable no-op, so stored snapshots survive re-cleaning on serve.
    """
    return _cleaner.clean(text)
