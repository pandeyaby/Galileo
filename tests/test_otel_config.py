"""Tests for DizzyGraph OTel config + sampling selection (no live export required)."""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")


def test_resolve_sampler_name_defaults(monkeypatch):
    from dizzygraph.otel import resolve_sampler_name

    monkeypatch.delenv("DIZZY_OTEL_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    assert resolve_sampler_name() == "parentbased_always_on"


def test_resolve_sampler_name_dizzy_overrides_otel(monkeypatch):
    from dizzygraph.otel import resolve_sampler_name

    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    monkeypatch.setenv("DIZZY_OTEL_SAMPLER", "traceidratio")
    assert resolve_sampler_name() == "traceidratio"
    assert resolve_sampler_name(sampler="always_off") == "always_off"


def test_resolve_sampler_name_aliases(monkeypatch):
    from dizzygraph.otel import resolve_sampler_name

    monkeypatch.delenv("DIZZY_OTEL_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    assert resolve_sampler_name(sampler="parentbased") == "parentbased_traceidratio"
    assert resolve_sampler_name(sampler="AlwaysOn") == "always_on"


def test_resolve_sampler_arg_clamps(monkeypatch):
    from dizzygraph.otel import resolve_sampler_arg

    monkeypatch.delenv("DIZZY_OTEL_SAMPLER_ARG", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
    assert resolve_sampler_arg(ratio=1.5) == 1.0
    assert resolve_sampler_arg(ratio=-0.2) == 0.0
    monkeypatch.setenv("DIZZY_OTEL_SAMPLER_ARG", "0.25")
    assert resolve_sampler_arg() == 0.25


def test_build_sampler_kinds():
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    from dizzygraph.otel import build_sampler

    assert build_sampler("always_on") is ALWAYS_ON
    assert build_sampler("always_off") is ALWAYS_OFF
    assert isinstance(build_sampler("traceidratio", ratio=0.5), TraceIdRatioBased)
    assert isinstance(build_sampler("parentbased_always_on"), ParentBased)
    assert isinstance(build_sampler("parentbased_always_off"), ParentBased)
    assert isinstance(build_sampler("parentbased_traceidratio", ratio=0.1), ParentBased)


def test_build_sampler_unknown():
    from dizzygraph.otel import build_sampler

    with pytest.raises(ValueError, match="Unknown sampler"):
        build_sampler("not_a_real_sampler")


def test_otel_config_from_env(monkeypatch):
    from dizzygraph.otel import OtelConfig

    monkeypatch.setenv("DIZZY_OTEL", "1")
    monkeypatch.setenv("DIZZY_OTEL_SAMPLER", "traceidratio")
    monkeypatch.setenv("DIZZY_OTEL_SAMPLER_ARG", "0.3")
    monkeypatch.setenv("DIZZY_OTEL_PROCESSOR", "simple")
    monkeypatch.setenv("DIZZY_OTEL_SERVICE_NAME", "cfg-test")
    monkeypatch.setenv("DIZZY_OTEL_CONSOLE", "1")
    monkeypatch.setenv("DIZZY_OTEL_NO_GALILEO", "1")
    monkeypatch.setenv("DIZZY_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "env=test")
    monkeypatch.delenv("GALILEO_API_KEY", raising=False)

    cfg = OtelConfig.from_env()
    assert cfg.sampler == "traceidratio"
    assert cfg.sampler_arg == 0.3
    assert cfg.processor == "simple"
    assert cfg.service_name == "cfg-test"
    assert cfg.console_export is True
    assert cfg.galileo_export is False
    assert cfg.otlp_endpoint == "http://127.0.0.1:4318/v1/traces"
    assert cfg.resource_attributes.get("env") == "test"


def test_setup_tracer_provider_console_only(monkeypatch):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from dizzygraph.otel import reset_otel_provider_state, setup_tracer_provider

    monkeypatch.setenv("DIZZY_OTEL", "1")
    monkeypatch.setenv("DIZZY_OTEL_NO_GALILEO", "1")
    monkeypatch.setenv("DIZZY_OTEL_CONSOLE", "1")
    monkeypatch.setenv("DIZZY_OTEL_SAMPLER", "always_on")
    monkeypatch.setenv("DIZZY_OTEL_PROCESSOR", "simple")
    monkeypatch.delenv("GALILEO_API_KEY", raising=False)
    monkeypatch.delenv("DIZZY_OTEL_OTLP_ENDPOINT", raising=False)

    reset_otel_provider_state()
    provider = setup_tracer_provider(force=True, service_name="unit-test-otel")
    assert isinstance(provider, TracerProvider)
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    # service.name on resource
    attrs = dict(provider.resource.attributes)
    assert attrs.get("service.name") == "unit-test-otel"
    # Do not leave a locked global provider for sibling tests.
    reset_otel_provider_state()


def test_resolve_processor_and_service(monkeypatch):
    from dizzygraph.otel import resolve_processor_kind, resolve_service_name

    monkeypatch.delenv("DIZZY_OTEL_PROCESSOR", raising=False)
    assert resolve_processor_kind() == "batch"
    monkeypatch.setenv("DIZZY_OTEL_PROCESSOR", "simple")
    assert resolve_processor_kind() == "simple"

    monkeypatch.delenv("DIZZY_OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    assert resolve_service_name() == "dizzygraph"
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-otel")
    assert resolve_service_name() == "from-otel"
    monkeypatch.setenv("DIZZY_OTEL_SERVICE_NAME", "from-dizzy")
    assert resolve_service_name() == "from-dizzy"
