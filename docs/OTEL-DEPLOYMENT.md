# OpenTelemetry deployment patterns (DizzyGraph × Galileo)

How to run real OTel export from DizzyGraph in production — sampling, multi-backend
fan-out, processors, resource attributes, and env-driven config.

Library: [`dizzygraph/otel.py`](../dizzygraph/otel.py)  
Fleet wiring: control plane `FleetRuntime` calls `setup_tracer_provider` when
`otel_enabled()` (optional Trinity / fleet path).

```bash
pip install 'dizzygraph[otel]'
# secondary OTLP (collector / Jaeger / etc.):
pip install opentelemetry-exporter-otlp-proto-http
```

---

## Enablement

| Env | Effect |
|-----|--------|
| `DIZZY_OTEL=0` / `false` | Force off |
| `DIZZY_OTEL=1` / `true` | Force on if SDK installed |
| *(unset)* | Auto-on when `GALILEO_API_KEY` + OTel packages present |

---

## Sampling

Head sampling at the SDK (before export). Prefer **parentbased** variants in
multi-service meshes so child spans follow the root decision.

| Name | Env value | Notes |
|------|-----------|--------|
| Always on | `always_on` | Dev / low volume |
| Always off | `always_off` | Kill switch |
| Trace ID ratio | `traceidratio` | Uses sampler arg `0.0`–`1.0` |
| Parent-based always on | `parentbased_always_on` | **Default** |
| Parent-based always off | `parentbased_always_off` | |
| Parent-based ratio | `parentbased_traceidratio` | Production default for high QPS |

```bash
# Standard OTel (honored when DIZZY_OTEL_SAMPLER unset):
export OTEL_TRACES_SAMPLER=parentbased_traceidratio
export OTEL_TRACES_SAMPLER_ARG=0.1

# Dizzy overrides (win over OTEL_*):
export DIZZY_OTEL_SAMPLER=parentbased_traceidratio
export DIZZY_OTEL_SAMPLER_ARG=0.25
```

Python:

```python
from dizzygraph.otel import build_sampler, resolve_sampler_name, setup_tracer_provider

assert resolve_sampler_name(sampler="traceidratio") == "traceidratio"
setup_tracer_provider(sampler="parentbased_traceidratio", sampler_arg=0.1)
```

Tail sampling belongs in an **OpenTelemetry Collector** gateway (not the SDK).
Use SDK parentbased + collector `tail_sampling` for keep-on-error policies.

---

## Batch vs simple span processor

| Processor | Env | When |
|-----------|-----|------|
| **Batch** (default) | `DIZZY_OTEL_PROCESSOR=batch` | Production — buffers, lower overhead |
| **Simple** | `DIZZY_OTEL_PROCESSOR=simple` | Tests / debug — export on every end |

Galileo's `GalileoSpanProcessor` registers its own internal batching. Console /
secondary OTLP exporters honor `DIZZY_OTEL_PROCESSOR`.

---

## Resource attributes / `service.name`

```bash
export OTEL_SERVICE_NAME=my-agent-fleet
# or
export DIZZY_OTEL_SERVICE_NAME=my-agent-fleet

export OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod,team=platform
# Dizzy merge (additive):
export DIZZY_OTEL_RESOURCE_ATTRS=dizzygraph.fleet=trinity
```

Control plane sets `service.name` default `dizzygraph-control` plus
`dizzygraph.component=control-plane` and `dizzygraph.tenant_id`.

---

## Multi-backend export

One provider, multiple exporters:

| Backend | How to enable |
|---------|----------------|
| **Galileo** (primary) | `GALILEO_API_KEY` + `galileo[otel]` (default when key present) |
| **Console** | `DIZZY_OTEL_CONSOLE=1` |
| **Secondary OTLP HTTP** | `DIZZY_OTEL_OTLP_ENDPOINT=http://localhost:4318/v1/traces` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` |
| Disable Galileo only | `DIZZY_OTEL_NO_GALILEO=1` (still console/OTLP) |

```bash
export GALILEO_API_KEY=...
export GALILEO_PROJECT=rax-galileo-labs
export GALILEO_LOG_STREAM=otel-prod

# Fan-out to local collector as well:
export DIZZY_OTEL_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
export DIZZY_OTEL_CONSOLE=0
export DIZZY_OTEL_PROCESSOR=batch
export DIZZY_OTEL_SAMPLER=parentbased_traceidratio
export DIZZY_OTEL_SAMPLER_ARG=0.2
```

Self-hosted Galileo: set `GALILEO_API_ENDPOINT` (Cloud default OTLP is
`https://api.galileo.ai/otel/traces`; `GalileoSpanProcessor` picks this up).

---

## Env matrix (quick reference)

| Variable | Role |
|----------|------|
| `DIZZY_OTEL` | Force on/off |
| `DIZZY_OTEL_SAMPLER` | Sampler name (overrides `OTEL_TRACES_SAMPLER`) |
| `DIZZY_OTEL_SAMPLER_ARG` | Ratio `0`–`1` (overrides `OTEL_TRACES_SAMPLER_ARG`) |
| `DIZZY_OTEL_PROCESSOR` | `batch` \| `simple` |
| `DIZZY_OTEL_SERVICE_NAME` | Overrides `OTEL_SERVICE_NAME` |
| `DIZZY_OTEL_RESOURCE_ATTRS` | Extra `k=v,k2=v2` attrs |
| `DIZZY_OTEL_CONSOLE` | Attach console exporter |
| `DIZZY_OTEL_OTLP_ENDPOINT` | Secondary OTLP HTTP traces URL |
| `DIZZY_OTEL_NO_GALILEO` | Skip Galileo exporter |
| `OTEL_SERVICE_NAME` | Standard service name |
| `OTEL_RESOURCE_ATTRIBUTES` | Standard resource attrs |
| `OTEL_TRACES_SAMPLER` / `_ARG` | Standard sampler |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `_TRACES_ENDPOINT` | Secondary OTLP |
| `OTEL_EXPORTER_OTLP_HEADERS` | `k=v,k2=v2` for secondary OTLP |
| `GALILEO_API_KEY` / `GALILEO_PROJECT` / `GALILEO_LOG_STREAM` | Galileo destination |
| `GALILEO_API_ENDPOINT` | Self-hosted Galileo API root |

---

## Patterns

### 1. Dev — always on + console

```bash
export DIZZY_OTEL=1
export DIZZY_OTEL_SAMPLER=always_on
export DIZZY_OTEL_PROCESSOR=simple
export DIZZY_OTEL_CONSOLE=1
export DIZZY_OTEL_NO_GALILEO=1
```

### 2. Prod — Galileo + ratio sampling

```bash
export GALILEO_API_KEY=...
export DIZZY_OTEL_SAMPLER=parentbased_traceidratio
export DIZZY_OTEL_SAMPLER_ARG=0.1
export DIZZY_OTEL_PROCESSOR=batch
export OTEL_SERVICE_NAME=dizzygraph-prod
```

### 3. Dual export — Galileo + collector gateway

```bash
export GALILEO_API_KEY=...
export DIZZY_OTEL_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
export DIZZY_OTEL_SAMPLER=parentbased_always_on
```

Collector gateway can fan-out further and apply tail sampling.

### 4. Fleet / Trinity (control plane)

```bash
pip install -e '.[control,otel]'
export OPENAI_API_KEY=... GALILEO_API_KEY=...
export DIZZY_OTEL=1
export DIZZY_OTEL_SERVICE_NAME=dizzygraph-control
python -m dizzygraph.control --trinity 4 --port 8800
```

Spans: `dizzygraph.<graph_id>` / `dizzygraph.<node_id>` with
`otel.span_name` + tenant attrs. Disable with `DIZZY_OTEL=0`.

### 5. Standalone callback

```python
from dizzygraph import Graph, AtomicNode, State
from dizzygraph.otel import OpenTelemetryCallback, setup_tracer_provider

setup_tracer_provider(
    project="rax-galileo-labs",
    log_stream="otel-demo",
    sampler="always_on",
    console=True,
)
g = Graph(id="demo")
g.add_node(AtomicNode(id="n1", fn=lambda s: {"done": True}))
g.set_entry("n1")
app = g.compile(callbacks=[OpenTelemetryCallback(thread_id="t1")])
app.invoke(State())
```

---

## Related

- Galileo OTel overview: https://docs.galileo.ai/sdk-api/third-party-integrations/opentelemetry-and-openinference  
- Distributed tracing sample: https://docs.galileo.ai/sdk-api/logging/distributed-tracing-otel  
- DizzyGraph use cases: [`GALILEO-DIZZYGRAPH-USE-CASES.md`](GALILEO-DIZZYGRAPH-USE-CASES.md)  
- Integration starters: [`examples/integrations/`](../examples/integrations/)
