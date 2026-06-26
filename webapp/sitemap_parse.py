"""SSRF- and XXE-safe sitemap parsing — replaces ``advertools.sitemap_to_df``.

``advertools`` fetches and parses sitemap XML with no control over the parser or
the response size, so a hostile sitemap can attempt XXE / entity-expansion
("billion laughs") or simply blow up memory. This module:

* fetches through :func:`webapp.ssrf.safe_get` (scheme + private-IP guarded,
  redirects re-validated),
* caps the bytes read per document,
* parses with :mod:`defusedxml` (external entities and DTD bombs are rejected),
* follows nested ``<sitemapindex>`` documents with a bounded fan-out.

Returns the same ``loc`` / ``lastmod`` DataFrame shape the callers expect.
"""

from __future__ import annotations

import pandas as pd
from defusedxml import ElementTree as ET

from webapp.ssrf import safe_get

MAX_SITEMAP_URLS = 50_000      # total URLs returned
MAX_SITEMAPS = 50             # documents fetched (index fan-out bound)
MAX_BYTES = 20 * 1024 * 1024  # 20 MB per document
HEADERS = {"User-Agent": "CrawlBudgetAnalyzer/1.0 (research tool; contact@example.com)"}
TIMEOUT = 15


def _local(tag: str) -> str:
    """Local tag name, namespace-stripped and lowercased."""
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap_xml(content: bytes):
    """Parse one sitemap document.

    Returns ``(urls, child_sitemaps)`` where ``urls`` is a list of
    ``{"loc", "lastmod"}`` dicts and ``child_sitemaps`` is a list of nested
    sitemap URLs (for a ``<sitemapindex>``). Raises on malformed or hostile XML
    (defusedxml rejects DTDs / external entities).
    """
    root = ET.fromstring(content)
    urls: list[dict] = []
    children: list[str] = []
    for node in root:
        ntag = _local(node.tag)
        if ntag == "sitemap":
            loc = next((c.text for c in node if _local(c.tag) == "loc" and c.text), None)
            if loc:
                children.append(loc.strip())
        elif ntag == "url":
            loc = lastmod = None
            for c in node:
                if _local(c.tag) == "loc" and c.text:
                    loc = c.text.strip()
                elif _local(c.tag) == "lastmod" and c.text:
                    lastmod = c.text.strip()
            if loc:
                urls.append({"loc": loc, "lastmod": lastmod})
    return urls, children


def safe_sitemap_to_df(url: str, *, max_urls: int = MAX_SITEMAP_URLS,
                       max_sitemaps: int = MAX_SITEMAPS) -> pd.DataFrame:
    """Fetch a sitemap (or sitemap index) and flatten it to a DataFrame.

    SSRF-safe (``safe_get``), XXE-safe (defusedxml), and bounded in both the
    number of documents fetched and the rows returned. Returns an empty
    ``DataFrame[loc, lastmod]`` on any failure (callers treat that as "no
    sitemap"), matching the previous advertools behaviour.
    """
    rows: list[dict] = []
    queue = [url]
    fetched = 0
    while queue and len(rows) < max_urls and fetched < max_sitemaps:
        sm_url = queue.pop(0)
        fetched += 1
        try:
            resp = safe_get(sm_url, headers=HEADERS, timeout=TIMEOUT)
            urls, children = parse_sitemap_xml(resp.content[:MAX_BYTES])
        except Exception:
            continue
        rows.extend(urls)
        queue.extend(children)
    return pd.DataFrame(rows[:max_urls], columns=["loc", "lastmod"])
