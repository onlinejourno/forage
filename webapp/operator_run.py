"""Run Forage on one site as the operator, with the site's door open.

    python -m webapp.operator_run https://news.example

The only path that sends FETCH_HOST_HEADERS (see ssrf.operator_door). Run it
on the Forage machine (`fly ssh console`), where the secret lives. The result
is stored as the site's latest run, which the signed hub endpoint reads.

Exit codes: 0 stored, 1 analysis or store failed, 2 URL refused. Every
failure prints why; nothing here is swallowed.
"""
from __future__ import annotations

import sys

from webapp import api, store
from webapp.ssrf import UnsafeURLError, operator_door, validate_public_url


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m webapp.operator_run <site-url>", file=sys.stderr)
        return 2
    url = argv[0].strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        validate_public_url(url)
    except UnsafeURLError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    req = api.AnalyseRequest(url=url, competitors=[], priority=[])
    try:
        with operator_door():
            result = api._build_result(req, lambda step: print(step, file=sys.stderr))
        store.save_result(url, result)
    except Exception as exc:  # noqa: BLE001 - an operator command reports every failure
        print(f"failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"stored {url}: {len(result['sitemap_summary'])} sitemap sections, "
        f"{len(result['depth_summary'])} crawled sections, "
        f"{len(result['mismatch'])} mismatch rows"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
