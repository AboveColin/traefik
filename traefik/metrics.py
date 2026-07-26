"""A very small reader for Traefik's Prometheus output.

This is deliberately not a general Prometheus parser. Traefik exports a few
thousand lines, almost all of them per-route latency histograms that nobody
wants as a Home Assistant entity. Only four families are read, and everything
else is skipped without being understood.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .constants import (
    METRIC_CERT_EXPIRY,
    METRIC_CONFIG_RELOADS,
    METRIC_LAST_RELOAD,
    METRIC_OPEN_CONNECTIONS,
)
from .models import Certificate, Metrics

__all__ = ["parse_metrics"]

_WANTED = (
    METRIC_CERT_EXPIRY,
    METRIC_CONFIG_RELOADS,
    METRIC_LAST_RELOAD,
    METRIC_OPEN_CONNECTIONS,
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
    """Read the four families this library cares about out of a metrics page."""
    open_connections: float | None = None
    reloads: float | None = None
    last_reload: datetime | None = None
    certificates: list[Certificate] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(_WANTED):
            continue
        parsed = _split(line)
        if parsed is None:
            continue
        name, labels, value = parsed

        if name == METRIC_OPEN_CONNECTIONS:
            # One series per entrypoint and method, so the total is the sum.
            open_connections = (open_connections or 0) + value
        elif name == METRIC_CONFIG_RELOADS:
            reloads = value
        elif name == METRIC_LAST_RELOAD:
            last_reload = _as_datetime(value)
        elif name == METRIC_CERT_EXPIRY:
            expiry = _as_datetime(value)
            if expiry is not None:
                certificates.append(
                    Certificate(
                        common_name=labels.get("cn", ""),
                        not_after=expiry,
                    )
                )

    return Metrics(
        open_connections=int(open_connections)
        if open_connections is not None
        else None,
        config_reloads=int(reloads) if reloads is not None else None,
        last_reload=last_reload,
        certificates=tuple(certificates),
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
