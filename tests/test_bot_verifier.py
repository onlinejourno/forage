"""Forward-confirm must compare the client IP against the RESOLVED IP LIST,
never against the PTR hostname string (an attacker controls their own PTR)."""
import socket

import pytest

from crawl_budget_analyzer.bot_verifier import _reverse_then_forward_confirm

GOOGLE_SUFFIXES = (".googlebot.com", ".google.com")


def test_legit_bot_confirms(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("crawl-66-249-66-1.googlebot.com", [], [ip]))
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda host: (host, [], ["66.249.66.1"]))
    assert _reverse_then_forward_confirm("66.249.66.1", GOOGLE_SUFFIXES) is True


def test_spoofed_ptr_fails_forward_confirm(monkeypatch):
    # Attacker sets their PTR to a real googlebot.com name; forward-resolving
    # that name yields Google's IPs, not the attacker's.
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("crawl-66-249-66-1.googlebot.com", [], [ip]))
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda host: (host, [], ["66.249.66.1"]))
    assert _reverse_then_forward_confirm("203.0.113.7", GOOGLE_SUFFIXES) is False


def test_hostname_containing_ip_text_is_not_a_match(monkeypatch):
    # Regression for the hostname-substring bug: an IP appearing textually in
    # the PTR hostname must not count as confirmation.
    monkeypatch.setattr(
        socket, "gethostbyaddr", lambda ip: ("203.0.113.7.fake.googlebot.com", [], [ip])
    )
    monkeypatch.setattr(
        socket, "gethostbyname_ex", lambda host: ("203.0.113.7.fake.googlebot.com", [], ["66.249.66.1"])
    )
    assert _reverse_then_forward_confirm("203.0.113.7", GOOGLE_SUFFIXES) is False


def test_wrong_suffix_fails(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: ("bot.attacker.com", [], [ip]))
    assert _reverse_then_forward_confirm("203.0.113.7", GOOGLE_SUFFIXES) is False


def test_dns_failure_fails_closed(monkeypatch):
    def boom(ip):
        raise socket.herror("no PTR")

    monkeypatch.setattr(socket, "gethostbyaddr", boom)
    assert _reverse_then_forward_confirm("203.0.113.7", GOOGLE_SUFFIXES) is False
