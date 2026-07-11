"""Canonical-key normalization — the single place these rules live (CLAUDE 規則 3).

- ``normalize_domain``    : Entity Model v1 §4 store domain rules.
- ``normalize_seed_name`` : Store Name → seed natural key (Phase 1-C).
"""

from __future__ import annotations

import re

_SCHEME_RE = re.compile(r"^[a-z]+://")
_PORT_RE = re.compile(r":\d+$")


def normalize_domain(raw: str) -> str:
    """Entity Model v1 §4: lowercase / strip scheme / strip path / strip www / strip port.

    e.g. ``https://WWW.WillowBloom.com/products/`` -> ``willowbloom.com``.
    """
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)  # strip scheme
    value = value.split("/", 1)[0]  # keep host only (drop trailing slash + path)
    value = _PORT_RE.sub("", value)  # strip port
    if value.startswith("www."):
        value = value[4:]  # strip www.
    return value


def normalize_seed_name(raw: str) -> str:
    """Store Name -> seed natural key: lowercase, strip punctuation, spaces -> underscore.

    Collapses runs of whitespace; drops characters other than [a-z0-9 _]. Used to
    build ``canonical_key = 'seed:' + normalize_seed_name(name)`` for dedupe.
    """
    value = raw.strip().lower()
    value = re.sub(r"[^a-z0-9\s_]", "", value)  # drop punctuation/symbols
    value = re.sub(r"\s+", "_", value.strip())  # whitespace runs -> single underscore
    value = re.sub(r"_+", "_", value).strip("_")  # collapse underscores
    return value


def seed_key(raw_store_name: str) -> str:
    """Full seed canonical_key for a Store Name."""
    return "seed:" + normalize_seed_name(raw_store_name)
