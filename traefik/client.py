"""Async client for the Traefik API."""

from __future__ import annotations

import asyncio
import socket
from types import TracebackType
from typing import Any, Self

import aiohttp
from yarl import URL

from .constants import (
    DEFAULT_TIMEOUT,
    EP_ENTRYPOINTS,
    EP_HTTP_ROUTERS,
    EP_HTTP_SERVICES,
    EP_METRICS,
    EP_OVERVIEW,
    EP_VERSION,
)
from .exceptions import (
    TraefikAuthenticationError,
    TraefikConnectionError,
    TraefikNotFoundError,
    TraefikResponseError,
)
from .metrics import parse_metrics
from .models import EntryPoint, Metrics, Overview, Router, ServerInfo, Service

__all__ = ["TraefikClient"]


class TraefikClient:
    """Talk to one Traefik instance.

    A client is safe to reuse across many requests; it holds no state beyond
    the session and the addresses.

    Traefik's API has no authentication of its own. Operators protect it with
    an ipAllowList or a basicAuth middleware, so optional credentials are
    accepted for the latter case.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        metrics_url: str | None = None,
        session: aiohttp.ClientSession | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
    ) -> None:
        """Configure the client.

        ``metrics_url`` is separate because Traefik's Prometheus exporter is
        normally published on its own entrypoint, on a different port from the
        API. Leave it unset and no metrics are read.

        Pass ``session`` to reuse an existing one; the caller keeps ownership
        of it and ``close()`` will leave it open.
        """
        self._base = URL(base_url.rstrip("/") + "/")
        self._metrics_base = (
            URL(metrics_url.rstrip("/") + "/") if metrics_url else None
        )
        self._auth = (
            aiohttp.BasicAuth(username, password or "")
            if username
            else None
        )
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._verify_ssl = verify_ssl

    async def __aenter__(self) -> Self:
        """Enter the context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the context manager, closing an owned session."""
        await self.close()

    async def close(self) -> None:
        """Close the session, if this client created it."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _fetch(self, url: URL, accept: str) -> aiohttp.ClientResponse:
        """Perform one GET and raise a typed error for anything unusable."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        try:
            response = await self._session.get(
                url,
                headers={"Accept": accept},
                auth=self._auth,
                timeout=self._timeout,
                ssl=self._verify_ssl,
            )
        except asyncio.TimeoutError as err:
            raise TraefikConnectionError(f"Timeout talking to {url.host}") from err
        except (aiohttp.ClientError, socket.gaierror) as err:
            raise TraefikConnectionError(f"Cannot reach {url.host}: {err}") from err

        if response.status in (401, 403):
            response.release()
            raise TraefikAuthenticationError(
                "Traefik refused the request. Its API is usually restricted by "
                "an ipAllowList or basicAuth middleware; allow this host, or "
                "supply the right credentials"
            )
        if response.status == 404:
            response.release()
            raise TraefikNotFoundError(f"Not found: {url.path}")
        if response.status >= 400:
            response.release()
            raise TraefikResponseError(
                f"Traefik returned HTTP {response.status} for {url.path}"
            )
        return response

    async def _request(self, path: str) -> Any:
        """Perform one GET against the API and return decoded JSON."""
        url = self._base.join(URL(path.lstrip("/")))
        response = await self._fetch(url, "application/json")
        async with response:
            # A reverse proxy that intercepts the request — an auth portal, a
            # captive login page — answers 200 with HTML. Decoding without
            # trusting Content-Type keeps the error message honest.
            try:
                return await response.json(content_type=None)
            except ValueError as err:
                raise TraefikResponseError(
                    f"Expected JSON from {path}; got something else. Is this "
                    "address really the Traefik API?"
                ) from err

    async def get_version(self) -> ServerInfo:
        """Return the version banner."""
        data = await self._request(EP_VERSION)
        if not isinstance(data, dict):
            raise TraefikResponseError("Unexpected payload for the version")
        return ServerInfo.from_api(data)

    async def get_overview(self) -> Overview:
        """Return the dashboard summary of object counts and features."""
        data = await self._request(EP_OVERVIEW)
        if not isinstance(data, dict):
            raise TraefikResponseError("Unexpected payload for the overview")
        return Overview.from_api(data)

    async def list_entrypoints(self) -> list[EntryPoint]:
        """Return every configured entrypoint."""
        data = await self._request(EP_ENTRYPOINTS)
        if not isinstance(data, list):
            raise TraefikResponseError("Unexpected payload for entrypoints")
        return [EntryPoint.from_api(item) for item in data if isinstance(item, dict)]

    async def list_routers(self) -> list[Router]:
        """Return every HTTP router."""
        data = await self._request(EP_HTTP_ROUTERS)
        if not isinstance(data, list):
            raise TraefikResponseError("Unexpected payload for routers")
        return [Router.from_api(item) for item in data if isinstance(item, dict)]

    async def list_services(self) -> list[Service]:
        """Return every HTTP service."""
        data = await self._request(EP_HTTP_SERVICES)
        if not isinstance(data, list):
            raise TraefikResponseError("Unexpected payload for services")
        return [Service.from_api(item) for item in data if isinstance(item, dict)]

    async def get_metrics(self) -> Metrics:
        """Read the Prometheus endpoint, if one was configured.

        Returns an empty ``Metrics`` when no metrics address was given, so
        callers do not have to branch on the configuration.
        """
        if self._metrics_base is None:
            return Metrics()
        url = self._metrics_base.join(URL(EP_METRICS.lstrip("/")))
        response = await self._fetch(url, "text/plain")
        async with response:
            return parse_metrics(await response.text())
