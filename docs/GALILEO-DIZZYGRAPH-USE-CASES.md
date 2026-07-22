# Galileo × DizzyGraph — unique use cases

These are the fits that are *native* to DizzyGraph layers (not just “log to Galileo”).
Status below matches the control-plane `/api/trinity/use-cases` list (v0.4+).

| Use case | DizzyGraph layer | Galileo surface | Fleet signal | Status |
|----------|------------------|-----------------|--------------|--------|
| **Protect as LoopNode checker** | `LoopNode` maker/checker | Protect invoke / stage | `loop_non_converge` alert (+ Protect score/path) | **shipped** |
| **XL drill fan-out** | `Supervisor` / XL graphs | One project, many traces | Parent aggregates child Protect status | **shipped** |
| **Silent regression meta-loop** | `MetaLoopExecutor` | Scorer trends across meta iters | `meta_protect_score` / `meta_quality_delta` | **shipped** |
| **HITL after Protect trigger** | `interrupt()` / resume | Protect `triggered` → human gate | `hitl_interrupt` + aging alert | **shipped** |
| **Tenant ↔ project/stream** | Auth `tenant_id` | Isolated Console projects | Per-tenant fleet + flush target | **shipped** |
| **Path ↔ span correlation** | `path_steps` + events | Trace span names `dizzygraph.<node>` | Stuck node = hot span | **shipped** (pragmatic v1; full OTel exporter pending) |

**Not in this table (still pending):** Google ADK starter; full OpenTelemetry exporter. Priority-2 starters (CrewAI, OpenAI Agents, OpenInference-shaped LangGraph) are under [`examples/integrations/`](../examples/integrations/).

## API

```bash
# Register live Trinity (400 if keys missing — no mock)
curl -X POST http://127.0.0.1:8800/api/trinity/register

# Spawn live Trinity agents
curl -X POST http://127.0.0.1:8800/api/trinity/spawn \
  -H 'content-type: application/json' \
  -d '{"n":4,"live":true}'

# XL-1..XL-6 fan-out under supervisor (aggregates Protect on parent)
curl -X POST http://127.0.0.1:8800/api/trinity/xl-fanout \
  -H 'content-type: application/json' \
  -d '{"wait":false}'

# Silent regression MetaLoop
curl -X POST http://127.0.0.1:8800/api/trinity/meta-regression \
  -H 'content-type: application/json' \
  -d '{"meta_iters":3}'

# Tenant → Galileo project/stream
curl http://127.0.0.1:8800/api/tenants/galileo
curl -H 'X-Tenant-Id: acme' http://127.0.0.1:8800/api/tenants/galileo

curl http://127.0.0.1:8800/api/trinity/use-cases
```

Requires `OPENAI_API_KEY` (and ideally `GALILEO_API_KEY`) via env, lab `.env`, or OpenClaw.

Tenant mapping (optional):

```bash
export DIZZY_TENANT_GALILEO='{"acme":{"project":"acme-labs","log_stream":"fleet"},"default":{"project":"rax-galileo-labs","log_stream":"trinity-dizzy"}}'
```

## Boot

```bash
python -m dizzygraph.control --trinity 4 --port 8800
```
