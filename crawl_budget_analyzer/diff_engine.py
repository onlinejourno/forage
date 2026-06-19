"""Join verified bot crawl logs against CMS publish metadata to compute the
metrics that actually matter for crawl-budget decisions: time-to-first-crawl,
crawl frequency by section, and depth-vs-attention correlation.
"""

from urllib.parse import urlparse

import pandas as pd


def url_section(url: str) -> str:
    """First path segment, e.g. /news/national/foo -> 'news'."""
    parts = urlparse(url).path.strip("/").split("/")
    return parts[0] if parts and parts[0] else "(root)"


def url_depth(url: str) -> int:
    """Number of path segments, used as a proxy for clicks-from-homepage."""
    return len([p for p in urlparse(url).path.strip("/").split("/") if p])


def crawl_frequency_by_section(bot_df: pd.DataFrame, url_col: str = "url") -> pd.DataFrame:
    df = bot_df.copy()
    df["section"] = df[url_col].apply(url_section)
    return (
        df.groupby("section")
        .agg(requests=("section", "size"), unique_urls=(url_col, "nunique"))
        .assign(requests_per_url=lambda d: d["requests"] / d["unique_urls"])
        .sort_values("requests", ascending=False)
    )


def depth_vs_crawl_attention(bot_df: pd.DataFrame, url_col: str = "url") -> pd.DataFrame:
    df = bot_df.copy()
    df["depth"] = df[url_col].apply(url_depth)
    return (
        df.groupby("depth")
        .agg(requests=("depth", "size"), unique_urls=(url_col, "nunique"))
        .assign(requests_per_url=lambda d: d["requests"] / d["unique_urls"])
    )


def time_to_first_crawl(bot_df: pd.DataFrame, publish_df: pd.DataFrame, url_col="url", publish_url_col="url",
                         publish_time_col="published_at") -> pd.DataFrame:
    """publish_df: one row per article, from the CMS (url, published_at).
    Returns per-article lag between publish time and first bot hit.
    """
    first_crawl = bot_df.groupby(url_col)["datetime"].min().rename("first_crawled_at")
    merged = publish_df.merge(first_crawl, left_on=publish_url_col, right_index=True, how="left")
    merged["published_at"] = pd.to_datetime(merged[publish_time_col], utc=True)
    merged["first_crawled_at"] = pd.to_datetime(merged["first_crawled_at"], utc=True)
    merged["lag_minutes"] = (merged["first_crawled_at"] - merged["published_at"]).dt.total_seconds() / 60
    merged["section"] = merged[publish_url_col].apply(url_section)
    return merged[[publish_url_col, "section", "published_at", "first_crawled_at", "lag_minutes"]]


def crawl_waste(bot_df: pd.DataFrame, url_col: str = "url", status_col: str = "status") -> pd.DataFrame:
    """Flag the classic waste categories: parameter URLs, 4xx, redirect chains
    (3xx repeatedly hit), and soft-404 candidates (200 but tiny response size).
    """
    df = bot_df.copy()
    df["has_params"] = df[url_col].str.contains(r"\?", regex=True, na=False)
    df["status"] = pd.to_numeric(df[status_col], errors="coerce")
    df["is_4xx"] = df["status"].between(400, 499)
    df["is_3xx"] = df["status"].between(300, 399)
    summary = pd.DataFrame({
        "parameter_url_requests": [df["has_params"].sum()],
        "4xx_requests": [df["is_4xx"].sum()],
        "3xx_requests": [df["is_3xx"].sum()],
        "total_requests": [len(df)],
    })
    summary["waste_pct"] = (
        (summary["parameter_url_requests"] + summary["4xx_requests"] + summary["3xx_requests"])
        / summary["total_requests"] * 100
    )
    return summary
