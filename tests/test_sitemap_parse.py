"""Sitemap parser tests — pure parsing, no network."""

import pytest

from webapp.sitemap_parse import parse_sitemap_xml

URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://x.example/a</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://x.example/b</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.example/sm1.xml</loc></sitemap>
  <sitemap><loc>https://x.example/sm2.xml</loc></sitemap>
</sitemapindex>"""

# Entity-expansion bomb ("billion laughs") — must be rejected, not expanded.
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&lol2;</loc></url>
</urlset>"""

# XXE — external entity pointing at a local file. Must be rejected.
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&xxe;</loc></url>
</urlset>"""


def test_parse_urlset():
    urls, children = parse_sitemap_xml(URLSET)
    assert [u["loc"] for u in urls] == ["https://x.example/a", "https://x.example/b"]
    assert urls[0]["lastmod"] == "2026-01-01"
    assert urls[1]["lastmod"] is None
    assert children == []


def test_parse_index():
    urls, children = parse_sitemap_xml(INDEX)
    assert urls == []
    assert children == ["https://x.example/sm1.xml", "https://x.example/sm2.xml"]


def test_rejects_entity_bomb():
    with pytest.raises(Exception):
        parse_sitemap_xml(BILLION_LAUGHS)


def test_rejects_xxe():
    with pytest.raises(Exception):
        parse_sitemap_xml(XXE)
