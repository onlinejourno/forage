"""CLI entry point.

Example:
    python -m crawl_budget_analyzer.cli analyze \
        --log-glob "/var/log/nginx/access.log*" \
        --sitemap https://example.com/sitemap-news.xml \
        --publish-csv articles.csv \
        --priority-config example_priority.yaml
"""

import click
import pandas as pd

from . import briefing, diff_engine, log_parser, priority_config, remediation, sitemap_audit


@click.group()
def cli():
    pass


@cli.command()
@click.option("--log-glob", required=True, help="Path or glob to raw access log(s)")
@click.option("--sitemap", "sitemap_url", default=None, help="Sitemap URL to audit coverage against")
@click.option("--publish-csv", default=None, help="CSV with columns: url,published_at")
@click.option("--priority-config", "priority_cfg", default=None, help="YAML/JSON with section ranks and target bots")
@click.option("--site-url", default="your site", help="Site URL or name, used in briefing header")
@click.option("--output-dir", default="./output")
def analyze(log_glob, sitemap_url, publish_csv, priority_cfg, site_url, output_dir):
    import os
    os.makedirs(output_dir, exist_ok=True)

    click.echo("Parsing logs...")
    raw = log_parser.parse_logs(log_glob, f"{output_dir}/parsed_logs.csv")
    bots = log_parser.filter_bot_traffic(raw)
    bots["bot_name"] = bots["user_agent"].apply(log_parser.bot_name)
    click.echo(f"  {len(bots)} bot requests out of {len(raw)} total")

    click.echo("Crawl frequency by section:")
    freq = diff_engine.crawl_frequency_by_section(bots)
    click.echo(freq.to_string())
    freq.to_csv(f"{output_dir}/crawl_frequency_by_section.csv")

    click.echo("\nDepth vs crawl attention:")
    depth = diff_engine.depth_vs_crawl_attention(bots)
    click.echo(depth.to_string())
    depth.to_csv(f"{output_dir}/depth_vs_crawl_attention.csv")

    click.echo("\nCrawl waste summary:")
    waste = diff_engine.crawl_waste(bots)
    click.echo(waste.to_string(index=False))
    waste.to_csv(f"{output_dir}/crawl_waste.csv", index=False)

    if sitemap_url:
        click.echo("\nAuditing sitemap coverage...")
        sm = sitemap_audit.load_sitemap(sitemap_url)
        gap = sitemap_audit.coverage_gap(sm, bots["url"])
        click.echo(f"  Sitemap URLs: {gap['sitemap_count']}, Crawled URLs: {gap['crawled_count']}")
        click.echo(f"  In sitemap but never crawled: {len(gap['in_sitemap_not_crawled'])}")
        click.echo(f"  Crawled but missing from sitemap: {len(gap['crawled_not_in_sitemap'])}")

    if publish_csv:
        click.echo("\nComputing time-to-first-crawl...")
        publish_df = pd.read_csv(publish_csv)
        lag = diff_engine.time_to_first_crawl(bots, publish_df)
        click.echo(lag.groupby("section")["lag_minutes"].describe().to_string())
        lag.to_csv(f"{output_dir}/time_to_first_crawl.csv", index=False)

    if priority_cfg:
        click.echo("\nPriority vs. attention mismatch:")
        cfg = priority_config.load_config(priority_cfg)
        mismatch = priority_config.mismatch_report(bots, cfg)
        click.echo(mismatch.to_string(index=False))
        mismatch.to_csv(f"{output_dir}/mismatch_report.csv", index=False)

        click.echo("\nTop under-crawled sections per bot (highest-priority IA fixes):")
        problems = priority_config.top_problems(mismatch)
        click.echo(problems[["bot", "section", "editorial_priority_share",
                              "crawl_attention_share", "mismatch_label"]].to_string(index=False))

        click.echo("\nGenerating remediation plan...")
        plan = remediation.build_remediation_plan(mismatch, depth, waste, freq)
        plan_md = remediation.format_remediation_md(plan, site_url=site_url)
        if not plan.empty:
            plan.to_csv(f"{output_dir}/remediation_plan.csv", index=False)

        click.echo("\nGenerating editorial briefing...")
        md = briefing.generate_briefing(mismatch, site_url=site_url)
        md += "\n\n" + plan_md
        briefing_path = f"{output_dir}/editorial_briefing.md"
        with open(briefing_path, "w") as f:
            f.write(md)
        click.echo(f"  Briefing written to {briefing_path}")
        click.echo("\n" + plan_md)

    click.echo(f"\nDone. Output written to {output_dir}/")


if __name__ == "__main__":
    cli()
