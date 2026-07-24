# Integration LIVE smoke results

**Date:** 2026-07-24  
**Project:** `rax-galileo-labs`  
**Runtime:** repo `.venv` (Python 3.14.5) except CrewAI on `.venv313` (Python 3.13.8)  
**Keys loaded via:** `trinity_dizzy.load_runtime_keys` (lab `.env` + OpenClaw) — secrets not logged  
**AWS:** default profile (`~/.aws/credentials`) used for Bedrock  

Success means the starter completed (exit 0), printed its Galileo project/stream, and flushed or exported a trace/span batch to Galileo Console ingest (where the path uses GalileoLogger flush, OTel processor, or OTLP exporter).

## Matrix

| Framework | Starter | Result | Console project / stream | Trace flushed? | Notes |
|-----------|---------|--------|--------------------------|----------------|-------|
| CrewAI | `crewai_galileo.py` | **PASS** | `rax-galileo-labs` / `crewai-integration` | Yes (`CrewAIEventListener`, `flush_on_crew_completed`) | Failed on `.venv` Py3.14 (chromadb/pydantic.v1). Ran on `.venv313`. |
| OpenAI Agents | `openai_agents_galileo.py` | **PASS** | `rax-galileo-labs` / `openai-agents-integration` | Yes (`GalileoTracingProcessor`) | Noisy Pydantic serializer warnings; exit 0 + answer. |
| MS Agent Framework | `ms_agent_framework_galileo.py` | **PASS** | `rax-galileo-labs` / `ms-agent-framework` | Yes (Galileo OTel `GalileoSpanProcessor`) | Fixed starter: `OpenAIChatClient(model=...)` (was `model_id=`). |
| Strands Agents | `strands_agents_galileo.py` | **PASS** | `rax-galileo-labs` / `strands-agents` | Yes (global TracerProvider + Strands OTLP setup) | “Overriding of current TracerProvider is not allowed” warning (benign after prior smokes). |
| Google ADK | `google_adk_galileo.py` | **FAIL** | — | No | Missing `GOOGLE_API_KEY` / `GEMINI_API_KEY`. Packages installed (`galileo-adk`, `google-adk`). |
| Gemini / Vertex Enterprise | `gemini_enterprise_galileo.py` | **FAIL** | — | No | Missing `GOOGLE_API_KEY` **or** `GOOGLE_APPLICATION_CREDENTIALS` + `VERTEX_PROJECT`. |
| Vercel AI SDK | `vercel_ai_sdk/` | **PASS** | `rax-galileo-labs` / `vercel-ai-sdk` | Yes (OTLP → `api.galileo.ai/otel/traces`) | Fixed starter: `Resource` instead of `resourceFromAttributes` (OTel JS 1.30). |
| Bedrock | `bedrock_galileo.py` | **PASS** | `rax-galileo-labs` / `bedrock-integration` | Yes (`GalileoLogger.flush`) | Used AWS default profile + default `amazon.nova-lite-v1:0`. Starter now accepts boto3 default credential chain. |
| OpenInference / LangGraph | `openinference_langgraph_galileo.py` | **PASS** | `rax-galileo-labs` / `openinference-langgraph` | Yes (`GalileoCallback`, `flush_on_chain_end`) | Path used official Galileo LangChain callback (not OpenInference fallback). |
| A2A | `a2a_galileo.py` | **PASS** | `rax-galileo-labs` / `a2a-integration` | Yes (`galileo-a2a` + Galileo OTel; provider flush on shutdown) | Dual-agent handoff OK. Note: PyPI `galileo-a2a` declares `galileo<2`; ran with `galileo==2.5.1`. |
| DizzyGraph OTel | `trinity_dizzy.py` | **PASS** | `rax-galileo-labs` / `trinity-dizzy` | Yes (ingest `POST .../ingest/traces/...` 200) | Live Trinity path; Protect interrupt expected. Evidence: `dizzygraph_out/last_live_run.json`. |

## Starter fixes landed this run

1. **`ms_agent_framework_galileo.py`** — `OpenAIChatClient(model=...)` (agent-framework API).
2. **`vercel_ai_sdk/index.ts`** — use `new Resource({...})` for `@opentelemetry/resources@1.30`.
3. **`crewai_galileo.py`** — fail-loud on Python ≥3.14 with `.venv313` guidance.
4. **`bedrock_galileo.py`** — resolve AWS via boto3 default credential chain when env vars unset.
5. **`README.md` / `.gitignore`** — CrewAI Py&lt;3.14 note; ignore `.venv313/`.

## Blockers needing user action

| Blocker | Needed |
|---------|--------|
| Google ADK + Gemini/Vertex | Set `GOOGLE_API_KEY` (or `GEMINI_API_KEY`), **or** Vertex: `GOOGLE_APPLICATION_CREDENTIALS` + `VERTEX_PROJECT` (+ optional `VERTEX_LOCATION`). Re-run those two starters. |
| CrewAI on main `.venv` | Keep using `.venv313` until CrewAI/chromadb support Python 3.14, **or** switch project `.venv` to 3.13. |
| `galileo-a2a` vs `galileo` 2.x | Upstream pin `galileo<2`; works today but may break — watch packaging. |

## How to re-run

```bash
# keys via trinity_dizzy / OpenClaw (never echo)
source .venv/bin/activate   # or call scripts with .venv/bin/python
export GALILEO_PROJECT=rax-galileo-labs

.venv/bin/python examples/integrations/openai_agents_galileo.py
.venv/bin/python examples/integrations/openinference_langgraph_galileo.py
.venv/bin/python examples/integrations/ms_agent_framework_galileo.py
.venv/bin/python examples/integrations/strands_agents_galileo.py
.venv/bin/python examples/integrations/a2a_galileo.py
.venv/bin/python examples/integrations/bedrock_galileo.py
cd examples/integrations/vercel_ai_sdk && npm run start

# CrewAI (Python 3.13 sidecar)
.venv313/bin/python examples/integrations/crewai_galileo.py

# DizzyGraph OTel / Trinity
.venv/bin/python trinity_dizzy.py --write-evidence "smoke query"

# After Google keys:
.venv/bin/python examples/integrations/google_adk_galileo.py
.venv/bin/python examples/integrations/gemini_enterprise_galileo.py
```
