"""Tests for the Prometheus reader.

The parser reads a fixed set of families by exact name. The cases below cover
what that costs: histogram buckets share a prefix with families we want, the
sans label contains commas, and a counter Traefik has not filled in yet reads
as zero rather than as missing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from traefik import parse_metrics

SAMPLE = """
# HELP traefik_open_connections How many open connections exist.
# TYPE traefik_open_connections gauge
traefik_open_connections{entrypoint="web",method="GET",protocol="http"} 3
traefik_open_connections{entrypoint="websecure",method="GET",protocol="http"} 5
traefik_config_reloads_total 7
traefik_config_last_reload_success 1.7566e+09
traefik_tls_certs_not_after{cn="a.example.test",sans="a.example.test,www.example.test",serial="01"} 1.9e+09
traefik_service_requests_total{code="200",service="api@file"} 100
traefik_service_requests_total{code="404",service="api@file"} 7
traefik_service_requests_total{code="503",service="api@file"} 3
traefik_service_requests_total{code="0",service="api@file"} 11
traefik_service_request_duration_seconds_sum{service="api@file"} 22.0
traefik_service_request_duration_seconds_count{service="api@file"} 110
traefik_service_request_duration_seconds_bucket{service="api@file",le="0.1"} 90
traefik_service_request_duration_seconds_bucket{service="api@file",le="+Inf"} 110
traefik_entrypoint_requests_total{code="200",entrypoint="websecure"} 40
traefik_entrypoint_request_duration_seconds_sum{entrypoint="websecure"} 4.0
traefik_entrypoint_request_duration_seconds_count{entrypoint="websecure"} 40
go_goroutines 42
"""


@pytest.fixture(name="metrics")
def fixture_metrics():
    """The sample page, parsed once."""
    return parse_metrics(SAMPLE)


class TestCounters:
    """Top-level gauges and counters."""

    def test_open_connections_sum_across_series(self, metrics) -> None:
        assert metrics.open_connections == 8

    def test_connections_are_also_kept_per_entrypoint(self, metrics) -> None:
        assert metrics.connections_by_entrypoint == {"web": 3, "websecure": 5}

    def test_config_reloads_are_read(self, metrics) -> None:
        assert metrics.config_reloads == 7

    def test_the_last_reload_becomes_a_datetime(self, metrics) -> None:
        assert metrics.last_reload == datetime.fromtimestamp(1.7566e9, tz=UTC)


class TestZeroTimestamps:
    """Traefik writes 0 before the first reload, and 0 is not a date."""

    def test_a_zero_reload_time_is_absent(self) -> None:
        metrics = parse_metrics("traefik_config_last_reload_success 0\n")
        assert metrics.last_reload is None

    def test_a_zero_certificate_expiry_is_dropped(self) -> None:
        metrics = parse_metrics('traefik_tls_certs_not_after{cn="a.test"} 0\n')
        assert metrics.certificates == ()


class TestCertificates:
    """The sans label is the awkward one: its value contains commas."""

    def test_the_common_name_is_read(self, metrics) -> None:
        assert [c.common_name for c in metrics.certificates] == ["a.example.test"]

    def test_a_comma_inside_the_sans_value_does_not_split_the_labels(
        self, metrics
    ) -> None:
        cert = metrics.certificates[0]
        assert cert.sans == ("a.example.test", "www.example.test")

    def test_the_expiry_is_a_datetime(self, metrics) -> None:
        assert metrics.certificates[0].not_after.tzinfo is UTC


class TestTrafficAccumulation:
    """Requests are bucketed by response class."""

    def test_every_response_counts_towards_requests(self, metrics) -> None:
        stats = metrics.services["api@file"]
        assert stats.requests == 121

    def test_4xx_and_5xx_are_separated(self, metrics) -> None:
        stats = metrics.services["api@file"]
        assert stats.client_errors == 7
        assert stats.server_errors == 3

    def test_code_zero_is_not_an_error(self, metrics) -> None:
        # Traefik reports code="0" for connections that never produced a
        # status, websocket upgrades mostly. Counting those as failures would
        # give every websocket service a permanent error rate.
        stats = metrics.services["api@file"]
        assert stats.errors == 10

    def test_duration_comes_from_sum_and_count_not_buckets(self, metrics) -> None:
        stats = metrics.services["api@file"]
        assert stats.duration_count == 110
        assert stats.average_duration == pytest.approx(0.2)

    def test_entrypoints_accumulate_the_same_way(self, metrics) -> None:
        stats = metrics.entrypoints["websecure"]
        assert stats.requests == 40
        assert stats.average_duration == pytest.approx(0.1)


class TestWhatIsIgnored:
    """Everything outside the wanted set is skipped without being understood."""

    def test_histogram_buckets_do_not_reach_the_totals(self, metrics) -> None:
        # The bucket series share the duration prefix. If prefix matching were
        # used, api@file would report 110 + 90 + 110 observations.
        assert metrics.services["api@file"].duration_count == 110

    def test_non_traefik_families_are_skipped(self, metrics) -> None:
        assert "go_goroutines" not in metrics.services
        assert "go_goroutines" not in metrics.entrypoints

    def test_an_unreadable_line_does_not_cost_the_rest_of_the_page(self) -> None:
        metrics = parse_metrics(
            "traefik_config_reloads_total not-a-number\n"
            "traefik_open_connections{entrypoint=\"web\"} 2\n"
        )
        assert metrics.config_reloads is None
        assert metrics.open_connections == 2

    def test_an_empty_page_parses_to_an_empty_result(self) -> None:
        metrics = parse_metrics("")
        assert metrics.open_connections is None
        assert metrics.certificates == ()
        assert metrics.services == {}
