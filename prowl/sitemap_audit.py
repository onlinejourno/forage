"""Audit XML sitemaps: lastmod accuracy, changefreq sanity, and coverage
gaps between what's submitted vs. what's actually in the crawl logs.
"""

import pandas as pd

from webapp.sitemap_parse import safe_sitemap_to_df


def load_sitemap(sitemap_url: str) -> pd.DataFrame:
    """Fetch and flatten a sitemap (or sitemap index) into a DataFrame.

    SSRF- and XXE-safe (see :mod:`webapp.sitemap_parse`).
    """
    return safe_sitemap_to_df(sitemap_url)


def flag_lastmod_issues(sitemap_df: pd.DataFrame) -> pd.DataFrame:
    """Heuristic check for the common 'lastmod bumped on every page regardless
    of real changes' anti-pattern: many distinct URLs sharing the exact same
    lastmod timestamp, clustered at a single point in time.
    """
    if "lastmod" not in sitemap_df.columns:
        raise ValueError("Sitemap has no <lastmod> field for any URL.")
    counts = sitemap_df["lastmod"].value_counts()
    suspicious_timestamps = counts[counts > 1].index
    flagged = sitemap_df[sitemap_df["lastmod"].isin(suspicious_timestamps)].copy()
    flagged["issue"] = "lastmod shared with other unrelated URLs - likely not a real change signal"
    return flagged


def coverage_gap(sitemap_df: pd.DataFrame, crawled_urls: pd.Series) -> dict:
    """Compare sitemap URLs against URLs actually seen in bot crawl logs."""
    sitemap_urls = set(sitemap_df["loc"])
    crawled = set(crawled_urls)
    return {
        "in_sitemap_not_crawled": sitemap_urls - crawled,
        "crawled_not_in_sitemap": crawled - sitemap_urls,
        "sitemap_count": len(sitemap_urls),
        "crawled_count": len(crawled),
    }
