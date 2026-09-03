"""Tests for the typed models.

Every case here comes from a shape Traefik actually emits: the zero timestamp
it reports before startup finishes, the string "false" it writes for a disabled
feature, and the serverStatus map it fills with UP for servers it never probes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from traefik import (
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


class TestServerInfo:
    """The version banner."""

    def test_parses_a_z_suffixed_timestamp(self, version_payload: dict) -> None:
        info = ServerInfo.from_api(version_payload)
        assert info.version == "3.7.10"
        assert info.codename == "saintnectaire"
        assert info.start_date is not None
        assert info.start_date.year == 2026

    def test_zero_time_is_an_absence_not_a_date(self) -> None:
        # Traefik answers with the Go zero time while it is still starting.
        info = ServerInfo.from_api({"Version": "3.7.10", "startDate": "0001-01-01T00:00:00Z"})
        assert info.start_date is None

    def test_missing_fields_do_not_raise(self) -> None:
        info = ServerInfo.from_api({})
        assert info.version == ""
        assert info.codename is None
        assert info.start_date is None

    def test_unparsable_timestamp_is_none(self) -> None:
        info = ServerInfo.from_api({"Version": "3", "startDate": "not a date"})
        assert info.start_date is None


class TestSectionCounts:
    """Object counts per protocol."""

    def test_reads_the_three_counters(self) -> None:
        counts = SectionCounts.from_api({"total": 12, "warnings": 1, "errors": 2})
        assert (counts.total, counts.warnings, counts.errors) == (12, 1, 2)

    def test_a_missing_section_counts_as_zero(self) -> None:
        # /api/overview omits a protocol entirely when nothing uses it.
        counts = SectionCounts.from_api(None)
        assert (counts.total, counts.warnings, counts.errors) == (0, 0, 0)


class TestOverview:
    """The dashboard summary."""

    def test_the_string_false_means_the_feature_is_off(self) -> None:
        overview = Overview.from_api(
            {"features": {"metrics": "false", "tracing": "false", "accessLog": False}}
        )
        assert overview.metrics_provider is None
        assert overview.tracing_provider is None
        assert overview.access_log is False

    def test_a_named_provider_is_reported(self) -> None:
        overview = Overview.from_api(
            {
                "features": {"metrics": "prometheus", "tracing": "otlp", "accessLog": True},
                "providers": ["File", "Docker"],
            }
        )
        assert overview.metrics_provider == "prometheus"
        assert overview.tracing_provider == "otlp"
        assert overview.access_log is True
        assert overview.providers == ("File", "Docker")

    def test_certificates_are_counted_not_listed(self) -> None:
        overview = Overview.from_api({"certificates": [{"cn": "a"}, {"cn": "b"}]})
        assert overview.certificates == 2

    def test_an_empty_payload_yields_zeroes(self) -> None:
        overview = Overview.from_api({})
        assert overview.http_routers.total == 0
        assert overview.providers == ()
        assert overview.certificates == 0


class TestEntryPoint:
    """Listening ports."""

    def test_http2_and_udp_are_presence_flags(self) -> None:
        entrypoint = EntryPoint.from_api(
            {"name": "websecure", "address": ":443", "http2": {"maxConcurrentStreams": 250}}
        )
        assert entrypoint.http2 is True
        assert entrypoint.udp is False


class TestRouterHostnames:
    """Hostname extraction from a router rule."""

    def test_reads_one_host(self) -> None:
        router = Router.from_api({"name": "a@file", "rule": "Host(`example.test`)"})
        assert router.hostnames == ("example.test",)

    def test_reads_several_hosts_from_one_matcher(self) -> None:
        router = Router.from_api({"rule": "Host(`a.test`, `b.test`)"})
        assert router.hostnames == ("a.test", "b.test")

    def test_reads_hosts_across_a_chained_rule(self) -> None:
        router = Router.from_api(
            {"rule": "Host(`a.test`) && PathPrefix(`/api`) || Host(`b.test`)"}
        )
        assert router.hostnames == ("a.test", "b.test")

    def test_hostsni_counts_as_a_hostname(self) -> None:
        router = Router.from_api({"rule": "HostSNI(`a.test`)"})
        assert router.hostnames == ("a.test",)

    def test_hostregexp_is_a_pattern_and_is_skipped(self) -> None:
        # Reporting ^.+\.example\.com$ as a hostname is worse than reporting none.
        router = Router.from_api({"rule": "HostRegexp(`^.+[.]example[.]test$`)"})
        assert router.hostnames == ()

    def test_a_repeated_host_appears_once(self) -> None:
        router = Router.from_api({"rule": "Host(`a.test`) || Host(`a.test`)"})
        assert router.hostnames == ("a.test",)

    def test_a_rule_without_a_host_yields_nothing(self) -> None:
        router = Router.from_api({"rule": "PathPrefix(`/api`)"})
        assert router.hostnames == ()

    def test_no_rule_at_all_yields_nothing(self) -> None:
        assert Router.from_api({}).hostnames == ()


class TestRouter:
    """The rest of the router model."""

    def test_enabled_follows_the_status_field(self) -> None:
        assert Router.from_api({"status": "enabled"}).enabled is True
        assert Router.from_api({"status": "warning"}).enabled is False

    def test_tls_is_a_presence_flag(self) -> None:
        assert Router.from_api({"tls": {}}).tls is True
        assert Router.from_api({}).tls is False

    def test_a_non_integer_priority_is_dropped(self) -> None:
        assert Router.from_api({"priority": "high"}).priority is None
        assert Router.from_api({"priority": 42}).priority == 42


class TestService:
    """Backend health, which is only meaningful with a health check."""

    def test_without_a_health_check_the_answer_is_unknown(self) -> None:
        # Traefik writes UP for servers it never probes, so UP proves nothing.
        service = Service.from_api(
            {"name": "s@file", "serverStatus": {"http://10.0.0.1:80": "UP"}}
        )
        assert service.has_health_check is False
        assert service.all_servers_up is None

    def test_with_a_health_check_every_server_must_be_up(self) -> None:
        service = Service.from_api(
            {
                "loadBalancer": {"healthCheck": {"path": "/health"}},
                "serverStatus": {"http://10.0.0.1:80": "UP", "http://10.0.0.2:80": "UP"},
            }
        )
        assert service.all_servers_up is True

    def test_one_down_server_fails_the_whole_service(self) -> None:
        service = Service.from_api(
            {
                "loadBalancer": {"healthCheck": {"path": "/health"}},
                "serverStatus": {"http://10.0.0.1:80": "UP", "http://10.0.0.2:80": "DOWN"},
            }
        )
        assert service.all_servers_up is False

    def test_a_health_check_with_no_servers_is_unknown(self) -> None:
        service = Service.from_api({"loadBalancer": {"healthCheck": {}}, "serverStatus": {}})
        assert service.all_servers_up is None


class TestCertificate:
    """Certificate matching and expiry."""

    @pytest.fixture(name="wildcard")
    def fixture_wildcard(self) -> Certificate:
        return Certificate(
            common_name="*.example.test",
            not_after=datetime.now(UTC) + timedelta(days=30),
        )

    def test_an_exact_name_matches(self) -> None:
        cert = Certificate("a.example.test", datetime.now(UTC))
        assert cert.covers("a.example.test") is True

    def test_matching_ignores_case_and_a_trailing_dot(self) -> None:
        cert = Certificate("A.Example.Test", datetime.now(UTC))
        assert cert.covers("a.example.test.") is True

    def test_a_wildcard_matches_one_label(self, wildcard: Certificate) -> None:
        assert wildcard.covers("a.example.test") is True

    def test_a_wildcard_does_not_match_two_labels(self, wildcard: Certificate) -> None:
        # X.509 wildcards cover exactly one label, and pretending otherwise
        # would report an expiry for a name this certificate cannot serve.
        assert wildcard.covers("a.b.example.test") is False

    def test_a_wildcard_does_not_match_the_bare_parent(self, wildcard: Certificate) -> None:
        assert wildcard.covers("example.test") is False

    def test_a_san_matches_too(self) -> None:
        cert = Certificate("a.test", datetime.now(UTC), sans=("b.test", "c.test"))
        assert cert.covers("b.test") is True

    def test_days_remaining_is_negative_once_expired(self) -> None:
        cert = Certificate("a.test", datetime.now(UTC) - timedelta(days=3))
        assert cert.days_remaining < 0


class TestTrafficStats:
    """Derived traffic numbers."""

    def test_errors_add_both_classes(self) -> None:
        stats = TrafficStats(requests=100, client_errors=3, server_errors=2)
        assert stats.errors == 5
        assert stats.error_rate == pytest.approx(5.0)

    def test_no_traffic_means_no_rate_rather_than_zero(self) -> None:
        stats = TrafficStats()
        assert stats.error_rate is None
        assert stats.average_duration is None

    def test_average_duration_is_the_lifetime_mean(self) -> None:
        stats = TrafficStats(duration_total=5.0, duration_count=10)
        assert stats.average_duration == pytest.approx(0.5)


class TestMetricsHelpers:
    """Lookups over the parsed metrics."""

    @pytest.fixture(name="metrics")
    def fixture_metrics(self) -> Metrics:
        now = datetime.now(UTC)
        return Metrics(
            certificates=(
                Certificate("*.example.test", now + timedelta(days=10)),
                Certificate("a.example.test", now + timedelta(days=60)),
                Certificate("other.test", now + timedelta(days=5)),
            ),
            entrypoints={
                "web": TrafficStats(requests=10, client_errors=1, duration_total=1.0, duration_count=10),
                "websecure": TrafficStats(requests=90, server_errors=9, duration_total=9.0, duration_count=90),
            },
        )

    def test_nearest_expiry_is_the_soonest(self, metrics: Metrics) -> None:
        nearest = metrics.nearest_expiry
        assert nearest is not None
        assert nearest.common_name == "other.test"

    def test_an_exact_certificate_beats_a_wildcard_that_also_covers(
        self, metrics: Metrics
    ) -> None:
        # Both cover a.example.test. Reporting the wildcard's earlier expiry
        # would raise an alarm about a certificate this host does not use.
        match = metrics.certificate_for("a.example.test")
        assert match is not None
        assert match.common_name == "a.example.test"

    def test_a_wildcard_is_used_when_nothing_exact_exists(self, metrics: Metrics) -> None:
        match = metrics.certificate_for("b.example.test")
        assert match is not None
        assert match.common_name == "*.example.test"

    def test_an_uncovered_hostname_has_no_certificate(self, metrics: Metrics) -> None:
        assert metrics.certificate_for("nothing.invalid") is None

    def test_totals_sum_the_entrypoints(self, metrics: Metrics) -> None:
        totals = metrics.totals()
        assert totals.requests == 100
        assert totals.errors == 10
        assert totals.average_duration == pytest.approx(0.1)

    def test_totals_of_nothing_are_empty(self) -> None:
        assert Metrics().totals().requests == 0
        assert Metrics().nearest_expiry is None
