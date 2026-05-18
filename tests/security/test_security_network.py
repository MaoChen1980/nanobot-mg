"""Tests for nanobot.security.network — SSRF protection and internal URL detection."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from nanobot.security.network import (
    configure_ssrf_whitelist,
    targets_internal_address,
    validate_resolved_url,
    validate_url_target,
)


def _fake_resolve(host: str, results: list[str]):
    """Return a getaddrinfo mock that maps the given host to fake IP results (sync)."""
    def _resolver(hostname, port, family=0, type_=0):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


def _fake_resolve_async(host: str, results: list[str]):
    """Return an async _resolve_hostname mock for the given host."""
    async def _resolver(hostname: str):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")
    return _resolver


# ---------------------------------------------------------------------------
# validate_url_target — scheme / domain basics
# ---------------------------------------------------------------------------

async def test_rejects_non_http_scheme():
    ok, err = await validate_url_target("ftp://example.com/file")
    assert not ok
    assert "http" in err.lower()


async def test_rejects_missing_domain():
    ok, err = await validate_url_target("http://")
    assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — blocked private/internal IPs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("127.0.0.2", "loopback_alt"),
    ("10.0.0.1", "rfc1918_10"),
    ("172.16.5.1", "rfc1918_172"),
    ("192.168.1.1", "rfc1918_192"),
    ("169.254.169.254", "metadata"),
    ("0.0.0.0", "zero"),
])
async def test_blocks_private_ipv4(ip: str, label: str):
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("evil.com", [ip])):
        ok, err = await validate_url_target(f"http://evil.com/path")
        assert not ok, f"Should block {label} ({ip})"
        assert "private" in err.lower() or "blocked" in err.lower()


async def test_blocks_ipv6_loopback():
    async def _resolver(hostname: str):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
    with patch("nanobot.security.network._resolve_hostname", _resolver):
        ok, err = await validate_url_target("http://evil.com/")
        assert not ok


# ---------------------------------------------------------------------------
# validate_url_target — allows public IPs
# ---------------------------------------------------------------------------

async def test_allows_public_ip():
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("example.com", ["93.184.216.34"])):
        ok, err = await validate_url_target("http://example.com/page")
        assert ok, f"Should allow public IP, got: {err}"


async def test_allows_normal_https():
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("github.com", ["140.82.121.3"])):
        ok, err = await validate_url_target("https://github.com/HKUDS/nanobot")
        assert ok


# ---------------------------------------------------------------------------
# targets_internal_address — shell command scanning
# ---------------------------------------------------------------------------

def test_detects_curl_metadata():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("169.254.169.254", ["169.254.169.254"])):
        assert targets_internal_address('curl -s http://169.254.169.254/computeMetadata/v1/')


def test_detects_wget_localhost():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("localhost", ["127.0.0.1"])):
        assert targets_internal_address("wget http://localhost:8080/secret")


def test_allows_normal_curl():
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve("example.com", ["93.184.216.34"])):
        assert not targets_internal_address("curl https://example.com/api/data")


def test_no_urls_returns_false():
    assert not targets_internal_address("echo hello && ls -la")


# ---------------------------------------------------------------------------
# SSRF whitelist — allow specific CIDR ranges (#2669)
# ---------------------------------------------------------------------------

async def test_blocks_cgnat_by_default():
    """100.64.0.0/10 (CGNAT / Tailscale) is blocked by default."""
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("ts.local", ["100.100.1.1"])):
        ok, _ = await validate_url_target("http://ts.local/api")
        assert not ok


async def test_whitelist_allows_cgnat():
    """Whitelisting 100.64.0.0/10 lets Tailscale addresses through."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("ts.local", ["100.100.1.1"])):
            ok, err = await validate_url_target("http://ts.local/api")
            assert ok, f"Whitelisted CGNAT should be allowed, got: {err}"
    finally:
        configure_ssrf_whitelist([])


async def test_whitelist_does_not_affect_other_blocked():
    """Whitelisting CGNAT must not unblock other private ranges."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("evil.com", ["10.0.0.1"])):
            ok, _ = await validate_url_target("http://evil.com/secret")
            assert not ok
    finally:
        configure_ssrf_whitelist([])


async def test_whitelist_invalid_cidr_ignored():
    """Invalid CIDR entries are silently skipped."""
    configure_ssrf_whitelist(["not-a-cidr", "100.64.0.0/10"])
    try:
        with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("ts.local", ["100.100.1.1"])):
            ok, _ = await validate_url_target("http://ts.local/api")
            assert ok
    finally:
        configure_ssrf_whitelist([])


# ---------------------------------------------------------------------------
# validate_resolved_url — post-redirect validation
# ---------------------------------------------------------------------------

async def test_validate_resolved_url_parse_error_returns_ok():
    """If URL parsing fails, return (True, '')."""
    ok, err = await validate_resolved_url("://invalid-url")
    assert ok
    assert err == ""


async def test_validate_resolved_url_no_hostname_returns_ok():
    ok, err = await validate_resolved_url("http://")
    assert ok
    assert err == ""


async def test_validate_resolved_url_blocks_private_ip():
    """Direct private IP in hostname is blocked."""
    ok, err = await validate_resolved_url("http://127.0.0.1/secret")
    assert not ok
    assert "private" in err.lower()


async def test_validate_resolved_url_allows_public_ip():
    ok, err = await validate_resolved_url("http://93.184.216.34/page")
    assert ok


async def test_validate_resolved_url_domain_resolves_to_private():
    """Domain that resolves to private IP is blocked."""
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("internal.local", ["10.0.0.5"])):
        ok, err = await validate_resolved_url("http://internal.local/page")
    assert not ok
    assert "private" in err.lower()


async def test_validate_resolved_url_domain_resolves_to_public():
    """Domain that resolves to public IP is allowed."""
    with patch("nanobot.security.network._resolve_hostname", _fake_resolve_async("example.com", ["93.184.216.34"])):
        ok, err = await validate_resolved_url("http://example.com/page")
    assert ok


async def test_validate_resolved_url_dns_failure_returns_ok():
    """If DNS resolution fails, allow by default."""
    with patch("nanobot.security.network._resolve_hostname", side_effect=socket.gaierror("no such host")):
        ok, err = await validate_resolved_url("http://unknown.example/path")
    assert ok


async def test_validate_resolved_url_ipv6_private_blocked():
    async def _resolver(hostname: str):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0))]
    with patch("nanobot.security.network._resolve_hostname", _resolver):
        ok, err = await validate_resolved_url("http://evil.com/")
        assert not ok


async def test_validate_resolved_url_whitelist_cgnat():
    """Whitelisted CGNAT bypasses private check for IP literal."""
    configure_ssrf_whitelist(["100.64.0.0/10"])
    try:
        ok, err = await validate_resolved_url("http://100.100.1.1/api")
        assert ok
    finally:
        configure_ssrf_whitelist([])
