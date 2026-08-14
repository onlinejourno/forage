"""Generate a plain-English editorial briefing from the mismatch report.

The audience is journalists, digital editors, and SEO leads — not developers.
Output is Markdown so it can be pasted into Slack, Notion, Google Docs, or
a strategy deck without reformatting.
"""

from datetime import date

import pandas as pd

BOT_PLAIN_NAMES = {
    "googlebot": "Google Search",
    "bingbot": "Bing Search",
    "gptbot": "ChatGPT / OpenAI",
    "perplexitybot": "Perplexity AI",
    "claudebot": "Claude / Anthropic",
    "applebot": "Apple (Siri / Spotlight)",
    "duckduckbot": "DuckDuckGo",
    "yandexbot": "Yandex Search",
}

TECH_ASKS = {
    "archive": (
        "Ask the dev team to move archive content under a dedicated `/archive/` "
        "URL path and add it to robots.txt with a lower crawl priority, so search "
        "engines stop treating historical content as your main product."
    ),
    "news": (
        "Ensure the homepage links directly to the latest news articles (not just "
        "the section front), and that the news sitemap is submitted to Google Search "
        "Console with accurate `<lastmod>` timestamps."
    ),
    "features": (
        "Add a 'Latest Features' module to the homepage and cross-link from news "
        "articles to related long-reads. Features content is often buried too deep "
        "in the URL structure for crawlers to prioritise it."
    ),
    "opinion": (
        "Opinion content is frequently missed by crawlers because it updates less "
        "often than news. Add opinion pieces to the homepage feed and ensure the "
        "opinion section is no more than two clicks from the homepage."
    ),
}

DEFAULT_TECH_ASK = (
    "Check how many clicks from the homepage this section is — if it's more than "
    "two, the dev team should flatten the URL structure or add direct homepage links."
)


def _bot_label(bot: str) -> str:
    return BOT_PLAIN_NAMES.get(bot, bot.title())


def _verdict_sentence(bot: str, section: str, label: str, attention: float, priority: float) -> str:
    bot_name = _bot_label(bot)
    pct_attention = round(attention * 100)
    pct_priority = round(priority * 100)

    if "significantly over-crawled" in label:
        return (
            f"{bot_name} is spending **{pct_attention}%** of its crawl time on `/{section}/`, "
            f"but you've rated this section as worth only ~{pct_priority}% of attention. "
            f"This is a significant mismatch — crawl budget is being diverted away from more important content."
        )
    elif "over-crawled" in label:
        return (
            f"{bot_name} is spending **{pct_attention}%** of its time on `/{section}/` "
            f"(you'd expect ~{pct_priority}%). Slightly over-crawled but not critical."
        )
    elif "significantly under-crawled" in label:
        return (
            f"{bot_name} is spending only **{pct_attention}%** of its crawl time on `/{section}/`, "
            f"but you've rated it as worth ~{pct_priority}% of attention. "
            f"This section is effectively invisible to {bot_name}."
        )
    elif "under-crawled" in label:
        return (
            f"{bot_name} is spending **{pct_attention}%** on `/{section}/` "
            f"(you'd expect ~{pct_priority}%). Under-crawled — worth investigating."
        )
    return f"{bot_name} and `/{section}/` are broadly aligned ({pct_attention}% vs {pct_priority}% expected)."


def generate_briefing(
    mismatch_df: pd.DataFrame,
    site_url: str = "your site",
    log_date_range: str = None,
) -> str:
    today = date.today().isoformat()
    date_note = f" (logs: {log_date_range})" if log_date_range else ""

    lines = [
        f"# Bot Crawl Briefing — {site_url}",
        f"*Generated {today}{date_note}*",
        "",
        "## What this is",
        "",
        "This briefing shows where search and AI crawlers are actually spending "
        "their time on the site, compared to the editorial priorities the team set. "
        "A mismatch means the site's structure is sending the wrong signals — "
        "crawlers are effectively ranking your sections differently to how you would.",
        "",
        "**This is not a technical report.** It's a prompt for editorial and product "
        "conversations about site structure and content strategy.",
        "",
    ]

    bots = mismatch_df["bot"].unique()

    for bot in sorted(bots):
        bot_df = mismatch_df[mismatch_df["bot"] == bot].copy()
        bot_label = _bot_label(bot)

        lines += [f"---", f"## {bot_label}", ""]

        # Significant problems first
        bad = bot_df[bot_df["mismatch_label"].str.contains("significantly under")]
        moderate = bot_df[bot_df["mismatch_label"].str.contains(r"^under", regex=True)]
        over = bot_df[bot_df["mismatch_label"].str.contains("over")]
        aligned = bot_df[bot_df["mismatch_label"] == "aligned"]

        if bad.empty and moderate.empty:
            lines += ["**No major mismatches detected for this bot.**", ""]
            continue

        if not bad.empty:
            lines += ["### ⚠️ Sections this bot is ignoring", ""]
            for _, row in bad.iterrows():
                lines += [
                    f"**{row['section'].title()}**  ",
                    _verdict_sentence(bot, row["section"], row["mismatch_label"],
                                      row["crawl_attention_share"], row["editorial_priority_share"]),
                    "",
                    f"*What to ask the team:* {TECH_ASKS.get(row['section'], DEFAULT_TECH_ASK)}",
                    "",
                ]

        if not moderate.empty:
            lines += ["### 📉 Sections getting less attention than they deserve", ""]
            for _, row in moderate.iterrows():
                lines += [
                    f"- **{row['section'].title()}**: "
                    f"{round(row['crawl_attention_share']*100)}% actual vs "
                    f"~{round(row['editorial_priority_share']*100)}% expected",
                ]
            lines += [""]

        if not over.empty:
            lines += ["### 📈 Sections getting more attention than warranted", ""]
            for _, row in over.iterrows():
                lines += [
                    f"- **{row['section'].title()}**: "
                    f"{round(row['crawl_attention_share']*100)}% actual vs "
                    f"~{round(row['editorial_priority_share']*100)}% expected",
                ]
            lines += [""]

    lines += [
        "---",
        "## What to do with this",
        "",
        "1. **Share with the digital/product team** — the mismatches flagged above "
        "are structural issues that require changes to URL architecture, internal "
        "linking, or sitemaps. Editors can flag the priority; the dev team implements the fix.",
        "",
        "2. **Check again in 4–6 weeks** — crawl patterns change slowly after "
        "structural fixes. Don't expect overnight results.",
        "",
        "3. **Different bots, different problems** — Google Search and ChatGPT/OpenAI "
        "crawl for different purposes. A section ignored by GPTBot may never surface "
        "in AI-generated answers, even if Google indexes it fine.",
        "",
        "*This briefing was generated by Forage, "
        "using server access logs as the data source.*",
    ]

    return "\n".join(lines)
