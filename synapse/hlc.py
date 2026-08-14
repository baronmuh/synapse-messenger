"""Hybrid Logical Clock (C1) — the causal-time coordination primitive.

Implements the HLC spec of DESIGN_CAUSAL_TIME_HLC_v2 (§3, card t_498f482f),
mirroring CockroachDB HLC semantics (DESIGN §3.1a): a ``(l, c)`` pair of a
physical component (ms since Unix epoch) and a logical per-l ordinal,
encoded as one fixed-width TEXT ``"{l:013d}.{c:06d}"`` so that SQLite
TEXT byte order equals causal order (lexicographic compare).

Update rules (Kulkarni 2014, as implemented by CockroachDB — the
reference contract, DESIGN §3.2):

    LOCAL EVENT / SEND:   l' = max(l, pt());  c' = c + 1 if l' == l else 0
    RECEIVE (l_r, c_r):   l' = max(l, l_r)
                          if   l == l_r == l' : c' = max(c, c_r) + 1
                          elif l == l'        : c' = c + 1
                          elif l_r == l'      : c' = c_r + 1
                          else                : c' = 0

Invariants (tested in MVE-1):
    I1  a -> b (happens-before)  =>  hlc(a) < hlc(b)
    I2  |hlc.l - pt()| is bounded (HLC tracks physical time)
    I3  hlc.l never decreases (monotone across NTP backwards jumps)
    I4  within one instance, hlc order == seq order (single Clock, one
        writer, serialized by BEGIN IMMEDIATE)

Server-stamped ONLY: the clock lives inside the server process and is
never accepted from clients (the Cassandra client-timestamp lesson,
DESIGN §3.2). One instance per server process, owned by ``Service``,
guarded by a ``threading.Lock`` (stamp/observe are O(1)).

``physical`` is an injectable provider (default: system clock in ms) —
this is the MVE skew seam: a test provider returns ``pt() + skew_ms``
per instance, and the same interface admits a stronger physical backend
(NTP-disciplined, hardware) later without schema or API change.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable

# Fixed-width components: 13 digits of ms covers the epoch up to year
# 2286; 6 digits of logical counter covers 0..999 999 stamps per
# millisecond. The fixed width is what makes SQLite TEXT byte order
# equivalent to causal order.
_L_DIGITS = 13
_C_DIGITS = 6
_MAX_L = 10**_L_DIGITS - 1
_MAX_C = 10**_C_DIGITS - 1

_HLC_RE = re.compile(rf"^\d{{{_L_DIGITS}}}\.\d{{{_C_DIGITS}}}$")


def default_pt() -> int:
    """Physical time in ms since the Unix epoch (system clock)."""
    return int(time.time() * 1000)


def encode(l: int, c: int) -> str:
    """Canonical fixed-width encoding of an ``(l, c)`` pair.

    Raises:
        ValueError: component out of the fixed-width range (defensive;
            unreachable with the update rules and a sane physical clock).
    """
    if not 0 <= l <= _MAX_L:
        raise ValueError(f"hlc physical component out of range: {l}")
    if not 0 <= c <= _MAX_C:
        raise ValueError(f"hlc logical component out of range: {c}")
    return f"{l:0{_L_DIGITS}d}.{c:0{_C_DIGITS}d}"


def decode(s: str) -> tuple[int, int]:
    """Parses a canonical HLC string back into ``(l, c)``.

    Raises:
        ValueError: the string is not in the canonical format.
    """
    if not is_valid(s):
        raise ValueError(f"malformed hlc: {s!r}")
    l, c = s.split(".")
    return int(l), int(c)


def is_valid(s: str) -> bool:
    """True when ``s`` is a canonical HLC string (exact fixed width)."""
    return isinstance(s, str) and _HLC_RE.fullmatch(s) is not None


class HLC:
    """A thread-safe hybrid logical clock (one per server process).

    Args:
        physical: injectable physical-time provider in ms (skew seam);
            defaults to the system clock.
        initial: rehydration value (the persisted upper bound — the
            Synapse analogue of CockroachDB's ``WallTimeUpperBound``,
            DESIGN §3.3): the clock never moves below what has been
            durably written. ``None`` starts at the physical clock.
    """

    def __init__(
        self,
        physical: Callable[[], int] = default_pt,
        initial: str | None = None,
    ) -> None:
        self._physical = physical
        self._lock = threading.Lock()
        if initial is not None:
            if not is_valid(initial):
                raise ValueError(f"malformed hlc rehydration value: {initial!r}")
            self._l, self._c = decode(initial)
        else:
            self._l, self._c = 0, 0

    def stamp(self) -> str:
        """Local-event rule: advances the clock and returns the stamp.

        The stamp is computed and committed atomically (lock-guarded):
        concurrent callers never observe or emit the same value twice.
        """
        with self._lock:
            pt = self._physical()
            l = self._l if self._l > pt else pt
            c = self._c + 1 if l == self._l else 0
            self._l, self._c = l, c
            return encode(l, c)

    def observe(self, remote: str) -> None:
        """The merge rule: absorbs a remote HLC timestamp.

        Called BEFORE any local stamp in a request that carries a
        remote hlc (inbound bridge envelope, relayed fact digest...).
        The local clock advances to ``max(local, remote)`` — a remote
        clock ahead of us (skew) is absorbed, never rejected in the MVE
        (a configurable skew guard is the phase-2 option, DESIGN §7.6).

        Raises:
            ValueError: the remote value is not a canonical HLC string
                (callers validate at the API boundary first).
        """
        if not is_valid(remote):
            raise ValueError(f"malformed remote hlc: {remote!r}")
        l_r, c_r = decode(remote)
        with self._lock:
            l = self._l if self._l > l_r else l_r
            if self._l == l_r == l:
                c = (self._c if self._c > c_r else c_r) + 1
            elif self._l == l:
                c = self._c + 1
            elif l_r == l:
                c = c_r + 1
            else:  # pragma: no cover - defensive: l' = max(l, l_r) makes
                # this branch unreachable (the paper's general form keeps
                # it; the local rule covers the physical-ahead case)
                c = 0
            self._l, self._c = l, c

    def peek(self) -> str:
        """Read-only view of the clock (no state change)."""
        with self._lock:
            return encode(self._l, self._c)
