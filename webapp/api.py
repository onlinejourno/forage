"""Crawl-Budget Analyser — JSON API over the existing public-data analysis.

Wraps the proven `fetchers` logic (sitemap, robots.txt, shallow spider, Common
Crawl) and returns ready-to-render JSON for the OnlineJourno Tools front-end.

The analysis is slow (spider + Common Crawl with polite delays), so it runs as a
background job: POST /api/analyse returns a job_id; the client polls
GET /api/analyse/{job_id} until status == "done". State is in-memory (single
machine) — no database, matching the no-login, public-data design.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from webapp import fetchers
from webapp.ssrf import UnsafeURLError, validate_public_url

app = FastAPI(title="Crawl-Budget Analyser API", version="1.0.0")

# Public read-only tool; allow any origin (the front-end proxies it server-side,
# but direct calls are harmless).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Per-IP rate limiting — the analysis endpoint is expensive (spider + Common Crawl).
# Behind Fly's proxy the real client IP is in Fly-Client-IP / X-Forwarded-For.
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

DEFAULT_PRIORITY = [
    "news", "opinion", "features", "business",
    "sport", "entertainment", "sci-tech", "archive",
]

# job_id -> {"status": "running"|"done"|"error", "step": str, "result": dict|None, "error": str|None}
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2)
JOB_TTL = 3600          # seconds — evict finished/stale jobs after an hour
MAX_COMPETITORS = 5     # cap fan-out: each competitor triggers a full crawl


def _prune_jobs() -> None:
    """Drop jobs older than JOB_TTL. Caller must hold _jobs_lock."""
    cutoff = time.time() - JOB_TTL
    for jid in [j for j, v in JOBS.items() if v.get("created_at", 0) < cutoff]:
        del JOBS[jid]


class AnalyseRequest(BaseModel):
    url: str
    competitors: list[str] = []
    priority: list[str] = DEFAULT_PRIORITY


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _clean(v):
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else round(float(v), 2)
    if isinstance(v, float):
        return None if pd.isna(v) else round(v, 2)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].astype(str)
    return [{k: _clean(v) for k, v in row.items()} for row in d.to_dict(orient="records")]


# ---------------------------------------------------------------------------
# Analysis (ports webapp/app.py's _run_site + the per-tab computations)
# ---------------------------------------------------------------------------

def _run_site(url: str, set_step) -> dict:
    res: dict = {}
    set_step("Fetching sitemap…")
    sm_df = fetchers.fetch_sitemap(url)
    res["sitemap_summary"] = fetchers.sitemap_section_summary(sm_df)

    set_step("Checking robots.txt…")
    res["robots"] = fetchers.fetch_robots(url)
    res["robots_issues"] = fetchers.robots_issues(res["robots"])

    set_step("Spidering the site (up to 60 pages)…")
    spider_df = fetchers.spider_depth(url, max_pages=60)
    res["depth_summary"] = fetchers.depth_by_section(spider_df)

    sm = res["sitemap_summary"]
    if not sm.empty:
        sections = sm["section"].tolist()[:10]
        set_step("Checking Common Crawl coverage…")
        res["cc"] = fetchers.common_crawl_coverage(url, sections)
    else:
        res["cc"] = pd.DataFrame()
    return res


def _mismatch(sm: pd.DataFrame, dd: pd.DataFrame, ranks: dict, n_priority: int) -> list[dict]:
    if sm.empty or dd.empty:
        return []
    merged = sm[["section", "url_count", "avg_depth"]].merge(
        dd[["section", "avg_depth"]].rename(columns={"avg_depth": "spider_depth"}),
        on="section", how="outer",
    )
    merged["editorial_rank"] = merged["section"].map(ranks).fillna(99).astype(int)

    def problem(r):
        rank = r["editorial_rank"]
        sd = r.get("spider_depth") or 0
        if rank <= 3 and sd > 2:
            return "High priority, deep URL"
        if rank >= n_priority - 1 and sd <= 1:
            return "Low priority but shallow"
        return "OK"

    merged["problem"] = merged.apply(problem, axis=1)
    merged = merged.sort_values("editorial_rank")
    return _records(merged[["section", "editorial_rank", "url_count", "avg_depth", "spider_depth", "problem"]])


def _competitor_rows(all_sites: dict) -> list[dict]:
    rows = []
    for url, res in all_sites.items():
        sm = res["sitemap_summary"]
        rob = res["robots"]
        rows.append({
            "site": url,
            "total_sitemap_urls": int(sm["url_count"].sum()) if not sm.empty else 0,
            "sections_in_sitemap": int(len(sm)) if not sm.empty else 0,
            "avg_url_depth": round(float(sm["avg_depth"].mean()), 1) if not sm.empty else None,
            "sitemaps_in_robots": len(rob["sitemaps_declared"]),
            "archive_blocked": any("archive" in p.lower() for p in rob["disallowed_patterns"]),
            "search_params_blocked": any("?" in p or "search" in p.lower() for p in rob["disallowed_patterns"]),
        })
    return rows


def _briefing(url: str, main: dict, ranks: dict) -> str:
    sm = main["sitemap_summary"]
    issues = main["robots_issues"]
    dd = main["depth_summary"]
    cc = main["cc"]

    lines = [
        f"# Bot Crawl Briefing — {url}",
        "",
        "## Why this matters",
        "Search engines and AI assistants don't see your site the way readers do. "
        "They follow links, read sitemaps, and decide which sections deserve attention "
        "based on your site's structure — not editorial judgment. This briefing shows "
        "where those decisions are working against you.",
        "",
        "## Sitemap health",
    ]
    if sm.empty:
        lines.append("⚠️ No sitemap found. Critical — crawlers cannot reliably discover content.")
    else:
        total = int(sm["url_count"].sum())
        lines.append(f"Total URLs in sitemap: **{total:,}** across {len(sm)} sections.")
        if "archive" in sm["section"].values:
            archive_pct = sm[sm["section"] == "archive"]["url_count"].values[0] / total * 100
            if archive_pct > 15:
                lines.append(
                    f"\n⚠️ **{archive_pct:.0f}% of sitemap URLs are archive content.** "
                    "Crawlers spending time on archive pages cannot crawl fresh editorial content instead."
                )

    lines += ["", "## URL depth by section"]
    if not dd.empty:
        for _, row in dd.sort_values("avg_depth", ascending=False).iterrows():
            rank = ranks.get(row["section"], 99)
            if rank <= 5 and row["avg_depth"] > 2:
                lines.append(
                    f"- **{str(row['section']).title()}** (priority #{rank}): avg {row['avg_depth']:.1f} "
                    "clicks from homepage — crawlers deprioritise content beyond 2 clicks."
                )

    lines += ["", "## robots.txt"]
    for level, msg in issues:
        lines.append(f"{'⚠️' if level == 'warning' else 'ℹ️'} {msg}")
    if not issues:
        lines.append("✅ No major robots.txt issues.")

    if not cc.empty:
        lines += ["", "## AI crawler coverage (Common Crawl)"]
        low = cc[cc["cc_url_count"] < 5]
        for _, row in low.iterrows():
            rank = ranks.get(row["section"], 99)
            if rank <= 4:
                lines.append(
                    f"- **{str(row['section']).title()}** has only {int(row['cc_url_count'])} URLs in "
                    "Common Crawl (used by AI training datasets). This content may be absent from AI answers."
                )

    lines += [
        "",
        "## What to ask the team",
        "1. **Dev team:** Flatten URL depth for high-priority sections to max 2 path segments.",
        "2. **Dev/SEO team:** Add `/archive/` Disallow to robots.txt to redirect crawl budget to fresh content.",
        "3. **SEO team:** Create section-specific sitemaps with accurate `lastmod` dates.",
        "4. **Editor:** Add 'Latest from Opinion/Features' widgets to the homepage so crawlers discover these sections.",
        "",
        "*Generated by OnlineJourno Crawl-Budget Analyser — data from public sources (sitemap, robots.txt, Common Crawl).*",
    ]
    return "\n".join(lines)


def _build_result(req: AnalyseRequest, set_step) -> dict:
    priority = [s.strip().lower() for s in req.priority if s.strip()] or DEFAULT_PRIORITY
    ranks = {s: i + 1 for i, s in enumerate(priority)}

    main = _run_site(req.url, set_step)

    comp_results = {}
    for i, cu in enumerate(req.competitors):
        set_step(f"Analysing competitor {i + 1}/{len(req.competitors)}…")
        comp_results[cu] = _run_site(cu, lambda s: None)

    all_sites = {req.url: main, **comp_results}

    return {
        "site": req.url,
        "priority": priority,
        "sitemap_summary": _records(main["sitemap_summary"]),
        "depth_summary": _records(main["depth_summary"]),
        "mismatch": _mismatch(main["sitemap_summary"], main["depth_summary"], ranks, len(priority)),
        "robots": main["robots"],
        "robots_issues": [{"level": lv, "message": m} for lv, m in main["robots_issues"]],
        "cc": _records(main["cc"]),
        "competitors": _competitor_rows(all_sites) if req.competitors else [],
        "briefing": _briefing(req.url, main, ranks),
    }


def _run_job(job_id: str, req: AnalyseRequest):
    def set_step(step: str):
        with _jobs_lock:
            if job_id in JOBS:
                JOBS[job_id]["step"] = step

    try:
        result = _build_result(req, set_step)
        with _jobs_lock:
            if job_id in JOBS:
                JOBS[job_id].update({"status": "done", "step": "Done", "result": result, "error": None})
    except Exception as exc:  # noqa: BLE001 — surface any analysis failure to the client
        with _jobs_lock:
            if job_id in JOBS:
                JOBS[job_id].update({"status": "error", "step": None, "result": None, "error": str(exc)})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/analyse")
@limiter.limit("10/minute")
def analyse(request: Request, req: AnalyseRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        validate_public_url(url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=f"refused: {exc}")
    req.url = url

    # Cap fan-out and drop any competitor that isn't a safe public URL.
    safe_competitors = []
    for comp in req.competitors[:MAX_COMPETITORS]:
        comp = comp.strip()
        if not comp:
            continue
        if not comp.startswith(("http://", "https://")):
            comp = "https://" + comp
        try:
            validate_public_url(comp)
        except UnsafeURLError:
            continue
        safe_competitors.append(comp)
    req.competitors = safe_competitors

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _prune_jobs()
        JOBS[job_id] = {"status": "running", "step": "Starting…", "result": None, "error": None, "created_at": time.time()}
    _pool.submit(_run_job, job_id, req)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/analyse/{job_id}")
def job_status(job_id: str):
    with _jobs_lock:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
