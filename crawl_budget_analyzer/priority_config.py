"""Editorial priority configuration and bot-attention mismatch scoring.

The site owner declares which sections matter most editorially. This module
diffs that declared priority against actual bot crawl attention to surface
where the IA is working against content strategy.

Config file format (YAML or JSON):

    sections:
      news:        1       # rank 1 = highest editorial priority
      opinion:     2
      features:    3
      business:    4
      sport:       5
      sci-tech:    6
      archive:     99     # explicitly low — expected to be deprioritised

    bots:                 # which bots to score; omit to score all
      - googlebot
      - bingbot
      - gptbot
      - perplexitybot

The mismatch score per section per bot is:

    attention_share - priority_share

Positive = bot is paying MORE attention than editorial priority warrants.
Negative = bot is paying LESS attention than editorial priority warrants.

Sections with large negative scores on Googlebot are structural IA problems.
Sections with large negative scores on GPTBot/Perplexitybot indicate content
that AI surfaces are not finding — relevant for AI search strategy.
"""

import json
from pathlib import Path

import pandas as pd
import yaml


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _priority_shares(section_ranks: dict) -> dict:
    """Convert rank integers to share-of-attention weights.
    Rank 1 gets the largest share. Archive-level ranks (>10) get near-zero.
    Uses inverse-rank normalised to sum to 1.
    """
    inverse = {s: 1.0 / r for s, r in section_ranks.items()}
    total = sum(inverse.values())
    return {s: v / total for s, v in inverse.items()}


def mismatch_report(
    bot_df: pd.DataFrame,
    config: dict,
    url_col: str = "url",
    bot_col: str = "bot_name",
) -> pd.DataFrame:
    """Return a tidy DataFrame with one row per (section, bot) showing:
      - editorial_priority_share  — declared importance as a fraction
      - crawl_attention_share     — actual fraction of bot's requests
      - mismatch                  — attention minus priority (+ = over-crawled,
                                    - = under-crawled relative to importance)
      - mismatch_label            — human-readable verdict
    """
    from .diff_engine import url_section

    section_ranks = config.get("sections", {})
    target_bots = config.get("bots", None)

    priority_shares = _priority_shares(section_ranks)

    df = bot_df.copy()
    df["section"] = df[url_col].apply(url_section)

    if target_bots:
        df = df[df[bot_col].isin(target_bots)]

    rows = []
    for bot, group in df.groupby(bot_col):
        total_requests = len(group)
        section_counts = group["section"].value_counts()

        all_sections = set(section_ranks.keys()) | set(section_counts.index)
        for section in all_sections:
            attention = section_counts.get(section, 0) / total_requests if total_requests else 0
            priority = priority_shares.get(section, 0)
            mismatch = attention - priority
            if abs(mismatch) < 0.01:
                label = "aligned"
            elif mismatch > 0.15:
                label = "significantly over-crawled"
            elif mismatch > 0:
                label = "over-crawled"
            elif mismatch < -0.15:
                label = "significantly under-crawled"
            else:
                label = "under-crawled"

            rows.append({
                "bot": bot,
                "section": section,
                "editorial_priority_share": round(priority, 4),
                "crawl_attention_share": round(attention, 4),
                "mismatch": round(mismatch, 4),
                "mismatch_label": label,
            })

    result = pd.DataFrame(rows)
    result = result.sort_values(["bot", "mismatch"]).reset_index(drop=True)
    return result


def top_problems(mismatch_df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Return the n most under-crawled sections per bot — these are the
    highest-priority IA fixes.
    """
    return (
        mismatch_df[mismatch_df["mismatch_label"].str.contains("under")]
        .sort_values("mismatch")
        .groupby("bot")
        .head(n)
        .reset_index(drop=True)
    )
