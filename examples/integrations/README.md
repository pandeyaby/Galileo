# Galileo third-party integration starters

Thin, **real** examples that import official SDKs and log to Galileo when keys are present.
No silent mocks — missing packages/keys exit with a clear error (exit code 2).

| Starter | Framework | Install | Run |
|---------|-----------|---------|-----|
| `crewai_galileo.py` | CrewAI + `CrewAIEventListener` | `pip install crewai galileo openai` | `python examples/integrations/crewai_galileo.py` |
| `openai_agents_galileo.py` | OpenAI Agents + `GalileoTracingProcessor` | `pip install openai-agents galileo openai` | `python examples/integrations/openai_agents_galileo.py` |
| `openinference_langgraph_galileo.py` | LangGraph + Galileo callback / OpenInference | `pip install langgraph langchain-openai galileo openai` (+ optional `openinference-instrumentation-langchain 'galileo[otel]'`) | `python examples/integrations/openinference_langgraph_galileo.py` |
| `a2a_galileo.py` | A2A via `galileo-a2a` | `pip install galileo-a2a a2a-sdk 'galileo[otel]'` | `python examples/integrations/a2a_galileo.py` |
| `google_adk_galileo.py` | Google ADK via `galileo-adk` | `pip install galileo-adk google-adk` | `python examples/integrations/google_adk_galileo.py` |
| `ms_agent_framework_galileo.py` | Microsoft Agent Framework (OTel) | `pip install agent-framework 'galileo[otel]' opentelemetry-sdk openai` | `python examples/integrations/ms_agent_framework_galileo.py` |
| `strands_agents_galileo.py` | Strands Agents (OTel) | `pip install strands-agents 'galileo[otel]'` | `python examples/integrations/strands_agents_galileo.py` |
| `bedrock_galileo.py` | AWS Bedrock Converse | `pip install boto3 galileo` | `python examples/integrations/bedrock_galileo.py` |
| `gemini_enterprise_galileo.py` | Gemini API / Vertex | `pip install google-genai galileo` | `python examples/integrations/gemini_enterprise_galileo.py` |
| `vercel_ai_sdk/` | Vercel AI SDK (TypeScript) | `cd vercel_ai_sdk && npm install` | `npm run start` |

## Prerequisites

```bash
export OPENAI_API_KEY=...          # most Python LLM starters
export GALILEO_API_KEY=...         # required for OTel / official handlers
export GALILEO_PROJECT=rax-galileo-labs
export GALILEO_LOG_STREAM=<stream-name>
```

Framework-specific keys:

| Starter | Extra keys |
|---------|------------|
| Google ADK | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| Gemini / Enterprise | `GOOGLE_API_KEY` **or** `GOOGLE_APPLICATION_CREDENTIALS` + `VERTEX_PROJECT` |
| Bedrock | AWS creds (`AWS_ACCESS_KEY_ID` / `AWS_PROFILE`) + `BEDROCK_MODEL_ID` |
| Vercel AI | `OPENAI_API_KEY` + `GALILEO_API_KEY` |

## Notes

- These are **starters**, not full product integrations. Prefer official Galileo
  handlers/callbacks/`galileo-*` packages when your SDK version ships them.
- DizzyGraph fleet emits `otel.span_name=dizzygraph.<node>` on `node_start` /
  `node_end` and flushes completed runs with tenant → project/stream mapping
  (path overlay ↔ Console spans). Full OTel SDK exporter for DizzyGraph itself
  is still optional/pragmatic v1.
- Shared helpers: `_common.py` (`load_keys`, `setup_galileo_otel`, fail-loud requires).
