"""The spider crawls past the homepage.

Observed 2026-11-18 on a live news site: a homepage with 91 internal links
produced one crawled page. The loop's limit counted URLs *queued*, so the
homepage's links filled the quota before a second page was fetched. Every
site whose homepage links to more than ``max_pages`` URLs was crawled to a
depth of zero.
"""
from webapp import fetchers


class _Resp:
    def __init__(self, html):
        self.text = html
        self.headers = {"content-type": "text/html"}
        self.status_code = 200


def _site(n_links):
    home = "".join(f'<a href="/story-{i}/">s</a>' for i in range(n_links))
    pages = {"https://news.example": home}
    for i in range(n_links):
        pages[f"https://news.example/story-{i}/"] = '<a href="/">home</a>'
    return pages


def _serve(monkeypatch, pages, fetched):
    def fake_get(url, **kw):
        fetched.append(url)
        return _Resp(pages[url])

    monkeypatch.setattr(fetchers, "safe_get", fake_get)
    monkeypatch.setattr(fetchers.time, "sleep", lambda s: None)


def test_a_homepage_with_more_links_than_the_limit_is_still_crawled_past(monkeypatch):
    fetched = []
    _serve(monkeypatch, _site(91), fetched)
    df = fetchers.spider_depth("https://news.example", max_pages=15)
    assert len(fetched) == 15
    assert len(df) == 15
    assert (df["depth"] == 1).sum() == 14


def test_the_limit_still_caps_fetches_on_a_small_site(monkeypatch):
    fetched = []
    _serve(monkeypatch, _site(3), fetched)
    df = fetchers.spider_depth("https://news.example", max_pages=15)
    assert len(fetched) == 4
    assert len(df) == 4
