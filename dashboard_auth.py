"""Fail-closed owner/session and narrowly scoped machine authentication.

This module never reads production data, initiates worker actions, or accepts a
request-supplied identity provider. Issue #3 separately owns CSRF protection.
"""

import asyncio
import hmac
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

SESSION_DEADLINE_SECONDS = 2.0
SESSION_MAX_BYTES = 64 * 1024
_MAX_SESSION_COOKIES = 16
_SESSION_COOKIE = re.compile(
    r"(?P<family>__Secure-authjs\.session-token|authjs\.session-token)"
    r"(?:\.(?P<part>0|[1-9][0-9]*))?"
)
_MACHINE_ROUTES = (
    ("GET", re.compile(r"/api/proposals/[^/]+/executor\Z")),
    ("GET", re.compile(r"/api/agents/[^/]+/executor-status\Z")),
    ("GET", re.compile(r"/api/agents/executor-summary\Z")),
    ("POST", re.compile(r"/api/proposals/[^/]+/comments\Z")),
)


def trusted_issuer_origin(auth_url: str) -> str | None:
    """Require one explicitly configured HTTPS origin, without extra URL parts."""
    try:
        parsed = urlsplit(auth_url.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or any(char in auth_url for char in "\r\n\\")
        ):
            return None
        _ = parsed.port  # Reject invalid ports instead of trusting malformed config.
        if any(char.isspace() for char in parsed.netloc):
            return None
        return f"https://{parsed.netloc}"
    except (ValueError, AttributeError):
        return None


def session_cookie_header(raw_headers: list[str]) -> str | None:
    """Forward only one complete Auth.js session cookie or ordered chunk set."""
    if sum(len(h) for h in raw_headers) > SESSION_MAX_BYTES:
        return None
    found: dict[str, str] = {}
    family: str | None = None
    for header in raw_headers:
        for entry in header.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            name, separator, value = entry.partition("=")
            if not separator:
                continue
            is_session = name.startswith(("authjs.session-token", "__Secure-authjs.session-token"))
            match = _SESSION_COOKIE.fullmatch(name)
            if is_session and match is None:
                return None
            if match is None:
                continue
            if (
                not value
                or len(value) > SESSION_MAX_BYTES
                or any(c in value for c in "\r\n;")
                or name in found
                or (family is not None and family != match["family"])
            ):
                return None
            family = match["family"]
            found[name] = value
            if len(found) > _MAX_SESSION_COOKIES:
                return None
    if not found or family is None:
        return None
    if family in found:
        return f"{family}={found[family]}" if len(found) == 1 else None
    chunks = [(int(key.removeprefix(family + ".")), value) for key, value in found.items()]
    chunks.sort()
    if [index for index, _ in chunks] != list(range(len(chunks))):
        return None
    return "; ".join(f"{family}.{index}={value}" for index, value in chunks)


def machine_route_allowed(method: str, path: str) -> bool:
    """No wildcard API access: comments and read-only executor routing only."""
    return any(method == verb and pattern.fullmatch(path) for verb, pattern in _MACHINE_ROUTES)


def machine_key_valid(presented_headers: list[str], configured_key: str) -> bool:
    """Weak, missing or duplicate key headers must fail closed."""
    if len(presented_headers) != 1 or len(configured_key) < 32:
        return False
    try:
        configured = configured_key.encode("ascii")
        provided = presented_headers[0].encode("ascii")
    except UnicodeEncodeError:
        return False
    return len(provided) <= 4096 and hmac.compare_digest(provided, configured)


def local_auth_off_allowed(peer: str | None, host: str) -> bool:
    """Explicit auth-off development is never a trusted remote/proxy mode."""
    if peer not in {"127.0.0.1", "::1", "testclient"}:
        return False
    try:
        parsed_host = urlsplit(f"http://{host}").hostname
    except ValueError:
        return False
    return parsed_host in {"localhost", "127.0.0.1", "::1", "testserver"}


def _new_issuer_client() -> httpx.AsyncClient:
    # No environment proxy, redirects or shared cookies for secret-bearing calls.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(SESSION_DEADLINE_SECONDS),
        follow_redirects=False,
        trust_env=False,
    )


async def verify_owner_session(
    raw_cookie_headers: list[str], auth_url: str, owner_github_id: str, owner_email: str
) -> bool:
    """Validate an Auth.js session with a fixed issuer and exact owner identity."""
    issuer = trusted_issuer_origin(auth_url)
    cookie = session_cookie_header(raw_cookie_headers)
    expected_id = owner_github_id.strip()
    expected_email = owner_email.strip().casefold()
    if not issuer or not cookie or not (expected_id or expected_email):
        return False
    try:
        async with asyncio.timeout(SESSION_DEADLINE_SECONDS):
            async with _new_issuer_client() as client:
                async with client.stream(
                    "GET", f"{issuer}/api/auth/session",
                    headers={"Cookie": cookie, "Accept": "application/json", "Cache-Control": "no-store"},
                ) as response:
                    if response.status_code != 200:
                        return False
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        return False
                    contents = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(contents) + len(chunk) > SESSION_MAX_BYTES:
                            return False
                        contents.extend(chunk)
        session = json.loads(bytes(contents).decode("utf-8"))
        if not isinstance(session, dict) or not isinstance(session.get("user"), dict):
            return False
        user = session["user"]
        expires = session.get("expires")
        if not isinstance(expires, str):
            return False
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            return False
        if expected_id and (not isinstance(user.get("id"), (str, int)) or str(user["id"]) != expected_id):
            return False
        if expected_email and (not isinstance(user.get("email"), str) or user["email"].strip().casefold() != expected_email):
            return False
        return True
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError, UnicodeError, OSError):
        return False
