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
# Matches the FIRST <img> inside a container (div/section/a) whose class
# contains seriesCoverBox / cover / poster. s.to and aniworld put the
# class on the container, not the img itself.
_COVER_CONTAINER_RE = re.compile(
    r'<(?:div|section|a|figure)\b[^>]*class="[^"]*(?:seriesCoverBox|cover|poster)[^"]*"[^>]*>'
    r'(?P<inner>.*?)</(?:div|section|a|figure)>',
    re.IGNORECASE | re.DOTALL,
)
_INNER_IMG_RE = re.compile(
    r'<img\b[^>]*>',
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
    """Find a show-specific cover image.

    Two strategies, in order:
      1. `<img class="…seriesCoverBox/cover/poster…">` — img carries the class
      2. `<div class="seriesCoverBox">...<img>...</div>` — container carries
         the class, img is inside (this is how s.to + aniworld mark up cover
         boxes; without this the show poster is unreachable via CSS class).
    """
    if not html:
        return None
    m = _COVER_IMG_RE.search(html)
    if m:
        tag = m.group(0)
        attrs = _parse_attrs(tag)
        src = attrs.get("data-src") or attrs.get("src")
        if src:
            return src
    for m in _COVER_CONTAINER_RE.finditer(html):
        inner = m.group("inner")
        img_m = _INNER_IMG_RE.search(inner)
        if not img_m:
            continue
        attrs = _parse_attrs(img_m.group(0))
        src = attrs.get("data-src") or attrs.get("src")
        if src:
            return src
    return None


def extract_poster(html: str) -> str | None:
    """
    Multi-strategy poster extraction.

    Show-specific cover images come FIRST, og:image only as a fallback. On
    s.to and aniworld the og:image meta tag often points at the generic
    site banner / logo rather than the actual show poster, so relying on
    it first would return the wrong image. The show-specific
    `<... class="seriesCoverBox">` container (or a directly-classed
    `<img class="poster">`) is always the real cover when present.

    Order:
      1. Show-specific cover img (class="seriesCoverBox|cover|poster" on
         the img itself OR on a wrapping div/section/a/figure)
      2. og:image (property or name attribute, either order)
      3. JSON-LD `image` field
      4. Any <img src="*.jpg"> in the page (last resort)
    """
    return (
        extract_cover_img(html)
        or extract_og_image(html)
        or extract_jsonld_image(html)
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
# aniworld frequently phrases the h1 as "Staffel 1 von <Show>" when the
# landing page defaults to a specific season — strip the prefix so we
# get just the show name.
_STAFFEL_PREFIX_RE = re.compile(
    r"^\s*(?:[Ss]taffel|[Ss]eason)\s+\d+\s+(?:von|of|aus)\s+",
    re.IGNORECASE,
)
# Same pattern for episode-level titles: "Episode 5 von <Show>" etc.
_EPISODE_PREFIX_RE = re.compile(
    r"^\s*(?:[Ee]pisode|[Ff]olge)\s+\d+\s+(?:von|of|aus)\s+",
    re.IGNORECASE,
)


def extract_title(html: str, fallback: str = "") -> str:
    """Extract the page title from <h1 itemprop="name"> or <title>.

    Strips common navigation-context prefixes/suffixes that s.to and
    aniworld wrap around the actual show name:
      - trailing "… Staffel X"
      - leading  "Staffel X von …"  /  "Season X of …"
      - leading  "Episode X von …" /  "Folge X von …"
    """
    if not html:
        return fallback
    title: str | None = None
    # Prefer the itemprop h1 (structured markup)
    m = re.search(
        r'<h1[^>]*itemprop="name"[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL
    )
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    # Fall back to any h1 that is not "navigation"
    if not title:
        for m in re.finditer(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL):
            t = m.group(1).strip()
            t_clean = re.sub(r'<[^>]+>', '', t).strip()
            if t_clean and t_clean.lower() != "navigation":
                title = t_clean
                break
    if not title:
        m = re.search(r"<title>([^<|]+)", html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
    if not title:
        return fallback
    import html
    title = html.unescape(title)
    # Strip nav-context prefixes/suffixes — always metadata, never part of
    # the actual name.
    title = _STAFFEL_SUFFIX_RE.sub("", title)
    title = _STAFFEL_PREFIX_RE.sub("", title)
    title = _EPISODE_PREFIX_RE.sub("", title)
    return title.strip() or fallback


_QUERY_PAREN_RE = re.compile(r"\s*[\(\[\{][^)\]}]*[\)\]}]")
_QUERY_YEAR_RE = re.compile(r"\s*\b(?:19|20)\d{2}\b")


def clean_search_query(query: str) -> str:
    """Loosen a title query so a picky site search index still matches it.

    Users often type extra tokens the site doesn't index — a release year or
    bracketed qualifiers ("Dune (2021)", "Tatort [HD]"). We strip those so a
    retry can find the title. Returns the cleaned query; may equal the input
    (callers should only retry when it actually changed).
    """
    cleaned = _QUERY_PAREN_RE.sub("", query)
    cleaned = _QUERY_YEAR_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:·|")
    return cleaned.strip()
