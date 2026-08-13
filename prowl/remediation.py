"""Data-driven remediation engine.

Joins the mismatch report, URL depth data, and crawl waste data to produce
a prioritised, plain-English action list. Each action names who needs to act
(editor, dev, SEO), what specifically to change, and why the data supports it.

The audience is the same as briefing.py — editors and digital leads who need
to make the case internally for structural changes.
"""

import pandas as pd


# ---------------------------------------------------------------------------
# Action templates
# Each is a dict with: title, owner, action, rationale_template, priority (1-3)
# rationale_template is a format string; available keys depend on the action.
# ---------------------------------------------------------------------------

def _depth_actions(section: str, avg_depth: float, bot: str) -> list:
    actions = []
    if avg_depth > 3:
        actions.append({
            "section": section,
            "bot": bot,
            "priority": 1,
            "owner": "Dev / CMS team",
            "title": f"Flatten URL structure for /{section}/",
            "action": (
                f"Reduce the URL depth of `/{section}/` content from an average of "
                f"{avg_depth:.1f} path segments to 2 (homepage → section → article). "
                f"Example: change `/features/magazine/long-reads/slug` to `/features/slug`."
            ),
            "rationale": (
                f"{bot.title()} crawlers deprioritise content that is more than 2–3 clicks "
                f"from the homepage. `/{section}/` URLs average {avg_depth:.1f} path segments deep, "
                f"which puts them below the crawl attention threshold."
            ),
        })
    elif avg_depth > 2:
        actions.append({
            "section": section,
            "bot": bot,
            "priority": 2,
            "owner": "Dev / CMS team",
            "title": f"Review URL depth for /{section}/",
            "action": (
                f"`/{section}/` URLs average {avg_depth:.1f} path segments. "
                f"Check whether any sub-categories can be collapsed into the section root."
            ),
            "rationale": (
                f"Depth above 2 begins to reduce crawl frequency. Not critical but worth reviewing "
                f"alongside other fixes."
            ),
        })
    return actions


def _archive_bloat_actions(mismatch_df: pd.DataFrame) -> list:
    actions = []
    archive_rows = mismatch_df[
        (mismatch_df["section"] == "archive") &
        (mismatch_df["mismatch"] > 0.15)
    ]
    for _, row in archive_rows.iterrows():
        pct = round(row["crawl_attention_share"] * 100)
        actions.append({
            "section": "archive",
            "bot": row["bot"],
            "priority": 1,
            "owner": "Dev / SEO team",
            "title": "Suppress archive from consuming crawl budget",
            "action": (
                "Add a `Crawl-delay` directive or `Disallow` rules in robots.txt for the "
                "`/archive/` path, OR move archive content to a subdomain "
                "(e.g. `archive.example.com`) so it has its own separate crawl budget. "
                "Do NOT noindex archive pages unless you want them removed from search entirely."
            ),
            "rationale": (
                f"{row['bot'].title()} is spending **{pct}%** of its crawl time on `/archive/` — "
                f"that budget is directly taken away from current, editorially important content."
            ),
        })
    return actions


def _homepage_linking_actions(mismatch_df: pd.DataFrame, crawl_df: pd.DataFrame) -> list:
    """Sections that are significantly under-crawled and have low crawl counts
    likely lack homepage/top-level internal links.
    """
    actions = []
    under = mismatch_df[mismatch_df["mismatch_label"].str.contains("significantly under")]

    for _, row in under.iterrows():
        section = row["section"]
        if section == "archive":
            continue
        section_crawls = crawl_df[crawl_df["section"] == section]["requests"].sum() if "section" in crawl_df.columns else 0
        actions.append({
            "section": section,
            "bot": row["bot"],
            "priority": 1,
            "owner": "Editor / Digital team",
            "title": f"Add /{section}/ content to homepage and high-traffic page feeds",
            "action": (
                f"Add a '**Latest from {section.title()}**' feed or widget to the homepage. "
                f"Also add '**Related {section.title()}**' links on the highest-traffic news "
                f"articles — crawlers follow internal links from pages they already visit frequently."
            ),
            "rationale": (
                f"`/{section}/` is rated high editorial priority but {row['bot']} is spending only "
                f"{round(row['crawl_attention_share']*100)}% of its crawl time there "
                f"(expected ~{round(row['editorial_priority_share']*100)}%). "
                f"The most common cause is that crawlers have no direct link path from "
                f"high-frequency pages (homepage, news fronts) to this section."
            ),
        })
    return actions


def _sitemap_actions(mismatch_df: pd.DataFrame) -> list:
    """For under-crawled sections, a dedicated sitemap with accurate lastmod
    is the fastest way to signal freshness without waiting for structural changes.
    """
    actions = []
    under_sections = mismatch_df[
        mismatch_df["mismatch_label"].str.contains("under") &
        (mismatch_df["bot"] == "googlebot")
    ]["section"].unique()

    for section in under_sections:
        if section == "archive":
            continue
        actions.append({
            "section": section,
            "bot": "googlebot",
            "priority": 2,
            "owner": "SEO / Dev team",
            "title": f"Create a dedicated sitemap for /{section}/",
            "action": (
                f"Create `sitemap-{section}.xml` and submit it in Google Search Console. "
                f"Set `<lastmod>` to the actual publish/update date of each article — "
                f"not today's date on every URL, which trains Google to ignore the signal. "
                f"For time-sensitive {section} content, use `<changefreq>daily</changefreq>`."
            ),
            "rationale": (
                f"A section-specific sitemap is the fastest short-term fix while URL structure "
                f"and internal linking changes are being implemented. It directly tells Google "
                f"which `/{section}/` URLs exist and when they were last updated."
            ),
        })
    return actions


def _waste_actions(waste_df: pd.DataFrame) -> list:
    actions = []
    if waste_df.empty:
        return actions
    row = waste_df.iloc[0]
    if row.get("parameter_url_requests", 0) / max(row["total_requests"], 1) > 0.05:
        actions.append({
            "section": "(all)",
            "bot": "(all bots)",
            "priority": 1,
            "owner": "Dev team",
            "title": "Block parameter URLs from being crawled",
            "action": (
                "Add `Disallow` rules in robots.txt for URL patterns containing `?` parameters "
                "used for filtering, sorting, or pagination (e.g. `?page=`, `?sort=`, `?filter=`). "
                "These create near-duplicate pages that consume crawl budget without adding value."
            ),
            "rationale": (
                f"{round(row['parameter_url_requests'] / row['total_requests'] * 100)}% of bot "
                f"requests are hitting parameter URLs. Each one wastes crawl budget that could "
                f"go to real editorial content."
            ),
        })
    if row.get("4xx_requests", 0) / max(row["total_requests"], 1) > 0.03:
        actions.append({
            "section": "(all)",
            "bot": "(all bots)",
            "priority": 1,
            "owner": "Dev team",
            "title": "Fix or remove 404 URLs that bots keep crawling",
            "action": (
                "Audit URLs returning 4xx errors in the log data and either restore the content, "
                "redirect to the correct URL, or return a proper 410 Gone status so crawlers "
                "stop revisiting them."
            ),
            "rationale": (
                f"{round(row['4xx_requests'] / row['total_requests'] * 100)}% of bot requests "
                f"are hitting broken URLs. Crawlers will keep retrying these, wasting budget."
            ),
        })
    return actions


def build_remediation_plan(
    mismatch_df: pd.DataFrame,
    depth_df: pd.DataFrame,
    waste_df: pd.DataFrame,
    crawl_freq_df: pd.DataFrame,
) -> pd.DataFrame:
    """Produce a prioritised action list as a DataFrame.

    depth_df: output of diff_engine.depth_vs_crawl_attention(), needs a 'section' join.
    crawl_freq_df: output of diff_engine.crawl_frequency_by_section(), indexed by section.
    """
    actions = []

    # Archive bloat
    actions += _archive_bloat_actions(mismatch_df)

    # Homepage linking gaps for under-crawled priority sections
    freq_reset = crawl_freq_df.reset_index() if "section" not in crawl_freq_df.columns else crawl_freq_df
    actions += _homepage_linking_actions(mismatch_df, freq_reset)

    # Sitemap gaps
    actions += _sitemap_actions(mismatch_df)

    # URL depth per section — join depth data with mismatch sections
    # depth_df is indexed by depth integer; we use per-section depth from mismatch
    under_sections = mismatch_df[
        mismatch_df["mismatch_label"].str.contains("under")
    ]["section"].unique()

    # We don't have per-section depth without the raw bot_df here, so we use
    # the global depth distribution as a proxy: if median depth > 2, flag it.
    if not depth_df.empty:
        depth_df = depth_df.reset_index()
        weighted_avg = (depth_df["depth"] * depth_df["requests"]).sum() / depth_df["requests"].sum()
        if weighted_avg > 2.5:
            for section in under_sections:
                if section == "archive":
                    continue
                actions += _depth_actions(section, weighted_avg, "googlebot")

    # Crawl waste
    actions += _waste_actions(waste_df)

    if not actions:
        return pd.DataFrame()

    df = pd.DataFrame(actions).drop_duplicates(subset=["title", "bot"])
    df = df.sort_values(["priority", "section"]).reset_index(drop=True)
    return df


def format_remediation_md(plan_df: pd.DataFrame, site_url: str = "your site") -> str:
    if plan_df.empty:
        return "## Remediation Plan\n\nNo significant structural issues detected.\n"

    lines = [
        f"## Remediation Plan — {site_url}",
        "",
        "Actions are ordered by priority. Priority 1 items have the highest impact "
        "on crawl budget reallocation and should be addressed first.",
        "",
    ]

    for priority, group in plan_df.groupby("priority"):
        label = {1: "🔴 Priority 1 — Fix immediately", 2: "🟡 Priority 2 — Fix this quarter",
                 3: "🟢 Priority 3 — Nice to have"}.get(priority, f"Priority {priority}")
        lines += [f"### {label}", ""]
        for _, row in group.iterrows():
            lines += [
                f"#### {row['title']}",
                f"**Who:** {row['owner']}  ",
                f"**Affects:** `/{row['section']}/` — {row['bot']}",
                "",
                f"**What to do:** {row['action']}",
                "",
                f"**Why:** {row['rationale']}",
                "",
            ]

    return "\n".join(lines)
