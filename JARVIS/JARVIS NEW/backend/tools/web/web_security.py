import ipaddress
import socket
from urllib.parse import urlparse

def validate_secure_url(url: str) -> str:
    """
    Validates a URL against strict security policies:
    - Must use HTTP or HTTPS.
    - Blocks loopback, private networks, and link-local addresses (SSRF protection).
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    
    if not hostname:
        raise ValueError(f"Invalid or empty hostname in URL: '{url}'")

    # 1. Explicit Hostname Denylists
    forbidden_hosts = {"localhost", "metadata.google.internal", "instance-data"}
    if hostname in forbidden_hosts:
        raise PermissionError(f"Security Violation: Access to restricted host '{hostname}' is blocked.")

    # 2. Resolve IP and Check against Private/Loopback/Link-Local Ranges
    try:
        # Resolve hostname to IP address to catch DNS pinning/rebinding tricks
        ip_str = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip_str)

        if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local or ip_obj.is_reserved:
            raise PermissionError(
                f"Security Violation: IP address '{ip_str}' for host '{hostname}' resolves to a private, loopback, or restricted network range."
            )
    except socket.gaierror:
        raise RuntimeError(f"DNS Resolution failed for host: '{hostname}'")
    except (ValueError, PermissionError) as e:
        if isinstance(e, PermissionError):
            raise e
        raise ValueError(f"Could not parse IP address for host '{hostname}'.")

    return url