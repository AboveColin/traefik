"""Typed models returned by the client.

Callers never see raw JSON. Every ``from_api`` classmethod is defensive about
missing keys: what Traefik reports depends on which providers and features the
operator enabled, so a field that exists on one instance may be absent on the
next. A missing value becomes ``None`` rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .constants import SERVER_UP, STATUS_ENABLED

__all__ = [
    "Certificate",
    "EntryPoint",
    "Metrics",
    "Overview",
    "Router",
    "SectionCounts",
    "ServerInfo",
    "Service",
]


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an API timestamp, tolerating the ``Z`` suffix and junk."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Traefik returns the zero time before it has finished starting; surfacing
    # year 1 as a real timestamp makes template sensors render nonsense dates.
    return None if parsed.year <= 1 else parsed


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Version banner of the instance."""

    version: str
    codename: str | None
    start_date: datetime | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ServerInfo:
        """Build from a ``/api/version`` payload."""
        return cls(
            version=str(data.get("Version", "")),
            codename=data.get("Codename") or None,
            start_date=_parse_timestamp(data.get("startDate")),
        )


@dataclass(frozen=True, slots=True)
class SectionCounts:
    """How many objects of one kind exist, and how healthy they are.

    Traefik reports this shape for routers, services and middlewares of every
    protocol, so one model covers all of them.
    """

    total: int
    warnings: int
    errors: int

    @classmethod
    def from_api(cls, data: Any) -> SectionCounts:
        """Build from a ``{total, warnings, errors}`` object."""
        if not isinstance(data, dict):
            return cls(total=0, warnings=0, errors=0)
        return cls(
            total=int(data.get("total", 0)),
            warnings=int(data.get("warnings", 0)),
            errors=int(data.get("errors", 0)),
        )


@dataclass(frozen=True, slots=True)
class Overview:
    """The dashboard summary: object counts per protocol, plus features."""

    http_routers: SectionCounts
    http_services: SectionCounts
    http_middlewares: SectionCounts
    tcp_routers: SectionCounts
    tcp_services: SectionCounts
    tcp_middlewares: SectionCounts
    udp_routers: SectionCounts
    udp_services: SectionCounts
    certificates: int
    providers: tuple[str, ...]
    metrics_provider: str | None
    access_log: bool
    tracing_provider: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Overview:
        """Build from an ``/api/overview`` payload."""
        http = data.get("http") if isinstance(data.get("http"), dict) else {}
        tcp = data.get("tcp") if isinstance(data.get("tcp"), dict) else {}
        udp = data.get("udp") if isinstance(data.get("udp"), dict) else {}
        features = (
            data.get("features") if isinstance(data.get("features"), dict) else {}
        )
        providers = data.get("providers")

        # Traefik writes the string "false" (not a boolean) when a feature is
        # off, so an empty-or-"false" test is what actually distinguishes
        # "configured" from "not configured".
        def _feature(key: str) -> str | None:
            value = features.get(key)
            if not value or value == "false":
                return None
            return str(value)

        return cls(
            http_routers=SectionCounts.from_api(http.get("routers")),
            http_services=SectionCounts.from_api(http.get("services")),
            http_middlewares=SectionCounts.from_api(http.get("middlewares")),
            tcp_routers=SectionCounts.from_api(tcp.get("routers")),
            tcp_services=SectionCounts.from_api(tcp.get("services")),
            tcp_middlewares=SectionCounts.from_api(tcp.get("middlewares")),
            udp_routers=SectionCounts.from_api(udp.get("routers")),
            udp_services=SectionCounts.from_api(udp.get("services")),
            certificates=len(data.get("certificates") or []),
            providers=tuple(str(p) for p in providers) if providers else (),
            metrics_provider=_feature("metrics"),
            access_log=bool(features.get("accessLog", False)),
            tracing_provider=_feature("tracing"),
        )


@dataclass(frozen=True, slots=True)
class EntryPoint:
    """A port Traefik listens on."""

    name: str
    address: str
    http2: bool
    udp: bool

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> EntryPoint:
        """Build from an entry of ``/api/entrypoints``."""
        return cls(
            name=str(data.get("name", "")),
            address=str(data.get("address", "")),
            http2=isinstance(data.get("http2"), dict),
            udp=isinstance(data.get("udp"), dict),
        )


@dataclass(frozen=True, slots=True)
class Router:
    """One HTTP router: a rule that sends matching requests to a service."""

    name: str
    rule: str | None
    service: str | None
    status: str
    provider: str | None
    priority: int | None
    entry_points: tuple[str, ...]
    middlewares: tuple[str, ...]
    tls: bool

    @property
    def enabled(self) -> bool:
        """Whether Traefik accepted this router's configuration."""
        return self.status == STATUS_ENABLED

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Router:
        """Build from an entry of ``/api/http/routers``."""
        entry_points = data.get("entryPoints") or []
        middlewares = data.get("middlewares") or []
        priority = data.get("priority")
        return cls(
            name=str(data.get("name", "")),
            rule=data.get("rule") or None,
            service=data.get("service") or None,
            status=str(data.get("status", "")),
            provider=data.get("provider") or None,
            priority=int(priority) if isinstance(priority, int) else None,
            entry_points=tuple(str(e) for e in entry_points),
            middlewares=tuple(str(m) for m in middlewares),
            tls=data.get("tls") is not None,
        )


@dataclass(frozen=True, slots=True)
class Service:
    """One HTTP service: the backend servers a router forwards to."""

    name: str
    status: str
    provider: str | None
    type: str | None
    server_status: dict[str, str]
    used_by: tuple[str, ...]
    has_health_check: bool = False

    @property
    def enabled(self) -> bool:
        """Whether Traefik accepted this service's configuration."""
        return self.status == STATUS_ENABLED

    @property
    def all_servers_up(self) -> bool | None:
        """Whether every probed server is up, or ``None`` if none are probed.

        Traefik reports ``serverStatus`` for every service, but only ever
        writes ``DOWN`` into it for a service with a
        ``loadBalancer.healthCheck``. Everything else reads ``UP`` forever,
        including servers that are switched off — so without a health check
        this is unknown rather than a clean bill of health.
        """
        if not self.has_health_check or not self.server_status:
            return None
        return all(state == SERVER_UP for state in self.server_status.values())

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Service:
        """Build from an entry of ``/api/http/services``."""
        server_status = data.get("serverStatus")
        used_by = data.get("usedBy") or []
        load_balancer = data.get("loadBalancer")
        return cls(
            name=str(data.get("name", "")),
            status=str(data.get("status", "")),
            provider=data.get("provider") or None,
            type=data.get("type") or None,
            server_status=(
                {str(k): str(v) for k, v in server_status.items()}
                if isinstance(server_status, dict)
                else {}
            ),
            used_by=tuple(str(u) for u in used_by),
            has_health_check=bool(
                isinstance(load_balancer, dict) and load_balancer.get("healthCheck")
            ),
        )


@dataclass(frozen=True, slots=True)
class Certificate:
    """A TLS certificate Traefik is serving, and when it stops being valid."""

    common_name: str
    not_after: datetime

    @property
    def days_remaining(self) -> int:
        """Whole days until expiry; negative once the certificate has expired."""
        return (self.not_after - datetime.now(UTC)).days


@dataclass(frozen=True, slots=True)
class Metrics:
    """The handful of Prometheus values worth turning into entities.

    Every field is optional: the metrics endpoint is opt-in, and which families
    appear depends on the Traefik configuration.
    """

    open_connections: int | None = None
    config_reloads: int | None = None
    last_reload: datetime | None = None
    certificates: tuple[Certificate, ...] = ()

    @property
    def nearest_expiry(self) -> Certificate | None:
        """The certificate that expires soonest, if any are known."""
        return min(self.certificates, key=lambda c: c.not_after, default=None)
