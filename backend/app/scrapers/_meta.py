"""
Shared HTML metadata extraction helpers.

The og:image regex used to be order-dependent — it only matched when the
`property` attribute came before the `content` attribute. Real-world HTML
puts them in either order, and some sites use `name="og:image"` instead
of `property=...`. The helper below finds every <meta> tag in the HTML
and inspects its attributes individually, so attribute-order and
property-vs-name drift don't break us.

Also provides a JSON-LD fallback for sites that declare `image` inside
a `<script type="application/ld+json">` block.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

_META_TAG_RE = re.compile(r"<meta\b[^>]*/?>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"""(\w[\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)
_JSONLD_RE = re.compile(
    r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_COVER_IMG_RE = re.compile(
    r'<img\b[^>]*class="[^"]*(?:seriesCoverBox|cover|poster)[^"]*"[^>]*>',
    re.IGNORECASE,
)
_GENERIC_IMG_SRC_RE = re.compile(
    r'<img\b[^>]*\bsrc="([^"]+(?:jpg|jpeg|png|webp))"', re.IGNORECASE
)


def _parse_attrs(tag_html: str) -> dict[str, str]:
    """Parse HTML tag attributes into a dict. Order-independent, quote-agnostic."""
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag_html):
        name = m.group(1).lower()
        value = m.group(2) or m.group(3) or m.group(4) or ""
        out[name] = value
    return out


def extract_og_image(html: str) -> str | None:
    """
    Find the Open Graph image URL. Handles:
      <meta property="og:image" content="X">
      <meta content="X" property="og:image">
      <meta name="og:image" content="X">
      <meta property='og:image' content='X'>
      <meta property="og:image:url" content="X">  (secondary tag)
    """
    if not html:
        return None
    best: str | None = None
    for m in _META_TAG_RE.finditer(html):
        attrs = _parse_attrs(m.group(0))
        tag_name = (attrs.get("property") or attrs.get("name") or "").lower()
        if tag_name == "og:image":
            content = attrs.get("content")
            if content:
                return content.strip()
        if tag_name == "og:image:url" and not best:
            content = attrs.get("content")
            if content:
                best = content.strip()
    return best


def extract_jsonld_image(html: str) -> str | None:
    """Scan <script type="application/ld+json"> blocks for an `image` field."""
    if not html:
        return None
    for m in _JSONLD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        # The LD-JSON block can be a single object or a list of them
        candidates: Iterable = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            img = obj.get("image")
            if isinstance(img, str):
                return img
            if isinstance(img, dict):
                url = img.get("url") or img.get("@id")
                if isinstance(url, str):
                    return url
            if isinstance(img, list) and img:
                first = img[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    url = first.get("url") or first.get("@id")
                    if isinstance(url, str):
                        return url
    return None


def extract_cover_img(html: str) -> str | None:
    """Fall back to any <img class="…(cover|poster|seriesCoverBox)…">."""
    if not html:
        return None
    m = _COVER_IMG_RE.search(html)
    if not m:
        return None
    tag = m.group(0)
    attrs = _parse_attrs(tag)
    return attrs.get("data-src") or attrs.get("src")


def extract_poster(html: str) -> str | None:
    """
    Multi-strategy poster extraction:
      1. og:image (property or name attribute, either order)
      2. JSON-LD `image` field
      3. <img class="…cover/poster/seriesCoverBox…">
      4. Any <img src="*.jpg"> in the page (last resort)
    """
    return (
        extract_og_image(html)
        or extract_jsonld_image(html)
        or extract_cover_img(html)
        or _first_generic_image(html)
    )


def _first_generic_image(html: str) -> str | None:
    m = _GENERIC_IMG_SRC_RE.search(html or "")
    return m.group(1) if m else None


def absolutize(url: str | None, base: str) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url


_STAFFEL_SUFFIX_RE = re.compile(
    r"\s*[-–—]?\s*[Ss]taffel\s+\d+\s*$", re.IGNORECASE
)


def extract_title(html: str, fallback: str = "") -> str:
    """Extract the page title from <h1 itemprop="name"> or <title>.

    Strips trailing "Staffel X" suffixes that s.to / aniworld sometimes
    include in page titles — they're navigation context, not part of the
    actual show name.
    """
    if not html:
        return fallback
    title: str | None = None
    m = re.search(
        r'<h1[^>]*itemprop="name"[^>]*>([^<]+)</h1>', html, re.IGNORECASE
    )
    if m:
        title = m.group(1).strip()
    if not title:
        m = re.search(r"<title>([^<|]+)", html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
    if not title:
        return fallback
    # Strip "Staffel X" suffix — always metadata, never part of the name.
    title = _STAFFEL_SUFFIX_RE.sub("", title).strip()
    return title or fallback
