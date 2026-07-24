# Multi-worker DizzyGraph control plane

**Audience:** operators running more than one `dizzygraph.control` worker.

## What works today

| Layer | Single process | Multi-worker |
|-------|----------------|--------------|
| Graph executor + HITL + fail policies | Yes | Yes (per worker) |
| SQLite store (`ControlStore`) | Yes (WAL, thread-safe) | **No** as shared DB across hosts |
| Postgres store (`DIZZY_DATABASE_URL`) | Yes | Yes for durable state |
| Redis bus (`DIZZY_REDIS_URL`) | Optional | Cross-process **event fan-out** |
| Admission / backpressure (`DIZZY_MAX_INFLIGHT`) | Yes | Per-worker ceiling |
| Health | `/api/health`, `/api/livez`, `/api/readyz` | Probe each worker |

## Recommended topology

```
          ┌──────────── load balancer ────────────┐
          │                                       │
   worker-a (uvicorn)                      worker-b (uvicorn)
          │                                       │
          └────────── Postgres (shared) ──────────┘
          └────────── Redis pub/sub (optional) ───┘
```

```bash
pip install -e ".[control-full]"
export DIZZY_DATABASE_URL=postgresql://dizzy:dizzy@localhost:5432/dizzy
export DIZZY_REDIS_URL=redis://localhost:6379/0
export DIZZY_MAX_INFLIGHT=64
export DIZZY_AUTH_REQUIRED=1
export DIZZY_API_KEYS=prod:replace-me
python -m dizzygraph.control --host 0.0.0.0 --port 8787
```

## Checkpoints

- Control plane uses `SqliteCheckpointer` / `StoreCheckpointer` over the shared store.
- `list(thread_id)` returns newest-first history; `clear(thread_id)` deletes rows.
- Resume requires the **checkpoint for that `thread_id`** in the shared store — any worker can resume.

## Still not LangGraph / still incomplete

Honest gaps for production fleets:

1. **No run lease / ownership** — two workers can both try to execute the same `thread_id` if you enqueue duplicates. Avoid duplicate starts; sticky LB helps but is not a lock.
2. **Postgres uses a single connection + lock** — fine for lab/low QPS; for high QPS add a pool (not yet shipped).
3. **Admission is per-worker** — global capacity needs an external limiter or shared counter.
4. **Auth defaults off** — set `DIZZY_AUTH_REQUIRED=1` outside demos.
5. **Trinity live paths never mock** — `--trinity` / `/api/trinity/*` fail loud without keys.

Prefer LangGraph + LangChain when you need Pregel semantics and the full ecosystem. DizzyGraph is for **named loop layers**, readable Mermaid, and a small fleet control plane you can finish reading in an afternoon — now with fail policies, admission control, and real health/ready probes.
