"""robots.txt must actually disallow the analysis endpoints."""

from webapp.robots import ROBOTS_BODY


def test_disallows_everything():
    assert "User-agent: *" in ROBOTS_BODY
    assert "Disallow: /" in ROBOTS_BODY


def test_no_blanket_allow():
    """`Allow: /` is what the tools front-end shipped; it must not appear here."""
    assert "Allow:" not in ROBOTS_BODY


def test_ends_with_a_newline():
    assert ROBOTS_BODY.endswith("\n")
