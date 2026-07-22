# Galileo third-party integration starters

Thin, **real** examples that log to Galileo when keys are present.
No silent mocks — missing packages/keys exit with a clear error.

| Starter | Framework | Run |
|---------|-----------|-----|
| `crewai_galileo.py` | CrewAI | `python examples/integrations/crewai_galileo.py` |
| `openai_agents_galileo.py` | OpenAI Agents SDK | `python examples/integrations/openai_agents_galileo.py` |
| `openinference_langgraph_galileo.py` | LangGraph + OpenInference-shaped spans | `python examples/integrations/openinference_langgraph_galileo.py` |

## Prerequisites

```bash
export OPENAI_API_KEY=...
export GALILEO_API_KEY=...   # optional but recommended for Console traces
# optional overrides:
export GALILEO_PROJECT=rax-galileo-labs
export GALILEO_LOG_STREAM=crewai-integration
```

Install only what you need:

```bash
pip install crewai galileo openai                    # CrewAI starter
pip install openai-agents galileo openai             # OpenAI Agents starter
pip install langgraph langchain-openai galileo openai  # OpenInference/LangGraph starter
```

## Notes

- These are **starters**, not full product integrations. Prefer official Galileo
  handlers/callbacks when your SDK version ships them.
- DizzyGraph fleet already emits `otel.span_name=dizzygraph.<node>` on
  `node_start` / `node_end` events so path overlay ↔ span correlation works
  without a separate OTel collector (pragmatic v1).
- Google ADK starter not yet shipped — next gap after these three.
