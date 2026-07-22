# DizzyGraph Control Plane — Goals

Production multi-agent **ops layer** on top of DizzyGraph.

## Goals (definition of done)

| ID | Goal | Acceptance |
|----|------|------------|
| G1 | Many concurrent agents | Start N `thread_id`s; fleet list shows all |
| G2 | Durable checkpoints | SQLite **or** Postgres; survives restart |
| G3 | Event bus | Persisted events + SSE; optional Redis pub/sub |
| G4 | Topology + live path overlay | Mermaid + **full path** highlight + current node |
| G5 | State snapshot | Latest checkpoint JSON per thread |
| G6 | Loop non-convergence alerts | Alert when LoopNode exhausts without converging |
| G7 | Stuck / error / retry / aging HITL alerts | Yes |
| G8 | Single-command ops | `python -m dizzygraph.control` |
| G9 | Cross-agent supervisor | `POST /api/supervisor/fanout` aggregates children |
| G10 | Metrics | lag p50/p95, fail rate, loop iters, stuck count + sparkline |
| G11 | Auth + multi-tenant | API keys (`DIZZY_API_KEYS`) + `X-Tenant-Id`; data isolated by tenant |

## Backends

| Mode | Env |
|------|-----|
| SQLite (default) | `--db dizzygraph_out/control.db` |
| Postgres | `DIZZY_DATABASE_URL=postgres://…` or `--database-url` |
| Redis bus | `DIZZY_REDIS_URL=redis://…` |

## Run

```bash
pip install -e ".[control,dev]"
# optional: pip install -e ".[control-full]"
python -m dizzygraph.control --demo 8 --fanout 4
# open http://127.0.0.1:8787
```
