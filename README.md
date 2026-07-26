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
            print(router.name, router.rule, router.status)


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

print(metrics.open_connections)
print(metrics.last_reload)
for cert in metrics.certificates:
    print(cert.common_name, cert.days_remaining, "days left")
```

Only four metric families are read — open connections, config reloads, last
successful reload, and per-certificate expiry. The rest of Traefik's output is
per-route latency histograms and is skipped.

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
