"""Tests for the HTTP client.

The client's job is to turn every failure into one of five typed errors, so
each status code and transport failure gets a case. The awkward one is a 200
carrying HTML: an auth portal in front of Traefik answers exactly that, and
trusting Content-Type would report it as a parse bug in Traefik.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp import web

from traefik import (
    TraefikAuthenticationError,
    TraefikClient,
    TraefikConnectionError,
    TraefikNotFoundError,
    TraefikResponseError,
)

from .conftest import FakeTraefik

VERSION = "/api/version"
ROUTERS = "/api/http/routers"
SERVICES = "/api/http/services"
ENTRYPOINTS = "/api/entrypoints"
OVERVIEW = "/api/overview"


class TestSuccessfulRequests:
    """The happy paths, one per endpoint."""

    async def test_get_version_returns_the_banner(
        self, client: TraefikClient, api: FakeTraefik, version_payload: dict
    ) -> None:
        api.json(VERSION, version_payload)
        info = await client.get_version()
        assert info.version == "3.7.10"
        assert info.codename == "saintnectaire"

    async def test_get_overview_returns_the_counts(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(OVERVIEW, {"http": {"routers": {"total": 9, "warnings": 0, "errors": 1}}})
        overview = await client.get_overview()
        assert overview.http_routers.total == 9
        assert overview.http_routers.errors == 1

    async def test_list_entrypoints_builds_one_model_per_entry(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(ENTRYPOINTS, [{"name": "web", "address": ":80"}, {"name": "websecure", "address": ":443"}])
        assert [e.name for e in await client.list_entrypoints()] == ["web", "websecure"]

    async def test_list_routers_builds_one_model_per_entry(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(
            ROUTERS,
            [
                {"name": "a@file", "rule": "Host(`a.test`)", "status": "enabled"},
                {"name": "b@file", "rule": "Host(`b.test`)", "status": "enabled"},
            ],
        )
        routers = await client.list_routers()
        assert [r.name for r in routers] == ["a@file", "b@file"]
        assert routers[0].hostnames == ("a.test",)

    async def test_list_services_builds_one_model_per_entry(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(SERVICES, [{"name": "s@file", "status": "enabled"}])
        assert [s.name for s in await client.list_services()] == ["s@file"]

    async def test_a_non_object_entry_in_a_list_is_skipped(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(ROUTERS, [{"name": "a@file"}, "junk", None])
        assert len(await client.list_routers()) == 1

    async def test_an_empty_list_is_not_an_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(ROUTERS, [])
        assert await client.list_routers() == []


class TestErrorMapping:
    """Every failure has exactly one type."""

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_request_is_an_authentication_error(
        self, client: TraefikClient, api: FakeTraefik, status: int
    ) -> None:
        api.status(VERSION, status)
        with pytest.raises(TraefikAuthenticationError):
            await client.get_version()

    async def test_the_authentication_error_names_the_actual_cause(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        # Traefik's API has no login of its own. A 403 means an ipAllowList or
        # a basicAuth middleware, and the message has to say so, or the user
        # goes hunting for a password that does not exist.
        api.status(VERSION, 403)
        with pytest.raises(TraefikAuthenticationError, match="ipAllowList"):
            await client.get_version()

    async def test_a_missing_endpoint_is_a_not_found_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.status(VERSION, 404)
        with pytest.raises(TraefikNotFoundError):
            await client.get_version()

    async def test_a_server_error_is_a_response_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.status(VERSION, 500)
        with pytest.raises(TraefikResponseError, match="500"):
            await client.get_version()

    async def test_a_timeout_is_a_connection_error(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        async def slow(_request: web.Request) -> web.StreamResponse:
            await asyncio.sleep(5)
            return web.json_response({})

        api.handle(VERSION, slow)
        client = TraefikClient(api.url, session=session, timeout=0.1)
        with pytest.raises(TraefikConnectionError, match="Timeout"):
            await client.get_version()

    async def test_an_unreachable_host_is_a_connection_error(
        self, session: aiohttp.ClientSession, closed_port: str
    ) -> None:
        client = TraefikClient(closed_port, session=session)
        with pytest.raises(TraefikConnectionError, match="Cannot reach"):
            await client.get_version()


class TestUnusablePayloads:
    """A 200 is not proof that the address is really Traefik."""

    async def test_html_behind_a_login_portal_is_a_response_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.text(VERSION, "<html><body>Please sign in</body></html>", "text/html")
        with pytest.raises(TraefikResponseError, match="really the Traefik API"):
            await client.get_version()

    async def test_json_with_the_wrong_content_type_is_still_accepted(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        # Some proxies rewrite Content-Type. The body is what matters.
        api.text(VERSION, '{"Version": "3.7.10"}', "text/plain")
        assert (await client.get_version()).version == "3.7.10"

    async def test_a_list_where_an_object_belongs_is_a_response_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(VERSION, [1, 2, 3])
        with pytest.raises(TraefikResponseError):
            await client.get_version()

    async def test_an_object_where_a_list_belongs_is_a_response_error(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(ROUTERS, {"error": "nope"})
        with pytest.raises(TraefikResponseError):
            await client.list_routers()


class TestMetricsEndpoint:
    """Metrics are optional, and absence is not an error."""

    async def test_no_metrics_address_returns_an_empty_result(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        client = TraefikClient(api.url, session=session)
        metrics = await client.get_metrics()
        assert metrics.open_connections is None
        assert metrics.services == {}

    async def test_no_metrics_address_makes_no_request_at_all(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        client = TraefikClient(api.url, session=session)
        await client.get_metrics()
        assert api.requests == []

    async def test_metrics_are_read_and_parsed(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.text("/metrics", 'traefik_open_connections{entrypoint="web"} 4\n')
        metrics = await client.get_metrics()
        assert metrics.open_connections == 4

    async def test_a_refused_metrics_endpoint_still_raises(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.status("/metrics", 403)
        with pytest.raises(TraefikAuthenticationError):
            await client.get_metrics()


class TestSessionOwnership:
    """close() must only close what the client created."""

    async def test_a_borrowed_session_survives_close(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        client = TraefikClient(api.url, session=session)
        await client.close()
        assert session.closed is False

    async def test_an_owned_session_is_closed(self, api: FakeTraefik) -> None:
        api.json(VERSION, {"Version": "3"})
        client = TraefikClient(api.url)
        await client.get_version()
        owned = client._session  # noqa: SLF001  ownership is the thing under test
        assert owned is not None
        await client.close()
        assert owned.closed is True

    async def test_the_context_manager_closes_an_owned_session(
        self, api: FakeTraefik
    ) -> None:
        api.json(VERSION, {"Version": "3"})
        async with TraefikClient(api.url) as client:
            await client.get_version()
            owned = client._session  # noqa: SLF001
        assert owned is not None
        assert owned.closed is True

    async def test_close_is_safe_to_call_twice(self, api: FakeTraefik) -> None:
        client = TraefikClient(api.url)
        await client.close()
        await client.close()


class TestAddressHandling:
    """A base URL with or without a trailing slash must reach the same place."""

    @pytest.mark.parametrize("suffix", ["", "/"])
    async def test_a_trailing_slash_does_not_change_the_target(
        self, api: FakeTraefik, session: aiohttp.ClientSession, suffix: str
    ) -> None:
        api.json(VERSION, {"Version": "3.7.10"})
        client = TraefikClient(api.url + suffix, session=session)
        assert (await client.get_version()).version == "3.7.10"
        assert api.requests[-1].path == VERSION

    async def test_a_base_path_is_preserved(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        # Traefik behind a path-stripping proxy is reached at /traefik/api/...
        api.json("/traefik/api/version", {"Version": "3.7.10"})
        client = TraefikClient(f"{api.url}/traefik", session=session)
        assert (await client.get_version()).version == "3.7.10"

    async def test_credentials_become_a_basic_auth_header(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        api.json(VERSION, {"Version": "3.7.10"})
        client = TraefikClient(api.url, username="u", password="p", session=session)
        await client.get_version()
        header = api.requests[-1].headers.get("Authorization", "")
        assert aiohttp.BasicAuth.decode(header) == aiohttp.BasicAuth("u", "p")

    async def test_no_credentials_send_no_authorization_header(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(VERSION, {"Version": "3.7.10"})
        await client.get_version()
        assert "Authorization" not in api.requests[-1].headers

    async def test_a_username_without_a_password_still_authenticates(
        self, api: FakeTraefik, session: aiohttp.ClientSession
    ) -> None:
        api.json(VERSION, {"Version": "3.7.10"})
        client = TraefikClient(api.url, username="u", session=session)
        await client.get_version()
        header = api.requests[-1].headers.get("Authorization", "")
        assert aiohttp.BasicAuth.decode(header) == aiohttp.BasicAuth("u", "")

    async def test_json_is_asked_for_by_the_accept_header(
        self, client: TraefikClient, api: FakeTraefik
    ) -> None:
        api.json(VERSION, {"Version": "3"})
        await client.get_version()
        assert api.requests[-1].headers["Accept"] == "application/json"
