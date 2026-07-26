# traefik

Async Python client for the [Traefik](https://traefik.io/) API.

Read-only: version, the dashboard overview, entrypoints, HTTP routers and
services, and — optionally — the handful of useful numbers from the Prometheus
metrics endpoint.

Written for [HA-Traefik](https://github.com/AboveColin/HA-Traefik), but it has
no Home Assistant dependency and works anywhere.

## Install

```bash
pip install traefik
```

## Use

```python
import asyncio

from traefik import TraefikClient


async def main() -> None:
    async with TraefikClient("http://192.0.2.10:8080") as client:
        info = await client.get_version()
        print(info.version, info.codename)

        overview = await client.get_overview()
        print(overview.http_routers.total, "routers")
        print(overview.http_routers.errors, "with errors")

        for router in await client.list_routers():
            # ``hostnames`` pulls the literal Host(...) values out of the rule.
            print(router.hostnames or router.name, router.status)


asyncio.run(main())
```

### Authentication

Traefik's API has no authentication of its own. It is normally protected by an
`ipAllowList` or a `basicAuth` middleware. For the second case, pass
credentials:

```python
TraefikClient("https://traefik.example.com", username="admin", password="…")
```

For the first, the host running this code has to be inside the allowed range —
there is nothing the client can do about it. Either way a refusal raises
`TraefikAuthenticationError`.

### Metrics

Traefik's Prometheus exporter usually lives on its own entrypoint, on a
different port from the API, so it is configured separately and is off unless
you ask for it:

```python
client = TraefikClient(
    "http://192.0.2.10:8080",
    metrics_url="http://192.0.2.10:8082",
)
metrics = await client.get_metrics()

print(metrics.open_connections, metrics.connections_by_entrypoint)
print(metrics.last_reload)

total = metrics.totals()
print(total.requests, "requests", total.error_rate, "% errors")

for name, stats in metrics.services.items():
    print(name, stats.requests, stats.errors, stats.average_duration)

for cert in metrics.certificates:
    print(cert.common_name, cert.sans, cert.days_remaining, "days left")

# Tightest match wins: an exact subject beats a wildcard that also covers it.
print(metrics.certificate_for("git.example.com"))
```

Ten metric families are read: open connections, config reloads, last
successful reload, per-certificate expiry, and request counts and durations
per service and per entrypoint. The latency *histograms* are skipped — they
are the bulk of the output and say little that the totals do not.

There are no per-router metrics to read. Traefik can label them
(`addRoutersLabels`) but does not by default, so per-route figures are best
obtained through the service a router forwards to. Note that a router names
its service without the provider suffix (`foo`) while the metrics use the
qualified name (`foo@file`).

`TrafficStats.error_rate` and `average_duration` are lifetime figures derived
from counters, and `None` rather than zero when a service has seen no traffic.
Responses Traefik reports with `code="0"` — websocket upgrades that never
produced a status — are counted as requests but not as errors.

## A note on `serverStatus`

`Service.all_servers_up` returns `None`, not `True`, when Traefik is not
probing a service's backends. Traefik only marks a server down when the
configuration gives that service a `loadBalancer.healthCheck`; without one it
reports every server as up forever, including servers that are switched off.

Note that a populated `serverStatus` is *not* evidence of probing — Traefik
fills it in either way. `Service.has_health_check` reads the service's
`loadBalancer.healthCheck` instead, which is the only honest signal.

## Errors

All of them derive from `TraefikError`:

| Error | Meaning |
|---|---|
| `TraefikConnectionError` | Could not reach the instance |
| `TraefikAuthenticationError` | 401/403 — allowlist or credentials |
| `TraefikNotFoundError` | The endpoint does not exist here |
| `TraefikResponseError` | The answer was not usable |

## License

MIT
