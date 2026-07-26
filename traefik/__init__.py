"""Async client library for the Traefik API."""

from __future__ import annotations

from .client import TraefikClient
from .exceptions import (
    TraefikAuthenticationError,
    TraefikConnectionError,
    TraefikError,
    TraefikNotFoundError,
    TraefikResponseError,
)
from .metrics import parse_metrics
from .models import (
    Certificate,
    EntryPoint,
    Metrics,
    Overview,
    Router,
    SectionCounts,
    ServerInfo,
    Service,
    TrafficStats,
)

__all__ = [
    "Certificate",
    "EntryPoint",
    "Metrics",
    "Overview",
    "Router",
    "SectionCounts",
    "ServerInfo",
    "Service",
    "TraefikAuthenticationError",
    "TraefikClient",
    "TraefikConnectionError",
    "TraefikError",
    "TraefikNotFoundError",
    "TraefikResponseError",
    "TrafficStats",
    "parse_metrics",
]

__version__ = "1.1.0"
