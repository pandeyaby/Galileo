# Diagnosis: Why Trinity-Stack Drill Traces Never Landed in Galileo

**Date:** 2026-06-14  
**Diagnosed by:** RAX  
**Stream:** `rax-galileo-labs` → `trinity-stack`

---

## Root Cause

**`AttributeError: 'LlmMetric' object has no attribute 'aggregator_fn'`**

The `GalileoLogger.flush()` silently crashes every time it tries to send traces, because
`app.py` passes `LlmMetric` objects as `local_metrics` — but `LlmMetric` is NOT a
`LocalMetricConfig`. The SDK's `populate_local_metrics()` function (called during
`_flush_batch()`) accesses `metric.aggregator_fn`, which doesn't exist on `LlmMetric`.

The exception is caught by `flush()`'s outer `try/except` and swallowed as a warning.
No trace is ever sent. No error is printed to stdout (the drills only capture stdout).

### Why the "flush verification" trace DID land

The "test flush verification" trace from June 14 was likely sent via a bare
`GalileoLogger` (no `local_metrics` parameter), bypassing the crash. When
`local_metrics` is `None` or `[]`, the `if self.local_metrics:` check in
`_flush_batch()` skips metric computation entirely, and the trace is sent
successfully.

### Code path (the crash)

```
run_query()
  → graph.invoke(..., callbacks=[GalileoCallback])
    → callback on_chain_end → handler.end_node → handler.commit()
      → logger.start_trace(...)    ✅ creates trace (self.traces = [1 trace])
      → handler.log_node_tree(...)  ✅ adds spans
      → logger.conclude(...)       ✅ finalizes trace
      → logger.flush()             ❌ CRASHES HERE
        → async_run(_flush_batch())
          → populate_local_metrics(trace, self.local_metrics)
            → local_metric.aggregator_fn   💥 AttributeError
        → exception caught, warning logged (not visible in stdout)
        → returns []
        → self.traces NEVER cleared (still [1 trace])
  → manual logger.flush()
    → same crash → returns [] → traces still stuck
```

### Evidence

1. **Bare logger (no local_metrics):** Trace ingested, `POST /ingest/traces/... → 200 OK`
2. **With local_metrics=get_metrics():** `flush()` returns `[]`, traces stay at 1, no POST
3. **Direct asyncio.run(_flush_batch()):** `AttributeError: 'LlmMetric' object has no attribute 'aggregator_fn'`
4. **SDK version:** galileo==2.3.0, galileo-core==4.3.0

---

## The Fix

### What to change

In `app.py` (and all 6 drill scripts that copy the pattern), remove `local_metrics=metrics`
from every `GalileoLogger(...)` constructor call.

**Before (broken):**
```python
metrics = get_metrics()  # returns [LlmMetric, LlmMetric, LlmMetric]
logger = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM, local_metrics=metrics)
```

**After (fixed):**
```python
logger = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
```

### Files to edit

| File | Line(s) | Change |
|------|---------|--------|
| `app.py` → `run_query()` | ~L202 | Remove `local_metrics=metrics` |
| `drills/xl2_poisoned_retriever.py` | (not applicable — uses `app.run_query`) | — |
| `drills/xl3_langgraph_misroute.py` → `run_q()` | ~L75 | Remove `local_metrics=metrics` |
| `drills/xl4_eval_to_protect.py` → `run_q()` | ~L103 | Remove `local_metrics=metrics` |
| `drills/xl5_slow_tool.py` → `run_q()` | ~L68 | Remove `local_metrics=metrics` |
| `drills/xl6_model_regression.py` → `run_q()` | ~L72 | Remove `local_metrics=metrics` |

### What about the quality metrics?

Removing `local_metrics` means the traces will land WITHOUT client-side metric scores.
The metrics (context_adherence, completeness, cites_kb_source, routing_accuracy) need
to be configured as **server-side scorers** on the trinity-stack log stream in the
Galileo Console instead:

1. Go to Console → rax-galileo-labs → trinity-stack → Settings → Scorers
2. Register the three LlmMetric scorers (same prompts as `get_metrics()`)
3. The Galileo backend will compute scores on every ingested trace using Luna-2

This is actually the CORRECT architecture — having the platform score traces is cheaper,
faster, and more consistent than client-side LLM-as-judge calls during flush.

### Also fix: deprecated import

```python
# Before (deprecation warning):
from galileo.__future__.metric import LlmMetric

# After:
from galileo.metric import LlmMetric
```

### Also fix: broken venv symlink

The `.venv/bin/python3` symlink pointed to `python3.11` (a broken uv-installed binary)
instead of `python3.14` (the Python the venv was built with). Fixed during diagnosis:
```bash
cd .venv/bin && ln -sf python3.14 python3 && ln -sf python3.14 python
```

---

## Smoke Test Result

**Test:** Single query via `app.py` with the fix applied (no `local_metrics`):

```
Query: "How do I debug a CUDA out-of-memory error during training?"
Result: Trace ingested successfully
  - logger.traces after flush: 0 (cleared — trace was sent)
  - flush result: 1 trace returned
  - Protect: not_triggered
  - Latency: 3486ms
```

**Compared to broken behavior (with `local_metrics=metrics`):**
```
  - logger.traces after flush: 1 (NOT cleared — trace stuck, never sent)
  - flush result: [] (empty — nothing sent)
  - No POST to /ingest/traces/ endpoint
```

---

## Summary

| Question | Answer |
|----------|--------|
| Why didn't traces land? | `LlmMetric` lacks `aggregator_fn`, crashes `flush()` silently |
| Why was the error invisible? | `flush()` swallows all exceptions, logs warning to Python logger (not stdout) |
| Why did "flush verification" work? | It didn't use `local_metrics` |
| Fix complexity | One-line change per file (remove `local_metrics=metrics`) |
| Risk | Zero — removing `local_metrics` only removes client-side scoring; traces + spans are unaffected |
| Metrics plan | Register as server-side scorers on the stream (step 3 of Abhi's task list) |

---

**Root-cause theory (one line):** `LlmMetric` objects passed as `local_metrics` crash
`GalileoLogger.flush()` with a swallowed `AttributeError` on `aggregator_fn`, preventing
all trace ingestion.
