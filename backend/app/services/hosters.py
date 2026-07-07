"""
Hoster resolvers.

Ports the extractor logic from phoenixthrush/AniWorld-Downloader (MIT):
  - VOE: 5-step deobfuscation (ROT13 -> junk replace -> base64 -> shift -> b64 reversed -> JSON)
  - Vidmoly: regex on file: '…m3u8…' in <script>
  - Vidoza: regex on src: "…" when page contains sourcesCode:
  - Doodstream: pass_md5 URL + token + random10 + unix expiry

Each resolver takes an embed URL and returns a direct playable URL
(HLS m3u8 or MP4). Failures are caught by the scraper fallback loop.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import random
import re
import string
import time
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

from app.services.http import get_client

log = logging.getLogger(__name__)


# ---- Provider-specific headers (ported from config.py PROVIDER_HEADERS_D) --

PROVIDER_HEADERS: dict[str, dict[str, str]] = {
    "voe": {
        "Accept": "*/*",
        # German-first: some hosters content-negotiate the default audio
        # track off Accept-Language. A German downloader should never ask
        # for en-US here.
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://voe.sx/",
        "Origin": "https://voe.sx",
    },
    "vidmoly": {"Referer": "https://vidmoly.biz"},
    "vidoza": {},
    "doodstream": {"Referer": "https://dood.li/"},
    "filemoon": {"Referer": "https://filemoon.to"},
    "vidara": {
        "Referer": "https://vidaraa.cc/",
        "Origin": "https://vidaraa.cc",
    },
    "vidsonic": {
        "Referer": "https://vidsonic.net/",
        "Origin": "https://vidsonic.net",
    },
}


def headers_for(hoster: str) -> dict[str, str]:
    key = (hoster or "").lower()
    for k, h in PROVIDER_HEADERS.items():
        if k in key:
            return h
    return {}


# ---- VOE --------------------------------------------------------------------

_VOE_JUNK = ("@$", "^^", "~@", "%?", "*~", "!!", "#&")
_VOE_SCRIPT_RE = re.compile(
    r'<script\s+type=["\']application/json["\']>(.*?)</script>', re.DOTALL
)
_VOE_REDIRECT_RE = re.compile(r"https?://[^'\"<>]+")


def _rot13(s: str) -> str:
    out: list[str] = []
    for c in s:
        o = ord(c)
        if 65 <= o <= 90:
            o = (o - 65 + 13) % 26 + 65
        elif 97 <= o <= 122:
            o = (o - 97 + 13) % 26 + 97
        out.append(chr(o))
    return "".join(out)


def _voe_decode(encoded: str) -> dict:
    """Decode the VOE payload into the {source, ...} JSON object."""
    try:
        step1 = _rot13(encoded)
        step2 = step1
        for part in _VOE_JUNK:
            step2 = step2.replace(part, "_")
        step2 = step2.replace("_", "")
        step3 = base64.b64decode(step2).decode("utf-8", "ignore")
        step4 = "".join(chr(ord(c) - 3) for c in step3)
        step5 = base64.b64decode(step4[::-1]).decode("utf-8", "ignore")
        return json.loads(step5)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"VOE decode failed: {e}") from e


def _voe_extract_source_from_html(html: str) -> str | None:
    for script in _VOE_SCRIPT_RE.findall(html):
        encoded = script.strip()
        if encoded.startswith('"') and encoded.endswith('"'):
            encoded = encoded[1:-1]
        try:
            encoded = encoded.encode().decode("unicode_escape")
        except Exception:
            pass
        try:
            decoded = _voe_decode(encoded)
        except ValueError:
            continue
        src = decoded.get("source")
        if src:
            return src
    return None


async def resolve_voe(embed_url: str) -> str | None:
    c = await get_client()
    headers = headers_for("voe")
    r = await c.get(embed_url, headers=headers, allow_redirects=True)
    r.raise_for_status()
    html = r.text

    src = _voe_extract_source_from_html(html)
    if src:
        return src

    # Fallback: follow the first http URL embedded in the page
    m = _VOE_REDIRECT_RE.search(html)
    if m:
        r2 = await c.get(m.group(0), headers=headers, allow_redirects=True)
        r2.raise_for_status()
        html2 = r2.text
        src = _voe_extract_source_from_html(html2)
        if src:
            return src
        m2 = re.search(r"https?://\S+?\.m3u8\S*", html2)
        if m2:
            return m2.group(0)

    # Last-resort: look for any m3u8 on the original page
    m3 = re.search(r"https?://\S+?\.m3u8\S*", html)
    return m3.group(0) if m3 else None


# ---- Vidmoly ---------------------------------------------------------------

_VIDMOLY_FILE_RE = re.compile(r'file\s*:\s*[\'"]([^\'"]+?\.m3u8[^\'"]*)[\'"]')


async def resolve_vidmoly(embed_url: str) -> str | None:
    c = await get_client()
    r = await c.get(embed_url, headers=headers_for("vidmoly"))
    r.raise_for_status()
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", r.text, re.DOTALL | re.IGNORECASE)
    blob = "\n".join(s for s in scripts if s)
    m = _VIDMOLY_FILE_RE.search(blob)
    return m.group(1) if m else None


# ---- Vidoza ----------------------------------------------------------------

_VIDOZA_SRC_RE = re.compile(r'src:\s*"([^"]+)"')


async def resolve_vidoza(embed_url: str) -> str | None:
    c = await get_client()
    r = await c.get(embed_url, headers=headers_for("vidoza"))
    r.raise_for_status()
    if "sourcesCode:" in r.text:
        m = _VIDOZA_SRC_RE.search(r.text)
        if m:
            return m.group(1)
    # Fallback: <source src="…">
    m2 = re.search(r'<source[^>]+src="([^"]+)"', r.text)
    return m2.group(1) if m2 else None


# ---- Doodstream ------------------------------------------------------------

_DOOD_BASE = "https://dood.li"
_DOOD_PASS_MD5_RE = re.compile(r"\$\.get\('([^']*\/pass_md5\/[^']*)'")
_DOOD_TOKEN_RE = re.compile(r"token=([a-zA-Z0-9]+)")


def _rand_string(n: int = 10) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(random.choices(alpha, k=n))


async def resolve_doodstream(embed_url: str) -> str | None:
    c = await get_client()
    headers = {**headers_for("doodstream"), "Referer": embed_url}
    r = await c.get(embed_url, headers=headers)
    r.raise_for_status()
    html = r.text

    pass_m = _DOOD_PASS_MD5_RE.search(html)
    token_m = _DOOD_TOKEN_RE.search(html)
    if not pass_m or not token_m:
        return None

    pass_url = pass_m.group(1)
    if not pass_url.startswith("http"):
        pass_url = urljoin(_DOOD_BASE, pass_url)

    r2 = await c.get(pass_url, headers=headers)
    r2.raise_for_status()
    base = (r2.text or "").strip()
    if not base:
        return None

    return f"{base}{_rand_string(10)}?token={token_m.group(1)}&expiry={int(time.time())}"


# ---- VIDARA -----------------------------------------------------------------


async def resolve_vidara(embed_url: str) -> str | None:
    parsed = urlparse(embed_url)
    domain = parsed.netloc
    filecode = parsed.path.strip("/").split("/")[-1]

    c = await get_client()
    url = f"https://{domain}/api/stream"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Referer": embed_url,
        "Origin": f"https://{domain}",
    }
    payload = {
        "filecode": filecode,
        "device": "web"
    }

    try:
        r = await c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("streaming_url")
    except Exception as e:
        log.warning("vidara: API call failed: %s", e)
        return None


# ---- Vidsonic --------------------------------------------------------------


async def resolve_vidsonic(embed_url: str) -> str | None:
    c = await get_client()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": embed_url,
    }
    try:
        r = await c.get(embed_url, headers=headers)
        r.raise_for_status()
        html = r.text

        m = re.search(r'const\s+_0x1\s*=\s*[\'"]([^\'"]+)[\'"]', html)
        if not m:
            log.warning("vidsonic: could not find _0x1 variable in HTML")
            return None

        hex_str = m.group(1)
        clean = hex_str.replace("|", "")
        decoded = bytes.fromhex(clean).decode("utf-8", errors="ignore")
        return decoded[::-1]
    except Exception as e:
        log.warning("vidsonic: failed to resolve stream: %s", e)
        return None


# ---- registry --------------------------------------------------------------

Resolver = Callable[[str], Awaitable[str | None]]

RESOLVERS: dict[str, Resolver] = {
    "voe": resolve_voe,
    "vidmoly": resolve_vidmoly,
    "vidoza": resolve_vidoza,
    "doodstream": resolve_doodstream,
    "dood": resolve_doodstream,
    "vidara": resolve_vidara,
    "vidsonic": resolve_vidsonic,
}


async def resolve_direct_url(hoster_name: str, embed_url: str) -> str | None:
    key = (hoster_name or "").lower().strip()
    for needle, fn in RESOLVERS.items():
        if needle in key:
            try:
                return await fn(embed_url)
            except Exception as e:
                log.warning("hoster %s resolver failed: %s", hoster_name, e)
                return None
    log.info(
        "no dedicated resolver for hoster=%s — yt-dlp will try the embed directly",
        hoster_name,
    )
    return embed_url
