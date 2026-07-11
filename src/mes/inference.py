"""Name → Domain inference via DuckDuckGo (Phase 1-C).

The search source is a REPLACEABLE part (P6 Provider Agnostic). This v1 uses the
no-JS HTML endpoint ``https://html.duckduckgo.com/html/`` (verified working
2026-07-11; the ``duckduckgo.com/html/`` host is blocked/unsupported). If it gets
blocked or flaky, report it — do NOT silently fall back to another source.

Trustworthy-domain rule (v1, explainable & evolvable — this is what the
``inferred_domain`` meta-feature will let us evaluate later):
  1. Query ``"<store name> shopify store"``.
  2. Non-200 / timeout / network error / no parseable results container
     -> ``fetch_failed`` (we don't know the answer).
  3. Walk the result links in order; take the FIRST whose registrable domain is
     not in the platform/aggregator blacklist -> ``observed`` (candidate domain).
  4. Results returned but none survive the blacklist -> ``not_found`` ("with this
     provider we could not find a trustworthy domain", NOT "the store is dead").

Status maps 1:1 to the Observation status three-values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from mes.normalize import normalize_domain

# 'duckduckgo_v1' is the producer tag (the method that makes the inference) that goes
# in the observation's producer column. DuckDuckGo-as-external-source is a P6 provider;
# here we care about who produced the value, hence "producer".
PRODUCER = "duckduckgo_v1"
_ENDPOINT = "https://html.duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')

# Non-target platforms / marketplaces / aggregators. A result on one of these is
# not the store's own site. Matched against the registrable-ish domain suffix.
_BLACKLIST = frozenset(
    {
        "shopify.com",
        "myshopify.com",
        "apps.shopify.com",
        "pinterest.com",
        "etsy.com",
        "amazon.com",
        "ebay.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "reddit.com",
        "wikipedia.org",
        "trustpilot.com",
        "yelp.com",
        "google.com",
        "apple.com",
        "shopifyspy.com",
        "storeleads.app",
        "builtwith.com",
        "similarweb.com",
        "g2.com",
        "capterra.com",
    }
)


@dataclass(frozen=True)
class InferenceResult:
    status: str  # 'observed' | 'fetch_failed' | 'not_found'
    domain: str | None = None
    raw_url: str | None = None
    producer: str = PRODUCER


def _is_blacklisted(domain: str) -> bool:
    return any(domain == b or domain.endswith("." + b) for b in _BLACKLIST)


def _clean_href(href: str) -> str:
    """DDG sometimes wraps links as //duckduckgo.com/l/?uddg=<encoded>. Unwrap it."""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


def infer_domain(store_name: str, *, timeout: float = 25.0) -> InferenceResult:
    """Infer a store's domain from its name. Never raises for network issues."""
    query = f"{store_name} shopify store"
    try:
        resp = httpx.post(
            _ENDPOINT, data={"q": query}, headers={"User-Agent": _USER_AGENT},
            timeout=timeout, follow_redirects=True,
        )
    except httpx.HTTPError:
        return InferenceResult(status="fetch_failed")

    if resp.status_code != 200:
        return InferenceResult(status="fetch_failed")

    hrefs = _RESULT_RE.findall(resp.text)
    if not hrefs:
        # No parseable results container — could be a layout change or a block page.
        # Treat as fetch_failed: we could not actually read a result set.
        return InferenceResult(status="fetch_failed")

    for href in hrefs:
        url = _clean_href(href)
        domain = normalize_domain(url)
        if domain and not _is_blacklisted(domain):
            return InferenceResult(status="observed", domain=domain, raw_url=url)

    # Results existed but all were blacklisted platforms/aggregators.
    return InferenceResult(status="not_found")
