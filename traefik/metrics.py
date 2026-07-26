"""A very small reader for Traefik's Prometheus output.

This is deliberately not a general Prometheus parser. Traefik exports a few
thousand lines, most of them latency-histogram buckets that nobody wants as a
Home Assistant entity. A fixed set of families is read by exact name — the
``_bucket`` series in particular share a prefix with families we do want, so
prefix matching alone would pull the whole histogram in — and everything else
is skipped without being understood.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from .constants import (
    METRIC_CERT_EXPIRY,
    METRIC_CONFIG_RELOADS,
    METRIC_ENTRYPOINT_DURATION_COUNT,
    METRIC_ENTRYPOINT_DURATION_SUM,
    METRIC_ENTRYPOINT_REQUESTS,
    METRIC_LAST_RELOAD,
    METRIC_OPEN_CONNECTIONS,
    METRIC_SERVICE_DURATION_COUNT,
    METRIC_SERVICE_DURATION_SUM,
    METRIC_SERVICE_REQUESTS,
)
from .models import Certificate, Metrics, TrafficStats

__all__ = ["parse_metrics"]

_WANTED = frozenset(
    {
        METRIC_CERT_EXPIRY,
        METRIC_CONFIG_RELOADS,
        METRIC_ENTRYPOINT_DURATION_COUNT,
        METRIC_ENTRYPOINT_DURATION_SUM,
        METRIC_ENTRYPOINT_REQUESTS,
        METRIC_LAST_RELOAD,
        METRIC_OPEN_CONNECTIONS,
        METRIC_SERVICE_DURATION_COUNT,
        METRIC_SERVICE_DURATION_SUM,
        METRIC_SERVICE_REQUESTS,
    }
)

# Cheap prefix gate so the histogram buckets — by far the bulk of the file —
# are dropped before the comparatively expensive label split.
_PREFIX = "traefik_"


class _Accumulator:
    """Running totals for one service or entrypoint."""

    __slots__ = ("client_errors", "duration_count", "duration_total", "requests", "server_errors")

    def __init__(self) -> None:
        self.requests = 0.0
        self.client_errors = 0.0
        self.server_errors = 0.0
        self.duration_total = 0.0
        self.duration_count = 0.0

    def add_requests(self, code: str, value: float) -> None:
        """Count a ``requests_total`` series, bucketed by response class."""
        self.requests += value
        # Traefik reports code="0" for connections that never produced a
        # status — websocket upgrades, mostly. Those are not errors.
        if code.startswith("5"):
            self.server_errors += value
        elif code.startswith("4"):
            self.client_errors += value

    def freeze(self) -> TrafficStats:
        """Convert to the public, immutable shape."""
        return TrafficStats(
            requests=int(self.requests),
            client_errors=int(self.client_errors),
            server_errors=int(self.server_errors),
            duration_total=self.duration_total,
            duration_count=int(self.duration_count),
        )


def _split(line: str) -> tuple[str, dict[str, str], float] | None:
    """Split one sample into its name, labels and value.

    Returns ``None`` for anything malformed; a single unreadable line should
    never cost the caller the rest of the file.
    """
    name, _, remainder = line.partition("{")
    if remainder:
        label_text, _, value_text = remainder.rpartition("}")
        labels = {}
        for pair in _split_labels(label_text):
            key, _, value = pair.partition("=")
            labels[key.strip()] = value.strip().strip('"')
    else:
        name, _, value_text = line.partition(" ")
        labels = {}

    try:
        return name.strip(), labels, float(value_text.strip())
    except ValueError:
        return None


def _split_labels(text: str) -> list[str]:
    """Split a label block on commas that are not inside a quoted value.

    Traefik puts SAN lists in the ``sans`` label, and those contain commas.
    """
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return [p for p in parts if p.strip()]


def parse_metrics(text: str) -> Metrics:
    """Read the families this library cares about out of a metrics page."""
    open_connections: float | None = None
    reloads: float | None = None
    last_reload: datetime | None = None
    certificates: list[Certificate] = []
    connections: dict[str, float] = defaultdict(float)
    services: dict[str, _Accumulator] = defaultdict(_Accumulator)
    entrypoints: dict[str, _Accumulator] = defaultdict(_Accumulator)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or not line.startswith(_PREFIX):
            continue
        parsed = _split(line)
        if parsed is None:
            continue
        name, labels, value = parsed
        if name not in _WANTED:
            continue

        if name == METRIC_OPEN_CONNECTIONS:
            # One series per entrypoint and protocol, so the total is the sum.
            open_connections = (open_connections or 0) + value
            if entrypoint := labels.get("entrypoint"):
                connections[entrypoint] += value
        elif name == METRIC_CONFIG_RELOADS:
            reloads = value
        elif name == METRIC_LAST_RELOAD:
            last_reload = _as_datetime(value)
        elif name == METRIC_CERT_EXPIRY:
            expiry = _as_datetime(value)
            if expiry is not None:
                sans = labels.get("sans", "")
                certificates.append(
                    Certificate(
                        common_name=labels.get("cn", ""),
                        not_after=expiry,
                        sans=tuple(s for s in (p.strip() for p in sans.split(",")) if s),
                    )
                )
        elif name == METRIC_SERVICE_REQUESTS:
            if service := labels.get("service"):
                services[service].add_requests(labels.get("code", ""), value)
        elif name == METRIC_SERVICE_DURATION_SUM:
            if service := labels.get("service"):
                services[service].duration_total += value
        elif name == METRIC_SERVICE_DURATION_COUNT:
            if service := labels.get("service"):
                services[service].duration_count += value
        elif name == METRIC_ENTRYPOINT_REQUESTS:
            if entrypoint := labels.get("entrypoint"):
                entrypoints[entrypoint].add_requests(labels.get("code", ""), value)
        elif name == METRIC_ENTRYPOINT_DURATION_SUM:
            if entrypoint := labels.get("entrypoint"):
                entrypoints[entrypoint].duration_total += value
        elif name == METRIC_ENTRYPOINT_DURATION_COUNT:
            if entrypoint := labels.get("entrypoint"):
                entrypoints[entrypoint].duration_count += value

    return Metrics(
        open_connections=int(open_connections)
        if open_connections is not None
        else None,
        config_reloads=int(reloads) if reloads is not None else None,
        last_reload=last_reload,
        certificates=tuple(certificates),
        services={name: acc.freeze() for name, acc in services.items()},
        entrypoints={name: acc.freeze() for name, acc in entrypoints.items()},
        connections_by_entrypoint={
            name: int(value) for name, value in connections.items()
        },
    )


def _as_datetime(value: float) -> datetime | None:
    """Turn a unix-timestamp metric into a datetime.

    Traefik reports 0 before the first reload has happened, and that is an
    absence rather than a date in 1970.
    """
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
