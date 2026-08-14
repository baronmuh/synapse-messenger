"""Synapse A2A registry (SPEC.txt F20 extension): open, governed discovery.

The registry is the additive discovery layer on top of the A2A bridge
(see :mod:`synapse.a2a_bridge`): a Synapse-defined HTTP API that lets any
agent publish its AgentCard v1.0 and lets *anonymous* clients discover
agents by capability and tag before talking to them ("discover-before-talk").

Design (scout_architecture REGISTRY_FEATURE_DESIGN, t_68dcd793 R-F MVE):

- Cards are registered **by URL** and fetched over HTTP from their
  ``/.well-known/agent-card.json`` (RFC 8615 style). Registration is
  **idempotent**: re-registering the same URL refreshes the entry instead
  of creating a duplicate, so recall/precision are stable under retries.
- Each registered card is **validated** as an A2A AgentCard v1.0 before it
  is stored (a ``name``, a non-empty ``supportedInterfaces`` list and a
  ``skills`` array whose ``id``/``tags`` form the capability/tag index).
- The public **capability search** returns the cards whose skills match the
  requested capability and tag filters — the governed pull-by-capability
  read-model of the registry.
- A **verified bit** records whether the card was fetched from a live
  well-known URI and validated at registration time. It is
  *evidence-of-working* — "this card was actually resolvable and parsed"
  — never a registry-blessed trust statement (provocateur t_02ceab8b
  guardrail: authority stays with the owner's self-signed JWS, design
  grade R-C, post-MVE).
- **Stale-card eviction**: a card whose ``last_seen`` age exceeds the TTL
  (the heartbeat TTL) is dropped on the next access, so a registry never
  serves a card whose owner has stopped heartbeating.

Trust model (MVE): registration is write-protected behind the bridge token
(only holders of the secret can publish); the capability **search** is
public and anonymous (no token), the same discover-before-talk principle as
the well-known card. Signature verification (JCS) and selective disclosure
are out of the MVE scope (design grade R-C/B, post-MVE).

Security (auditor N1, 2026-08-11): the registry fetch is a server-side
request whose target comes from the caller, so it is an SSRF surface
(``file:///etc/hostname`` was readable through :func:`_default_fetch`).
The fetch now fails closed: only ``http``/``https`` targets are accepted,
and private/loopback/link-local/reserved addresses (literal or via DNS
resolution) are refused — every redirect hop is re-validated with the
same guard. ``Registry(allow_private=True)`` is the explicit test/local
escape hatch for loopback card servers; the scheme check is never
bypassable.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

logger = logging.getLogger("synapse.a2a.registry")

# Well-known URI (RFC 8615 style) where an agent publishes its AgentCard.
_WELL_KNOWN_CARD = "/.well-known/agent-card.json"

# Default heartbeat TTL: a card not refreshed within this window is evicted.
DEFAULT_TTL_SECONDS = 86400.0  # 24 h
# A registration body larger than this is refused (anti-abuse, mirrors the
# 1 MiB API limit).
_MAX_REGISTRATION_BYTES = 1024 * 1024
# Fetch timeout when pulling a remote well-known card.
_FETCH_TIMEOUT = 5.0

# SSRF guard (fail closed): the only schemes a card fetch may use.
_ALLOWED_SCHEMES = ("http", "https")

# JSON-RPC-style error codes reused across the registry API.
_ERROR_INVALID_REQUEST = -32600
_ERROR_INVALID_PARAMS = -32602
_ERROR_INTERNAL = -32603


class RegistryError(Exception):
    """A registry operation failed.

    ``code`` carries the JSON-RPC-style error code; ``http_status`` the
    HTTP status the bridge should answer with (defaults to 400 for a
    validation/registration error).
    """

    def __init__(self, message: str, *, code: int = _ERROR_INVALID_REQUEST,
                 http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def _skill_capabilities(card: dict) -> list[str]:
    """Extracts the capability ids and tags of an AgentCard v1.0.

    In A2A the discoverable capabilities are the ``skills``: each skill
    carries an ``id`` (the capability) and ``tags``. They form the search
    index; ``capabilities.streaming``/``pushNotifications`` are interface
    features, not searchable capabilities.
    """
    skills = card.get("skills") or []
    capabilities: list[str] = []
    for skill in skills:
        if isinstance(skill, dict):
            cid = skill.get("id")
            if isinstance(cid, str) and cid:
                capabilities.append(cid)
    return capabilities


def _skill_tags(card: dict) -> list[str]:
    skills = card.get("skills") or []
    tags: list[str] = []
    for skill in skills:
        if isinstance(skill, dict):
            skill_tags = skill.get("tags")
            if isinstance(skill_tags, list):
                tags.extend(t for t in skill_tags if isinstance(t, str) and t)
    return tags


def _normalize_card(card: dict) -> dict:
    """Validates an AgentCard v1.0 and returns the normalized searchable form.

    Raises:
        RegistryError: the payload is not a valid AgentCard v1.0.
    """
    if not isinstance(card, dict):
        raise RegistryError("invalid card: expected a JSON object")
    name = card.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RegistryError("invalid card: missing 'name' (AgentCard v1.0)")
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        raise RegistryError(
            "invalid card: missing non-empty 'supportedInterfaces'")
    skills = card.get("skills")
    if not isinstance(skills, list):
        raise RegistryError("invalid card: 'skills' must be a list")
    capabilities = _skill_capabilities(card)
    if not capabilities:
        raise RegistryError(
            "invalid card: no skill 'id' to index (capability search needs "
            "at least one discoverable skill)")
    return {
        "name": name,
        "description": card.get("description"),
        "capabilities": capabilities,
        "tags": _skill_tags(card),
    }


def _blocked(reason: str) -> RegistryError:
    """RegistryError for a URL refused by the SSRF guard (HTTP 400)."""
    return RegistryError(f"registry refuses {reason}",
                         code=_ERROR_INVALID_PARAMS)


def _is_global_ip(value: str) -> bool:
    """True when ``value`` is an IP literal in the public Internet range.

    ``is_global`` is the complement of private/reserved/loopback/
    link-local/multicast/unspecified/broadcast, so it rejects
    ``169.254.169.254`` (cloud metadata), ``127.0.0.1``, ``10.x``,
    ``192.168.x`` and the documentation ranges in one check. IPv4-mapped
    IPv6 addresses (``::ffff:a.b.c.d``) are classified by the mapped IPv4.
    """
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False  # not an IP literal (a DNS name, or garbage)
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return bool(getattr(addr, "is_global", False))


def _host_is_public(host: str) -> bool:
    """True when the host is, or resolves only to, a public address.

    DNS names are resolved and EVERY address must be public — a single
    private/reserved address fails the check (the sentinel fail-closed
    pattern), which also catches names that resolve to loopback via
    ``/etc/hosts``. Resolution failure fails closed: if we cannot prove
    the target is public, we refuse it.
    """
    if _is_global_ip(host):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # a DNS name: resolve and verify all addresses
    else:
        return False  # an IP literal that is not global
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {str(info[4][0]) for info in infos}
    return bool(addrs) and all(_is_global_ip(a) for a in addrs)


def _assert_safe_fetch_url(url: str, *, allow_private: bool) -> None:
    """Fail-closed SSRF guard for registry fetches.

    Refuses any URL that is not http(s), that has no host, or — unless
    ``allow_private`` — whose host is a private/loopback/link-local/
    reserved literal address or resolves (any address) to one. The scheme
    check is unconditional: even an ``allow_private`` caller cannot fetch
    ``file://`` etc. (the audit-proven escape).
    """
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _blocked(f"non-http(s) scheme {parts.scheme or '<none>'!r}")
    host = parts.hostname or ""
    if not host:
        raise _blocked(f"URL without a host ({url!r})")
    if allow_private:
        return
    if not _host_is_public(host):
        raise _blocked(f"private/loopback/link-local target {host!r}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect hop against the SSRF guard.

    A public http(s) URL must not be able to bounce the fetch to a
    private target or a non-http(s) scheme (e.g. ``file://``) through a
    redirect, so each hop runs :func:`_assert_safe_fetch_url` before it
    is followed. Used whenever the default (fail-closed) fetch is active.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_safe_fetch_url(newurl, allow_private=False)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetch(url: str, timeout: float = _FETCH_TIMEOUT,
                   *, allow_private: bool = False) -> bytes:
    """Fetches a well-known card over HTTP(S) — SSRF-guarded.

    Fails closed: non-http(s) schemes and private/loopback/link-local
    targets are refused before any connection (see
    :func:`_assert_safe_fetch_url`). ``allow_private`` is the explicit
    test/local escape hatch for loopback card servers; it relaxes the
    address checks, never the scheme check. Redirects are re-validated
    hop by hop.

    Raises:
        RegistryError: the URL is refused by the guard, unreachable, or
            returned an error status.
    """
    _assert_safe_fetch_url(url, allow_private=allow_private)
    opener = (urllib.request.build_opener() if allow_private
              else urllib.request.build_opener(_SafeRedirectHandler()))
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with opener.open(req, timeout=timeout) as resp:
            return resp.read(_MAX_REGISTRATION_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RegistryError(
            f"could not fetch card at {url}: HTTP {exc.code}",
            code=_ERROR_INVALID_PARAMS, http_status=502) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegistryError(
            f"could not fetch card at {url}: {exc}",
            code=_ERROR_INVALID_PARAMS, http_status=502) from exc


class Registry:
    """In-memory A2A registry: register cards by URL and search by capability.

    Thread-safe: all mutations and searches take the internal lock, so a
    concurrent register/search on a shared bridge is consistent.

    Args:
        fetch: optional callable ``(url) -> bytes`` replacing the default
            HTTP fetch (used by tests to avoid the network). Defaults to
            :func:`_default_fetch` (SSRF-guarded).
        ttl: heartbeat TTL in seconds; a card older than this is evicted.
        now: optional clock ``() -> float`` for deterministic eviction
            tests. Defaults to ``time.time``.
        allow_private: test/local escape hatch — allows fetching from
            loopback/private hosts (local card servers). Defaults to
            ``False``: the registry fetch fails closed. The http(s)-only
            scheme check is enforced in both modes.
    """

    def __init__(self, *, fetch: Callable[[str], bytes] | None = None,
                 ttl: float = DEFAULT_TTL_SECONDS,
                 now: Callable[[], float] | None = None,
                 allow_private: bool = False) -> None:
        self._allow_private = allow_private
        if fetch is None:
            self._fetch = functools.partial(_default_fetch,
                                            allow_private=allow_private)
        else:
            self._fetch = fetch
        self._ttl = ttl
        self._now = now or time.time
        # url -> normalized card entry
        self._cards: dict[str, dict] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, url: str) -> dict:
        """Registers (or refreshes) a card by its URL.

        Fetches the AgentCard v1.0 at ``url`` (or at ``url`` +
        ``/.well-known/agent-card.json`` when the URL is not already a card
        path), validates it and stores it. Idempotent: a second call with the
        same URL returns the same card identifier and refreshes it rather
        than duplicating it.

        Returns:
            The stored card entry (with ``verified`` reflecting the fetch).
        """
        card_url = self._resolve_card_url(url)
        raw = self._fetch(card_url)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RegistryError(
                f"invalid card JSON at {card_url}",
                code=_ERROR_INVALID_PARAMS, http_status=502) from exc
        return self.register_payload(payload, url=card_url)

    def register_payload(self, card: dict, *, url: str,
                         verified: bool = True) -> dict:
        """Validates and stores an already-fetched card.

        ``verified`` defaults to ``True`` (the card was fetched and validated
        before this call); a test/local caller may pass ``False`` to record an
        entry that has not been resolved against a live well-known URI.
        """
        normalized = _normalize_card(card)
        now = self._now()
        entry = {
            "url": url,
            "name": normalized["name"],
            "description": normalized["description"],
            "capabilities": sorted(normalized["capabilities"]),
            "tags": sorted(set(normalized["tags"])),
            "verified": bool(verified),
            "registered_at": now,
            "last_seen": now,
        }
        with self._lock:
            self._cards[url] = entry
        logger.info("registry: registered %s (%d capabilities)", url,
                    len(entry["capabilities"]))
        return dict(entry)

    def _resolve_card_url(self, url: str) -> str:
        """Turns a base URL into a well-known card URL.

        If ``url`` already looks like a card document (ends with the
        well-known path or ``.json``), it is used as-is; otherwise the
        well-known path is appended (RFC 8615 convention).
        """
        if url.rstrip("/").endswith((".json", _WELL_KNOWN_CARD)):
            return url
        return url.rstrip("/") + _WELL_KNOWN_CARD

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(self, *, capability: str | None = None,
               tag: str | None = None) -> list[dict]:
        """Public capability/tag search over the registered cards.

        Returns the matching cards (any card whose capability set contains
        ``capability`` and whose tag set contains ``tag``). A filter that is
        ``None`` is not applied. Stale cards are evicted before searching, so
        the result never contains a card that has stopped heartbeating.
        """
        with self._lock:
            self._evict_stale_locked()
            needle_cap = capability.lower() if capability else None
            needle_tag = tag.lower() if tag else None
            results = []
            for entry in self._cards.values():
                caps = {c.lower() for c in entry["capabilities"]}
                tags = {t.lower() for t in entry["tags"]}
                if needle_cap is not None and needle_cap not in caps:
                    continue
                if needle_tag is not None and needle_tag not in tags:
                    continue
                results.append(dict(entry))
            results.sort(key=lambda e: e["url"])
            return results

    def get(self, url: str) -> dict | None:
        """Returns a single registered card by URL (or ``None``)."""
        with self._lock:
            self._evict_stale_locked()
            entry = self._cards.get(url)
            return dict(entry) if entry else None

    def list_all(self) -> list[dict]:
        """Returns every currently stored card (used for registry health)."""
        with self._lock:
            self._evict_stale_locked()
            return [dict(e) for e in sorted(
                self._cards.values(), key=lambda e: e["url"])]

    # ------------------------------------------------------------------
    # Heartbeat / lifecycle
    # ------------------------------------------------------------------
    def heartbeat(self, url: str) -> bool:
        """Refreshes a card's ``last_seen``. Returns ``False`` if unknown."""
        with self._lock:
            entry = self._cards.get(url)
            if entry is None:
                return False
            entry["last_seen"] = self._now()
            return True

    def remove(self, url: str) -> bool:
        """Removes a card from the registry. Returns ``False`` if unknown."""
        with self._lock:
            return self._cards.pop(url, None) is not None

    def evict_stale(self) -> list[str]:
        """Removes cards whose age exceeds the TTL; returns evicted URLs."""
        with self._lock:
            return self._evict_stale_locked()

    def _evict_stale_locked(self) -> list[str]:
        now = self._now()
        stale = [url for url, e in self._cards.items()
                 if (now - e["last_seen"]) > self._ttl]
        for url in stale:
            self._cards.pop(url, None)
        if stale:
            logger.info("registry: evicted %d stale card(s): %s",
                        len(stale), ", ".join(stale))
        return stale
