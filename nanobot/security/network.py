"""Network security utilities — SSRF protection and internal URL detection."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse
from loguru import logger

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),   # carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),          # unique local
    ipaddress.ip_network("fe80::/10"),         # link-local v6
]

_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)

_allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []


def configure_ssrf_whitelist(cidrs: list[str]) -> None:
    """Allow specific CIDR ranges to bypass SSRF blocking (e.g. Tailscale's 100.64.0.0/10)."""
    global _allowed_networks
    nets = []
    for cidr in cidrs:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("Invalid CIDR notation in network whitelist: {}", cidr)
    _allowed_networks = nets


def _is_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if _allowed_networks and any(addr in net for net in _allowed_networks):
        return False
    return any(addr in net for net in _BLOCKED_NETWORKS)


async def _resolve_hostname(hostname: str) -> list:
    """Async DNS resolution with 10s timeout."""
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(
                hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            ),
            timeout=10.0,
        )
        return infos
    except asyncio.TimeoutError:
        raise socket.gaierror(f"DNS resolution timed out for {hostname}")


async def validate_url_target(url: str) -> tuple[bool, str]:
    """Validate a URL is safe to fetch: scheme, hostname, and resolved IPs.

    Returns (ok, error_message).  When ok is True, error_message is empty.
    """
    try:
        p = urlparse(url)
    except Exception as e:
        return False, str(e)

    if p.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
    if not p.netloc:
        return False, "Missing domain"

    hostname = p.hostname
    if not hostname:
        return False, "Missing hostname"

    try:
        infos = await _resolve_hostname(hostname)
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked(addr):
            return False, f"Blocked: {hostname} resolves to private/internal address {addr}"

    return True, ""


async def validate_resolved_url(url: str) -> tuple[bool, str]:
    """Validate an already-fetched URL (e.g. after redirect). Only checks the IP, skips DNS."""
    try:
        p = urlparse(url)
    except Exception as e:
        logger.warning("Failed to parse URL in validate_resolved_url: {} ({})", url, e)
        return False, f"Invalid URL: {e}"

    hostname = p.hostname
    if not hostname:
        return True, ""

    try:
        addr = ipaddress.ip_address(hostname)
        if _is_blocked(addr):
            return False, f"Redirect target is a private address: {addr}"
    except ValueError:
        # hostname is a domain name, resolve it
        try:
            infos = await _resolve_hostname(hostname)
        except socket.gaierror:
            return True, ""
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if _is_blocked(addr):
                return False, f"Redirect target {hostname} resolves to private address {addr}"

    return True, ""


_LOOPBACK_NETWORKS = {
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
}


def targets_internal_address(command: str, allow_loopback: bool = False) -> bool:
    """Return True if the command string contains a URL targeting an internal/private address.

    When *allow_loopback* is True (default False), 127.0.0.0/8 and ::1 are
    considered safe — useful for shell commands that need to reach local
    services spawned by the agent itself.
    """
    for m in _URL_RE.finditer(command):
        url = m.group(0)
        try:
            p = urlparse(url)
        except Exception as e:
            logger.debug("Failed to parse URL in targets_internal_address: {} ({})", url, e)
            continue
        if p.scheme not in ("http", "https"):
            continue
        hostname = p.hostname
        if not hostname:
            continue
        try:
            infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # Cannot resolve hostname — treat as potentially internal for defense in depth.
            # Unlike validate_url_target which raises, here we block to avoid SSRF via DNS rebinding.
            logger.debug("DNS resolution failed for {} in targets_internal_address", hostname)
            return True
        for info in infos:
            try:
                addr = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if allow_loopback and any(addr in net for net in _LOOPBACK_NETWORKS):
                continue
            if _is_blocked(addr):
                return True
    return False
