"""
Captcha / Cloudflare challenge detection.

**IMPORTANT**: We only flag *definitive* challenge markers — things that
can only appear on an actual challenge page. Preloaded Turnstile scripts,
hCaptcha assets and similar show up in the HTML of normal s.to / aniworld
pages as well (the inline player modal loads them lazily), so matching
on those would produce a mountain of false positives.

The checker is intentionally opt-in now: `services/http.py::get()` does
NOT auto-check. Scrapers call `raise_if_captcha()` themselves after they
have exhausted their parsing attempts.
"""
from __future__ import annotations


class CaptchaRequiredError(RuntimeError):
    """Raised when a Cloudflare challenge / CAPTCHA page is returned."""


# Only the unambiguous markers — things that appear ONLY on challenge pages.
# We intentionally do NOT check for "cf-turnstile", "challenges.cloudflare.com",
# "hcaptcha.com", "g-recaptcha", etc. because those scripts are routinely
# preloaded in legitimate s.to / aniworld pages for the inline player modal.
_DEFINITIVE_MARKERS = (
    "<title>just a moment",              # CF interstitial
    "<title>attention required",         # CF block page
    "<title>one more step",              # CF variant
    "<title>please wait...",             # CF variant
    "<title>stream wird vorbereitet",    # legacy aniworld
    "window._cf_chl_opt",                # CF challenge options script
    "cf-challenge-running",              # CF running marker
    "enable javascript and cookies to continue",  # CF fallback text
    "player-prepare-turnstile",          # s.to inline modal trigger
)


def is_captcha_page(html: str, status_code: int = 200) -> bool:
    """
    Return True only if the response is DEFINITELY a challenge page.

    False positives are much worse than false negatives here: a false
    positive aborts a valid download, a false negative just means the
    scraper will fail at a later step with a "no hosters found" error
    that the user can actually recover from.
    """
    if status_code in (403, 503):
        return True
    if not html:
        return status_code >= 400
    lower = html.lower()
    return any(m in lower for m in _DEFINITIVE_MARKERS)


def raise_if_captcha(html: str, status_code: int, url: str) -> None:
    if is_captcha_page(html, status_code):
        raise CaptchaRequiredError(
            f"Cloudflare / captcha challenge for {url} — "
            "curl_cffi TLS fingerprint was insufficient this time. "
            "Please retry later or configure a proxy."
        )
