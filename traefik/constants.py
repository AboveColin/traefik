"""Endpoints and magic values for the Traefik API."""

from __future__ import annotations

from typing import Final

DEFAULT_TIMEOUT: Final = 10.0

# Traefik serves its API under /api on whichever entrypoint the operator
# exposed it on, so the port varies between installations but the paths do not.
EP_VERSION: Final = "/api/version"
EP_OVERVIEW: Final = "/api/overview"
EP_ENTRYPOINTS: Final = "/api/entrypoints"
EP_HTTP_ROUTERS: Final = "/api/http/routers"
EP_HTTP_SERVICES: Final = "/api/http/services"

# Metrics live on their own entrypoint and are off unless the operator enabled
# the Prometheus exporter, so this is always treated as optional.
EP_METRICS: Final = "/metrics"

# Values Traefik reports in a router's or service's "status" field.
STATUS_ENABLED: Final = "enabled"
STATUS_DISABLED: Final = "disabled"
STATUS_WARNING: Final = "warning"

# Values Traefik reports per backend server in a service's "serverStatus" map.
SERVER_UP: Final = "UP"
SERVER_DOWN: Final = "DOWN"

# Prometheus metric families this library understands. Traefik exports far
# more; parsing only these keeps the reader small and predictable.
METRIC_CERT_EXPIRY: Final = "traefik_tls_certs_not_after"
METRIC_OPEN_CONNECTIONS: Final = "traefik_open_connections"
METRIC_CONFIG_RELOADS: Final = "traefik_config_reloads_total"
METRIC_LAST_RELOAD: Final = "traefik_config_last_reload_success"

# Per-service and per-entrypoint traffic. These only exist when the operator
# left addServicesLabels / addEntryPointsLabels on (both default to true).
METRIC_SERVICE_REQUESTS: Final = "traefik_service_requests_total"
METRIC_SERVICE_DURATION_SUM: Final = "traefik_service_request_duration_seconds_sum"
METRIC_SERVICE_DURATION_COUNT: Final = "traefik_service_request_duration_seconds_count"
METRIC_ENTRYPOINT_REQUESTS: Final = "traefik_entrypoint_requests_total"
METRIC_ENTRYPOINT_DURATION_SUM: Final = (
    "traefik_entrypoint_request_duration_seconds_sum"
)
METRIC_ENTRYPOINT_DURATION_COUNT: Final = (
    "traefik_entrypoint_request_duration_seconds_count"
)
