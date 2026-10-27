"""SSRF guard: resolve + validate every hop, then connect to the validated IP (anti DNS-rebinding)."""
import ipaddress
import socket
from urllib.parse import urlsplit

from backend import config

ALLOWED_PORTS = {80, 443, 8080, 8443}
METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


class SSRFBlocked(Exception):
    pass


def _ip_ok(ip: ipaddress._BaseAddress) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or (getattr(ip, "ipv4_mapped", None) is not None and not _ip_ok(ip.ipv4_mapped)))


def validate_url(url: str) -> tuple[str, str, int, str]:
    """Return (scheme, host, port, resolved_ip) or raise SSRFBlocked."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"scheme '{parts.scheme}' is not allowed")
    if parts.username or parts.password:
        raise SSRFBlocked("credentials in URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host or host in METADATA_HOSTS:
        raise SSRFBlocked("host is not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    allow_private = host in config.HTTP_ALLOW_PRIVATE_HOSTS
    if port not in ALLOWED_PORTS and not allow_private:
        raise SSRFBlocked(f"port {port} is not allowed")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFBlocked(f"cannot resolve host: {e}") from e
    ips = [ipaddress.ip_address(i[4][0]) for i in infos]
    if not ips:
        raise SSRFBlocked("host did not resolve")
    if not allow_private:
        for ip in ips:
            if not _ip_ok(ip):
                raise SSRFBlocked(f"host resolves to a non-public address ({ip})")
    return parts.scheme, host, port, str(ips[0])
