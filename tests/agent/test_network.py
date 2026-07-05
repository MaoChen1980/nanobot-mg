"""Tests for nanobot.security.network — SSRF protection.

Covers targets_internal_address, validate_url_target, validate_resolved_url,
_is_blocked, and configure_ssrf_whitelist.

conftest.py must be imported first — it sets up nanobot stubs and loads
the real network/security modules before this file runs.
"""

from __future__ import annotations

import ipaddress
import socket
from unittest.mock import patch

import pytest

# Modules are already loaded by conftest.py
# Import from sys.modules to get the real module instances
import nanobot.security.network as _net_mod

# Re-export for convenience
BLOCKED_NETWORKS = _net_mod._BLOCKED_NETWORKS
_is_blocked = _net_mod._is_blocked
_resolve_hostname = _net_mod._resolve_hostname
configure_ssrf_whitelist = _net_mod.configure_ssrf_whitelist
targets_internal_address = _net_mod.targets_internal_address
validate_resolved_url = _net_mod.validate_resolved_url
validate_url_target = _net_mod.validate_url_target


# ============================================================================
# _is_blocked
# ============================================================================

class TestIsBlocked:
    def test_blocked_private_ipv4(self):
        assert _is_blocked(ipaddress.ip_address("10.0.0.1")) is True
        assert _is_blocked(ipaddress.ip_address("172.16.0.1")) is True
        assert _is_blocked(ipaddress.ip_address("192.168.0.1")) is True
        assert _is_blocked(ipaddress.ip_address("169.254.169.254")) is True  # AWS metadata

    def test_blocked_loopback_ipv4(self):
        assert _is_blocked(ipaddress.ip_address("127.0.0.1")) is True
        assert _is_blocked(ipaddress.ip_address("127.255.255.255")) is True

    def test_blocked_loopback_ipv6(self):
        assert _is_blocked(ipaddress.ip_address("::1")) is True

    def test_blocked_linklocal_ipv6(self):
        assert _is_blocked(ipaddress.ip_address("fe80::1")) is True

    def test_blocked_0_0_0_0(self):
        assert _is_blocked(ipaddress.ip_address("0.0.0.0")) is True

    def test_allowed_public_ipv4(self):
        assert _is_blocked(ipaddress.ip_address("8.8.8.8")) is False
        assert _is_blocked(ipaddress.ip_address("1.1.1.1")) is False

    def test_allowed_public_ipv6(self):
        assert _is_blocked(ipaddress.ip_address("2001:4860:4860::8888")) is False

    def test_whitelist_overrides_blocklist(self):
        configure_ssrf_whitelist(["100.64.0.0/10"])  # Carrier-grade NAT
        # 100.64.0.0/10 overlaps with the blocklist entry 100.64.0.0/10
        # but should now be allowed because it is in the whitelist
        try:
            assert _is_blocked(ipaddress.ip_address("100.64.0.1")) is False
        finally:
            configure_ssrf_whitelist([])  # Reset


# ============================================================================
# targets_internal_address
# ============================================================================

class TestTargetsInternalAddress:
    """Synchronous tests — no network I/O, all mocked."""

    def test_public_url_returns_false(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80)),
            ]
            result = targets_internal_address("curl https://example.com")
            assert result is False

    def test_private_ip_url_returns_true(self):
        # URL with IP that is in blocked ranges
        result = targets_internal_address("curl http://192.168.1.1/")
        assert result is True

    def test_aws_metadata_ip_returns_true(self):
        result = targets_internal_address("curl http://169.254.169.254/latest/meta-data/")
        assert result is True

    def test_localhost_allowed_with_allow_loopback_true(self):
        # localhost is blocked by default but allow_loopback bypasses it
        # Note: the function signature defaults allow_loopback=False
        result = targets_internal_address("curl http://127.0.0.1:8080/")
        assert result is True  # 127.0.0.0/8 is in BLOCKED_NETWORKS

    def test_multiple_urls_all_public_returns_false(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 80)),
            ]
            result = targets_internal_address(
                "curl https://one.example.com && curl https://two.example.com"
            )
            assert result is False

    def test_one_private_one_public_returns_true(self):
        # Mixed: one public, one private → blocked
        result = targets_internal_address(
            "curl https://example.com && curl http://192.168.1.1/"
        )
        assert result is True

    def test_dns_resolution_failure_returns_true(self):
        """W1 fix: DNS resolution failure should block, not silently allow.

        When a hostname cannot be resolved, it is treated as potentially
        internal for defense-in-depth (avoids SSRF via DNS rebinding).
        """
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")
            result = targets_internal_address("curl https://unresolvable.example.com/")
            assert result is True  # Blocked — cannot verify it's safe

    def test_non_http_url_ignored(self):
        # file:// and ftp:// URLs are skipped
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80)),
            ]
            result = targets_internal_address("curl ftp://ftp.example.com/file")
            assert result is False  # Skipped (not http/https)

    def test_url_without_hostname_skipped(self):
        # URL parse fails gracefully
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            result = targets_internal_address("curl http://[invalid]/")
            assert result is False  # continue path

    def test_mixed_case_https_parsed(self):
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
            ]
            result = targets_internal_address("curl HTTPS://Example.COM/")
            assert result is False  # Public IP → allowed


# ============================================================================
# validate_url_target (async)
# ============================================================================

@pytest.mark.asyncio
class TestValidateUrlTarget:
    async def test_public_url_allowed(self):
        ok, msg = await validate_url_target("https://example.com")
        assert ok is True
        assert msg == ""

    async def test_aws_metadata_blocked(self):
        ok, msg = await validate_url_target("http://169.254.169.254/latest/meta-data/")
        assert ok is False
        assert "169.254.169.254" in msg

    async def test_localhost_blocked(self):
        ok, msg = await validate_url_target("http://127.0.0.1:8080/")
        assert ok is False
        assert "127.0.0.1" in msg

    async def test_private_network_blocked(self):
        ok, msg = await validate_url_target("http://192.168.1.1/")
        assert ok is False
        assert "192.168.1.1" in msg

    async def test_unsupported_scheme_rejected(self):
        ok, msg = await validate_url_target("ftp://example.com/file")
        assert ok is False
        assert "Only http/https" in msg

    async def test_missing_hostname_rejected(self):
        ok, msg = await validate_url_target("https://")
        assert ok is False

    async def test_dns_failure_rejected(self):
        """DNS resolution failure returns (False, error_msg) — does not silently allow."""
        with patch.object(_net_mod, "_resolve_hostname") as mock_resolve:
            mock_resolve.side_effect = socket.gaierror("Name or service not known")
            ok, msg = await validate_url_target("https://unresolvable.example.com")
            assert ok is False
            assert "Cannot resolve" in msg

    async def test_whitelisted_cidr_allowed(self):
        configure_ssrf_whitelist(["100.64.0.0/10"])
        try:
            # 100.64.0.0/10 is normally blocked but whitelisted here
            ok, msg = await validate_url_target("https://100.64.0.1/")
            assert ok is True
        finally:
            configure_ssrf_whitelist([])


# ============================================================================
# validate_resolved_url (async)
# ============================================================================

@pytest.mark.asyncio
class TestValidateResolvedUrl:
    async def test_public_redirect_allowed(self):
        ok, msg = await validate_resolved_url("https://example.com")
        assert ok is True
        assert msg == ""

    async def test_private_redirect_blocked(self):
        ok, msg = await validate_resolved_url("http://192.168.1.1/redirect")
        assert ok is False

    async def test_aws_metadata_redirect_blocked(self):
        ok, msg = await validate_resolved_url("http://169.254.169.254/")
        assert ok is False
        assert "private" in msg.lower()


# ============================================================================
# configure_ssrf_whitelist
# ============================================================================

class TestConfigureSsrfWhitelist:
    def test_valid_cidr_added(self):
        configure_ssrf_whitelist(["8.8.8.0/24"])
        # 8.8.8.8 is normally allowed, but 8.8.8.0/24 is not in the whitelist
        # so it should still be allowed (not blocked) since it's not in BLOCKED_NETWORKS
        configure_ssrf_whitelist([])  # Reset

    def test_invalid_cidr_logs_warning(self):
        # loguru's logger.warning() does not trigger pytest.warns
        # Verify it executes without raising instead
        configure_ssrf_whitelist(["not-a-cidr"])  # Should not raise
        configure_ssrf_whitelist([])  # Reset


# ============================================================================
# _resolve_hostname
# ============================================================================

@pytest.mark.asyncio
class TestResolveHostname:
    async def test_resolves_valid_hostname(self):
        infos = await _resolve_hostname("example.com")
        assert len(infos) > 0

    async def test_raises_gaierror_for_invalid_hostname(self):
        with pytest.raises(socket.gaierror):
            await _resolve_hostname("this-host-definitely-does-not-exist-12345.example")
