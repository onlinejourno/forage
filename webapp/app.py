"""Bot Crawl Analyser — journalist-facing Streamlit dashboard.

Enter a news site URL. Get a plain-English report on how search engines
and AI crawlers see the site, where crawl budget is being wasted, and
what editorial and product changes would improve visibility.

No server access required. All data from public sources.
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from webapp import audit_log, fetchers

st.set_page_config(
    page_title="Bot Crawl Analyser",
    page_icon="🤖",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — inputs
# ---------------------------------------------------------------------------

st.sidebar.title("🤖 Bot Crawl Analyser")
st.sidebar.markdown(
    "Understand how Google, Bing, and AI crawlers see your news site — "
    "and where your content is being overlooked."
)

site_url = st.sidebar.text_input(
    "News site URL",
    placeholder="https://example.com",
    help="Enter the homepage URL of the news site to analyse.",
)

competitor_urls_raw = st.sidebar.text_area(
    "Competitor URLs (one per line, optional)",
    placeholder="https://competitor1.com\nhttps://competitor2.com",
    height=100,
)

editorial_priority = st.sidebar.text_area(
    "Your section priority (one per line, rank order)",
    value="news\nopinion\nfeatures\nbusiness\nsport\nentertainment\nsci-tech\narchive",
    height=180,
    help="List your sections from most to least editorially important. "
         "Archive should go last.",
)

run = st.sidebar.button("Analyse", type="primary", use_container_width=True)

st.sidebar.markdown("---")

# Recently audited sites
popular = audit_log.popular_sites(limit=8)
recent = audit_log.recent_audits(limit=8)

if popular:
    st.sidebar.markdown("**🔥 Most audited sites**")
    for row in popular:
        st.sidebar.markdown(
            f"`{row['site'].replace('https://','').replace('http://','').rstrip('/')}` "
            f"— {row['times_audited']}×"
        )
    st.sidebar.markdown("")

if recent:
    st.sidebar.markdown("**🕐 Recently audited**")
    for row in recent:
        st.sidebar.markdown(
            f"`{row['site'].replace('https://','').replace('http://','').rstrip('/')}` "
            f"<span style='color:grey;font-size:0.8em'>{row['audited_at']}</span>",
            unsafe_allow_html=True,
        )
    st.sidebar.markdown("")

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Data sources:** sitemap.xml, robots.txt, public site spider, "
    "Common Crawl index. No login or server access required."
)

# ---------------------------------------------------------------------------
# Main area — before run
# ---------------------------------------------------------------------------

if not run or not site_url:
    st.title("Bot Crawl Analyser")
    st.markdown(
        """
        Enter a news site URL in the sidebar and click **Analyse** to see:

        - 📊 **How crawl budget is distributed** across sections (news, opinion, features, archive…)
        - 🤖 **Which sections AI crawlers have indexed** via Common Crawl
        - ⚠️ **Where your robots.txt and sitemap are working against you**
        - 🔍 **How your site structure compares to competitors**
        - 📋 **A plain-English briefing** you can share with the product/dev team

        *No server access required. Works on any public news site, including competitors.*
        """
    )
    st.stop()

# ---------------------------------------------------------------------------
# Parse priority config from sidebar text
# ---------------------------------------------------------------------------

priority_sections = [
    s.strip().lower()
    for s in editorial_priority.strip().splitlines()
    if s.strip()
]
priority_ranks = {s: i + 1 for i, s in enumerate(priority_sections)}

competitor_urls = [
    u.strip() for u in competitor_urls_raw.strip().splitlines() if u.strip()
]

# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------

col_title, col_meta = st.columns([3, 1])
with col_title:
    st.title(f"Bot Crawl Report — {site_url}")
with col_meta:
    if times_audited > 1:
        st.metric("Times audited", times_audited, help="How many times this site has been analysed using this tool")

def _run_site(url: str, label: str):
    results = {}
    with st.spinner(f"Fetching sitemap for {label}…"):
        results["sitemap_df"] = fetchers.fetch_sitemap(url)
        results["sitemap_summary"] = fetchers.sitemap_section_summary(results["sitemap_df"])

    with st.spinner(f"Checking robots.txt for {label}…"):
        results["robots"] = fetchers.fetch_robots(url)
        results["robots_issues"] = fetchers.robots_issues(results["robots"])

    with st.spinner(f"Spidering {label} (up to 80 pages)…"):
        results["spider_df"] = fetchers.spider_depth(url, max_pages=80)
        results["depth_summary"] = fetchers.depth_by_section(results["spider_df"])

    if not results["sitemap_summary"].empty:
        sections = results["sitemap_summary"]["section"].tolist()[:10]
        with st.spinner(f"Checking Common Crawl coverage for {label}…"):
            results["cc_df"] = fetchers.common_crawl_coverage(url, sections)
    else:
        results["cc_df"] = pd.DataFrame()

    return results

main_results = _run_site(site_url, site_url)

# Record this audit
sm_summary = main_results.get("sitemap_summary", pd.DataFrame())
audit_log.record_audit(
    site_url,
    sitemap_urls=int(sm_summary["url_count"].sum()) if not sm_summary.empty else 0,
    sections=len(sm_summary) if not sm_summary.empty else 0,
)
times_audited = audit_log.site_audit_count(site_url)

competitor_results = {}
for cu in competitor_urls:
    competitor_results[cu] = _run_site(cu, cu)
    audit_log.record_audit(cu)

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tabs = st.tabs(["📊 Crawl Map", "🤖 AI Coverage", "⚠️ Issues", "🔍 Competitors", "📋 Briefing"])

# ── Tab 1: Crawl Map ──────────────────────────────────────────────────────

with tabs[0]:
    st.header("Crawl Map — how your site structure is seen by bots")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Sitemap: URLs per section")
        sm = main_results["sitemap_summary"]
        if sm.empty:
            st.warning("No sitemap found or sitemap returned no URLs.")
        else:
            sm["editorial_rank"] = sm["section"].map(priority_ranks).fillna(99).astype(int)
            sm = sm.sort_values("editorial_rank")
            fig = px.bar(
                sm,
                x="section",
                y="url_count",
                color="avg_depth",
                color_continuous_scale="RdYlGn_r",
                labels={"url_count": "URLs in sitemap", "section": "Section", "avg_depth": "Avg URL depth"},
                title="URLs per section (colour = avg depth — red = too deep)",
            )
            fig.update_layout(xaxis_tickangle=-35)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(sm.drop(columns=["editorial_rank"]), use_container_width=True)

    with col2:
        st.subheader("Spider: click depth from homepage")
        dd = main_results["depth_summary"]
        if dd.empty:
            st.warning("Spider returned no results — site may be blocking automated requests.")
        else:
            dd["editorial_rank"] = dd["section"].map(priority_ranks).fillna(99).astype(int)
            dd["priority_label"] = dd["section"].map(
                lambda s: f"#{priority_ranks[s]}" if s in priority_ranks else "unranked"
            )
            fig2 = px.scatter(
                dd,
                x="avg_depth",
                y="pages_found",
                text="section",
                color="avg_depth",
                color_continuous_scale="RdYlGn_r",
                size="pages_found",
                labels={"avg_depth": "Avg clicks from homepage", "pages_found": "Pages found"},
                title="Sections: depth vs. pages found (red = too deep)",
            )
            fig2.update_traces(textposition="top center")
            st.plotly_chart(fig2, use_container_width=True)
            st.dataframe(dd.drop(columns=["editorial_rank"]), use_container_width=True)

    st.subheader("Priority vs. depth mismatch")
    if not sm.empty and not dd.empty:
        merged = sm[["section", "url_count", "avg_depth"]].merge(
            dd[["section", "avg_depth"]].rename(columns={"avg_depth": "spider_depth"}),
            on="section", how="outer"
        )
        merged["editorial_rank"] = merged["section"].map(priority_ranks).fillna(99).astype(int)
        merged["problem"] = merged.apply(
            lambda r: "⚠️ High priority, deep URL" if r["editorial_rank"] <= 3 and (r.get("spider_depth") or 0) > 2
            else ("📦 Low priority but shallow" if r["editorial_rank"] >= len(priority_sections) - 1 and (r.get("spider_depth") or 0) <= 1
            else "✅ OK"),
            axis=1
        )
        st.dataframe(
            merged.sort_values("editorial_rank")[["section", "editorial_rank", "url_count", "avg_depth", "spider_depth", "problem"]],
            use_container_width=True
        )


# ── Tab 2: AI Coverage ───────────────────────────────────────────────────

with tabs[1]:
    st.header("AI Crawler Coverage — Common Crawl index")
    st.markdown(
        "Common Crawl is used by many AI systems (including training datasets for "
        "ChatGPT, Gemini, and others) as a primary web corpus. Coverage here is a "
        "reasonable proxy for which of your sections AI assistants have actually ingested."
    )

    cc = main_results["cc_df"]
    if cc.empty:
        st.warning("No Common Crawl data returned — the API may be rate-limiting or the site has low coverage.")
    else:
        cc["editorial_rank"] = cc["section"].map(priority_ranks).fillna(99).astype(int)
        cc = cc.sort_values("editorial_rank")
        fig3 = px.bar(
            cc,
            x="section",
            y="cc_url_count",
            color="cc_url_count",
            color_continuous_scale="Blues",
            labels={"cc_url_count": "URLs in Common Crawl", "section": "Section"},
            title="Common Crawl coverage by section (proxy for AI ingestion)",
        )
        fig3.update_layout(xaxis_tickangle=-35)
        st.plotly_chart(fig3, use_container_width=True)

        st.markdown("**What this means for AI assistants:**")
        low_cc = cc[cc["cc_url_count"] < 5]
        for _, row in low_cc.iterrows():
            rank = priority_ranks.get(row["section"])
            if rank and rank <= 4:
                st.warning(
                    f"`/{row['section']}/` has only {row['cc_url_count']} URLs in Common Crawl "
                    f"but is ranked #{rank} in your editorial priorities. "
                    f"This content is likely underrepresented in AI training data and may not "
                    f"surface well in AI assistant responses."
                )


# ── Tab 3: Issues ────────────────────────────────────────────────────────

with tabs[2]:
    st.header("Structural Issues")

    st.subheader("robots.txt")
    issues = main_results["robots_issues"]
    if not issues:
        st.success("No major robots.txt issues detected.")
    for level, msg in issues:
        if level == "warning":
            st.warning(msg)
        else:
            st.info(msg)

    with st.expander("View raw robots.txt"):
        st.code(main_results["robots"].get("raw", "Not found"), language="text")

    st.subheader("Sitemap quality")
    sm = main_results["sitemap_summary"]
    if not sm.empty:
        if "fresh_urls_30d" in sm.columns:
            stale = sm[sm["fresh_urls_30d"] == 0]
            for _, row in stale.iterrows():
                if priority_ranks.get(row["section"], 99) <= 5:
                    st.warning(
                        f"`/{row['section']}/` has {row['url_count']} URLs in the sitemap "
                        f"but **zero with a lastmod date in the last 30 days**. "
                        f"Google may treat this section as stale and reduce crawl frequency."
                    )
        deep = sm[sm["avg_depth"] > 3]
        for _, row in deep.iterrows():
            rank = priority_ranks.get(row["section"], 99)
            if rank <= 5:
                st.warning(
                    f"`/{row['section']}/` (priority #{rank}) has an average URL depth of "
                    f"{row['avg_depth']:.1f} — too deep for reliable crawl attention. "
                    f"Ask the dev team to flatten the URL structure."
                )

    st.subheader("Archive risk")
    if not sm.empty and "archive" in sm["section"].values:
        archive_count = sm[sm["section"] == "archive"]["url_count"].values[0]
        total = sm["url_count"].sum()
        archive_pct = archive_count / total * 100
        if archive_pct > 20:
            st.error(
                f"**{archive_pct:.0f}% of all sitemap URLs are in `/archive/`** "
                f"({archive_count:,} of {total:,} total). "
                f"If crawlers are following these, they're consuming budget that should go "
                f"to fresh editorial content. Add `/archive/` to robots.txt Disallow rules "
                f"or move it to a subdomain."
            )
        elif archive_pct > 10:
            st.warning(f"Archive is {archive_pct:.0f}% of sitemap URLs — worth monitoring.")


# ── Tab 4: Competitors ───────────────────────────────────────────────────

with tabs[3]:
    st.header("Competitor Comparison")

    if not competitor_urls:
        st.info("Add competitor URLs in the sidebar to enable comparison.")
    else:
        all_sites = {site_url: main_results, **competitor_results}
        rows = []
        for url, res in all_sites.items():
            sm = res["sitemap_summary"]
            dd = res["depth_summary"]
            rob = res["robots"]
            rows.append({
                "site": url,
                "total_sitemap_urls": sm["url_count"].sum() if not sm.empty else 0,
                "sections_in_sitemap": len(sm) if not sm.empty else 0,
                "avg_url_depth": sm["avg_depth"].mean().round(1) if not sm.empty else None,
                "sitemaps_in_robots": len(rob["sitemaps_declared"]),
                "archive_blocked": any("archive" in p.lower() for p in rob["disallowed_patterns"]),
                "search_params_blocked": any("?" in p or "search" in p.lower() for p in rob["disallowed_patterns"]),
            })

        comp_df = pd.DataFrame(rows)
        st.dataframe(comp_df, use_container_width=True)

        fig4 = px.bar(
            comp_df,
            x="site",
            y="total_sitemap_urls",
            title="Total sitemap URLs by site",
            labels={"total_sitemap_urls": "URLs in sitemap", "site": "Site"},
        )
        st.plotly_chart(fig4, use_container_width=True)

        fig5 = px.bar(
            comp_df,
            x="site",
            y="avg_url_depth",
            color="avg_url_depth",
            color_continuous_scale="RdYlGn_r",
            title="Average URL depth (lower = better for crawl budget)",
            labels={"avg_url_depth": "Avg depth", "site": "Site"},
        )
        st.plotly_chart(fig5, use_container_width=True)


# ── Tab 5: Briefing ──────────────────────────────────────────────────────

with tabs[4]:
    st.header("Editorial Briefing")
    st.markdown(
        "Copy this into Slack, Notion, or a Google Doc to share with your "
        "product, dev, and editorial teams."
    )

    sm = main_results["sitemap_summary"]
    robots = main_results["robots"]
    issues = main_results["robots_issues"]
    dd = main_results["depth_summary"]
    cc = main_results["cc_df"]

    briefing_lines = [
        f"# Bot Crawl Briefing — {site_url}",
        "",
        "## Why this matters",
        "Search engines and AI assistants don't see your site the way readers do. "
        "They follow links, read sitemaps, and make decisions about which sections "
        "deserve attention based on your site's structure — not editorial judgment. "
        "This briefing shows where those decisions are working against you.",
        "",
        "## Sitemap health",
    ]

    if sm.empty:
        briefing_lines.append("⚠️ No sitemap found. This is a critical issue — crawlers cannot reliably discover content.")
    else:
        total = sm["url_count"].sum()
        briefing_lines.append(f"Total URLs in sitemap: **{total:,}** across {len(sm)} sections.")
        if "archive" in sm["section"].values:
            archive_pct = sm[sm["section"]=="archive"]["url_count"].values[0] / total * 100
            if archive_pct > 15:
                briefing_lines.append(
                    f"\n⚠️ **{archive_pct:.0f}% of sitemap URLs are archive content.** "
                    "Crawlers spending time on archive pages cannot crawl fresh editorial content instead."
                )

    briefing_lines += ["", "## URL depth by section"]
    if not dd.empty:
        for _, row in dd.sort_values("avg_depth", ascending=False).iterrows():
            rank = priority_ranks.get(row["section"], 99)
            if rank <= 5 and row["avg_depth"] > 2:
                briefing_lines.append(
                    f"- **{row['section'].title()}** (priority #{rank}): avg {row['avg_depth']:.1f} clicks "
                    f"from homepage — crawlers deprioritise content beyond 2 clicks."
                )

    briefing_lines += ["", "## robots.txt"]
    for level, msg in issues:
        prefix = "⚠️" if level == "warning" else "ℹ️"
        briefing_lines.append(f"{prefix} {msg}")
    if not issues:
        briefing_lines.append("✅ No major robots.txt issues.")

    if not cc.empty:
        briefing_lines += ["", "## AI crawler coverage (Common Crawl)"]
        low = cc[cc["cc_url_count"] < 5]
        for _, row in low.iterrows():
            rank = priority_ranks.get(row["section"], 99)
            if rank <= 4:
                briefing_lines.append(
                    f"- **{row['section'].title()}** has only {row['cc_url_count']} URLs in Common Crawl "
                    f"(used by AI training datasets). This content may be absent from AI assistant answers."
                )

    briefing_lines += [
        "",
        "## What to ask the team",
        "1. **Dev team:** Flatten URL depth for high-priority sections to max 2 path segments.",
        "2. **Dev/SEO team:** Add `/archive/` Disallow to robots.txt to redirect crawl budget to fresh content.",
        "3. **SEO team:** Create section-specific sitemaps with accurate `lastmod` dates.",
        "4. **Editor:** Add 'Latest from Opinion/Features' widgets to the homepage so crawlers discover these sections.",
        "",
        "*Generated by Bot Crawl Analyser — data from public sources (sitemap, robots.txt, Common Crawl).*",
    ]

    briefing_md = "\n".join(briefing_lines)
    st.markdown(briefing_md)
    st.download_button(
        "⬇️ Download briefing as Markdown",
        data=briefing_md,
        file_name=f"bot-crawl-briefing-{urlparse(site_url).netloc}.md",
        mime="text/markdown",
    )
