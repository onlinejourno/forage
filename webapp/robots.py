"""robots.txt for the Forage API host.

Every crawl of an analysis endpoint is a free run: a sitemap fetch, a shallow
spider and a Common Crawl query, all on someone else's behalf. This host serves
no human-readable page — the UI lives in the tools front-end and calls here — so
there is nothing on it worth indexing and the whole host is disallowed.

This stops POLITE crawlers only. It is the cheap layer, not the defence: a
scripted abuser ignores robots.txt entirely, which is what the rate-limit key in
``client_ip.py`` is for.
"""

from __future__ import annotations

ROBOTS_BODY = (
    "User-agent: *\n"
    "Disallow: /\n"
)
