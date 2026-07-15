"""Shopify App Store review-page scraper — Store Names from five review apps.

Real-world, version-sensitive. The selector below was verified against the live
apps.shopify.com HTML on 2026-07-11 (loox) and 2026-07-15 (the other four): each
review sits in a ``data-merchant-review`` block; the merchant Store Name is the
``title`` attribute of a ``<span>`` inside a ``tw-text-heading-xs tw-text-fg-primary``
div (the title holds the full name even when visually truncated). ~10 reviews/page.
The selector is the SAME App Store HTML across all apps (it's the store's chrome,
not the app's). Harvesting all five widens the seed supply (loox alone drained by
day 2 — see findings).

If the page structure changes, ``parse_store_names`` returns fewer/zero names —
report that (it is itself signal), do not paper over it with a guessed selector.

Compliance (apps.shopify.com robots.txt, checked 2026-07-11): the ``*/reviews``
path is allowed for User-agent ``*`` (only /internal/, /services/, ``*q=*`` and
shpxid/auth params are disallowed). We additionally self-throttle 5–25s between
requests.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_BASE = "https://apps.shopify.com"

# Review-app name -> Shopify App Store URL handle (handles verified 2026-07-15; they
# are NOT all the same as the app's canonical name — e.g. stamped's slug is historical).
REVIEW_APP_HANDLES = {
    "loox": "loox",
    "judgeme": "judgeme",
    "yotpo": "yotpo-social-reviews",
    "okendo": "okendo-reviews",
    "stamped": "product-reviews-addon",
}

# Verified selector (2026-07-11). Kept as one place so a structure change is a
# single edit + a doc/version bump.
_STORE_NAME_RE = re.compile(
    r'tw-text-heading-xs tw-text-fg-primary[^>]*>\s*<span[^>]*title="([^"]+)"'
)

MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 25


@dataclass(frozen=True)
class ScrapedStore:
    store_name: str
    source_url: str


def fetch_review_page(app_handle: str, page: int = 1, *, timeout: float = 30.0) -> str:
    """GET one review page's HTML. Raises httpx.HTTPError on network/HTTP failure."""
    url = f"{_BASE}/{app_handle}/reviews"
    params = {"page": page} if page > 1 else None
    resp = httpx.get(
        url, params=params, headers={"User-Agent": _USER_AGENT},
        timeout=timeout, follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


def parse_store_names(html: str) -> list[str]:
    """Extract Store Names from a review page. Empty list = structure changed / no reviews."""
    return _STORE_NAME_RE.findall(html)


def scrape_store_names(
    app_handle: str = "loox",
    *,
    pages: int = 1,
    min_delay: float = MIN_DELAY_SECONDS,
    max_delay: float = MAX_DELAY_SECONDS,
    sleep: bool = True,
) -> Iterator[ScrapedStore]:
    """Yield ScrapedStore for each review across ``pages``, throttling 5–25s between pages."""
    for page in range(1, pages + 1):
        if sleep and page > 1:
            time.sleep(random.uniform(min_delay, max_delay))
        html = fetch_review_page(app_handle, page)
        url = f"{_BASE}/{app_handle}/reviews" + (f"?page={page}" if page > 1 else "")
        for name in parse_store_names(html):
            yield ScrapedStore(store_name=name, source_url=url)
