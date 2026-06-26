"""Public-data fetchers — no server access required.

All inputs are derived from publicly accessible sources:
sitemap, robots.txt, a shallow site spider, and the Common Crawl index.
"""

import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from webapp.sitemap_parse import safe_sitemap_to_df
from webapp.ssrf import safe_get

HEADERS = {"User-Agent": "CrawlBudgetAnalyzer/1.0 (research tool; contact@example.com)"}
TIMEOUT = 15


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

def fetch_sitemap(site_url: str) -> pd.DataFrame:
    """Discover and fetch the sitemap, returning a flat DataFrame of URLs."""
    base = site_url.rstrip("/")
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/news-sitemap.xml",
    ]
    # Also check robots.txt for Sitemap: directives
    try:
        r = safe_get(f"{base}/robots.txt", headers=HEADERS, timeout=TIMEOUT)
        for line in r.text.splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.insert(0, line.split(":", 1)[1].strip())
    except Exception:
        pass

    for url in candidates:
        df = safe_sitemap_to_df(url)  # SSRF + XXE safe, byte/row capped
        if not df.empty:
            return df
    return pd.DataFrame()


def sitemap_section_summary(sitemap_df: pd.DataFrame) -> pd.DataFrame:
    """Count URLs per top-level section and check lastmod freshness."""
    if sitemap_df.empty or "loc" not in sitemap_df.columns:
        return pd.DataFrame()

    def section(url):
        parts = urlparse(url).path.strip("/").split("/")
        return parts[0] if parts and parts[0] else "(root)"

    df = sitemap_df.copy()
    df["section"] = df["loc"].apply(section)
    df["depth"] = df["loc"].apply(
        lambda u: len([p for p in urlparse(u).path.strip("/").split("/") if p])
    )
    has_lastmod = "lastmod" in df.columns

    agg = df.groupby("section").agg(
        url_count=("loc", "size"),
        avg_depth=("depth", "mean"),
    )
    if has_lastmod:
        df["lastmod"] = pd.to_datetime(df["lastmod"], errors="coerce", utc=True)
        fresh = df[df["lastmod"] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)]
        agg["fresh_urls_30d"] = fresh.groupby("section")["loc"].count()
        agg["fresh_urls_30d"] = agg["fresh_urls_30d"].fillna(0).astype(int)

    return agg.sort_values("url_count", ascending=False).reset_index()


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def fetch_robots(site_url: str) -> dict:
    """Parse robots.txt and return key signals."""
    base = site_url.rstrip("/")
    result = {
        "has_robots": False,
        "sitemaps_declared": [],
        "disallowed_patterns": [],
        "crawl_delay": None,
        "raw": "",
    }
    try:
        r = safe_get(f"{base}/robots.txt", headers=HEADERS, timeout=TIMEOUT)
        result["has_robots"] = r.status_code == 200
        result["raw"] = r.text
        for line in r.text.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                result["sitemaps_declared"].append(line.split(":", 1)[1].strip())
            elif line.lower().startswith("disallow:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    result["disallowed_patterns"].append(val)
            elif line.lower().startswith("crawl-delay:"):
                try:
                    result["crawl_delay"] = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
    except Exception:
        pass
    return result


def robots_issues(robots: dict) -> list:
    """Flag common robots.txt problems relevant to crawl budget."""
    issues = []
    if not robots["has_robots"]:
        issues.append(("warning", "No robots.txt found — crawlers have no guidance on what to skip."))
    if not robots["sitemaps_declared"]:
        issues.append(("warning", "robots.txt does not declare a Sitemap: URL — crawlers may miss content."))
    patterns = robots["disallowed_patterns"]
    blocks_archive = any("archive" in p.lower() for p in patterns)
    blocks_search = any("search" in p.lower() or "?" in p for p in patterns)
    if not blocks_archive:
        issues.append(("info", "robots.txt does not block /archive/ — old content may be consuming crawl budget."))
    if not blocks_search:
        issues.append(("info", "robots.txt does not block search/filter parameter URLs (?sort=, ?page=, etc.)."))
    return issues


# ---------------------------------------------------------------------------
# Common Crawl index — proxy for AI crawler coverage
# ---------------------------------------------------------------------------

CC_INDEX_API = "https://index.commoncrawl.org/CC-MAIN-2025-13-index"

def common_crawl_coverage(site_url: str, sections: list, max_per_section: int = 100) -> pd.DataFrame:
    """Check Common Crawl index for coverage of each section.
    Returns a DataFrame with section, cc_url_count.
    Common Crawl is a reasonable proxy for which content AI crawlers
    (many of which are built on CC data) have ingested.
    """
    host = urlparse(site_url).netloc
    rows = []
    for section in sections:
        query = f"{host}/{section}/*" if section != "(root)" else host
        try:
            r = requests.get(
                CC_INDEX_API,
                params={"url": query, "output": "json", "limit": max_per_section},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            count = len([l for l in r.text.strip().splitlines() if l])
        except Exception:
            count = 0
        rows.append({"section": section, "cc_url_count": count})
        time.sleep(0.3)  # be polite to CC API
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Shallow site spider — internal link depth from homepage
# ---------------------------------------------------------------------------

def _extract_links(html: str, base_url: str) -> list:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    host = urlparse(base_url).netloc
    links = []
    for h in hrefs:
        full = urljoin(base_url, h)
        if urlparse(full).netloc == host and not full.endswith((".css", ".js", ".png", ".jpg", ".svg")):
            links.append(full.split("#")[0].split("?")[0])
    return list(set(links))


def spider_depth(site_url: str, max_pages: int = 80) -> pd.DataFrame:
    """BFS spider from homepage, recording click depth per URL.
    Stays within the same hostname. Stops at max_pages to stay polite.
    Returns DataFrame with url, depth, section columns.
    """
    base = site_url.rstrip("/")
    visited = {base: 0}
    queue = [(base, 0)]
    rows = []

    while queue and len(visited) < max_pages:
        url, depth = queue.pop(0)
        try:
            r = safe_get(url, headers=HEADERS, timeout=TIMEOUT)
            if "text/html" not in r.headers.get("content-type", ""):
                continue
            links = _extract_links(r.text, base)
            for link in links:
                if link not in visited and len(visited) < max_pages:
                    visited[link] = depth + 1
                    queue.append((link, depth + 1))
            parts = urlparse(url).path.strip("/").split("/")
            section = parts[0] if parts and parts[0] else "(root)"
            rows.append({"url": url, "depth": depth, "section": section})
        except Exception:
            pass
        time.sleep(0.1)

    return pd.DataFrame(rows)


def depth_by_section(spider_df: pd.DataFrame) -> pd.DataFrame:
    if spider_df.empty:
        return pd.DataFrame()
    return (
        spider_df.groupby("section")
        .agg(pages_found=("url", "size"), avg_depth=("depth", "mean"))
        .sort_values("pages_found", ascending=False)
        .reset_index()
    )
