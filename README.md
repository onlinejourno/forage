# News Crawl Budget Analyser

A tool for journalists, digital editors, and newsroom SEO leads to understand how search engines and AI crawlers actually see their publication — and where the site's structure is working against editorial priorities.

**No technical background required to use the web dashboard. No server access required.**

---

## The problem this solves

Search engines and AI assistants (ChatGPT, Perplexity, Google) don't read your site the way readers do. They follow links and sitemaps and make decisions about which sections deserve attention based on site structure — not editorial judgment.

A section like Opinion or Features can be editorially important but structurally invisible to crawlers: buried too deep in the URL hierarchy, missing from the sitemap, not linked from the homepage. The result is that Google doesn't index it promptly, and AI assistants don't know it exists.

This tool surfaces that gap and tells you — in plain English — what to ask the dev team to fix.

---

## Two ways to use it

### 1. Web dashboard (no setup required)

Enter any news site URL. Get a report in ~60 seconds. Works on your own site and competitors.

→ **[Try it on Streamlit Community Cloud](https://share.streamlit.io)** *(link once deployed)*

What it analyses from public data:
- Sitemap structure — how many URLs per section, how deep they are, whether lastmod dates are accurate
- robots.txt — whether crawlers are being guided correctly, whether archive content is consuming crawl budget
- Click depth from the homepage — how many clicks it takes a crawler to reach each section
- Common Crawl coverage — proxy for which sections AI systems have actually ingested
- Competitor comparison — run the same analysis on any other public news site

### 2. Python library (for newsrooms with server log access)

If your newsroom can export Nginx/Apache access logs, the Python library gives you ground-truth data: exactly which bots hit which URLs, how often, and how their attention compares to your editorial priorities.

```bash
pip install -r requirements.txt

python -m crawl_budget_analyzer.cli analyze \
    --log-glob "/var/log/nginx/access.log*" \
    --priority-config example_priority.yaml \
    --site-url yoursite.com \
    --output-dir ./output
```

Output: `output/editorial_briefing.md` — a plain-English report ready to paste into Slack or a strategy doc.

---

## What the output looks like

### Mismatch report (per bot, per section)

| Bot | Section | Your priority | Bot's attention | Verdict |
|---|---|---|---|---|
| Google Search | /opinion/ | 18% | 0% | ⚠️ Significantly under-crawled |
| Google Search | /archive/ | 0.4% | 43% | ⚠️ Significantly over-crawled |
| ChatGPT / OpenAI | /features/ | 12% | 50% | Over-crawled |

### Remediation plan (data-driven, not generic)

```
🔴 Priority 1 — Fix immediately

Suppress archive from consuming crawl budget
Who: Dev / SEO team
What: Add /archive/ Disallow to robots.txt or move to subdomain
Why: Google is spending 43% of crawl time on archive — that budget
     is taken from current editorial content.

Add /opinion/ content to homepage feeds
Who: Editor / Digital team
What: Add 'Latest from Opinion' widget to homepage and cross-links
      from news articles
Why: /opinion/ is rated #2 priority but Google spends 0% of crawl
     time there. No direct link path from high-traffic pages.
```

---

## Who built this and why

Built as an open-source tool to give journalists a seat at the table in conversations about site architecture and content strategy. The decisions that determine whether a story gets indexed — URL structure, sitemap configuration, internal linking — are usually made by developers without editorial input. This tool makes those decisions visible and translates them into editorial language.

---

## Running the web dashboard locally

```bash
git clone https://github.com/onlinejourno/news-crawl-budget-analyzer
cd news-crawl-budget-analyzer/crawl-budget-analyzer
pip install -r webapp/requirements.txt
streamlit run webapp/app.py
```

Opens at `http://localhost:8501`.

---

## Project structure

```
crawl-budget-analyzer/
├── crawl_budget_analyzer/      # Python library (server-log analysis)
│   ├── log_parser.py           # Parse Nginx/Apache logs, filter bot traffic
│   ├── bot_verifier.py         # Reverse-DNS verification (anti-spoofing)
│   ├── diff_engine.py          # Crawl frequency, depth, waste metrics
│   ├── sitemap_audit.py        # Sitemap coverage and lastmod checks
│   ├── priority_config.py      # Editorial priority vs. bot attention mismatch
│   ├── briefing.py             # Plain-English editorial briefing generator
│   ├── remediation.py          # Data-driven action plan with owners
│   ├── gsc_client.py           # Google Search Console API wrapper
│   └── cli.py                  # Command-line interface
│
├── webapp/                     # Streamlit dashboard (public-data)
│   ├── app.py                  # Main dashboard (5 tabs)
│   ├── fetchers.py             # Sitemap, robots.txt, spider, Common Crawl
│   └── audit_log.py            # SQLite log of recent audits
│
├── example_priority.yaml       # Sample editorial priority config
└── requirements.txt            # Python dependencies
```

---

## Data sources used

| Source | What it provides | Access |
|---|---|---|
| sitemap.xml | URL inventory, section structure, lastmod dates | Public |
| robots.txt | Crawl directives, blocked paths | Public |
| Site spider | Click depth from homepage, internal link structure | Public |
| Common Crawl | AI crawler coverage by section | Public API |
| Server access logs | Actual bot behaviour (ground truth) | Requires server access |
| Google Search Console | Crawl Stats, index coverage | Requires GSC access |

---

## Contributing

Issues and PRs welcome. Particularly interested in:
- Additional bot identification (new AI crawlers emerge regularly)
- SERP API integration for Google/Bing indexed page counts
- Support for JSON-format access logs
- Newsroom case studies

---

## Licence

MIT. Use freely, attribution appreciated.

Built by [OnlineJourno](https://onlinejourno.com).
