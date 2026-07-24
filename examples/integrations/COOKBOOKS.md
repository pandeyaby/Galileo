# Cookbook recipes (integrations)

Thin, **real** examples. Missing packages/keys exit **2** — no silent mocks.

| Recipe | Path | Required env |
|--------|------|----------------|
| Stripe TS agent | [`stripe_ts_agent/`](stripe_ts_agent/) | `STRIPE_SECRET_KEY`, `OPENAI_API_KEY`, `GALILEO_API_KEY` |
| MongoDB Atlas RAG | [`mongodb_atlas_rag_galileo.py`](mongodb_atlas_rag_galileo.py) | `MONGODB_URI`, `OPENAI_API_KEY`, `GALILEO_API_KEY` |
| Elasticsearch + LangGraph RAG | [`elasticsearch_langgraph_rag_galileo.py`](elasticsearch_langgraph_rag_galileo.py) | `ELASTIC_URL` or `ELASTIC_CLOUD_ID`, `ELASTIC_API_KEY` (or user/password), `OPENAI_API_KEY`, `GALILEO_API_KEY` |
| Instruction Adherence | [`instruction_adherence_galileo.py`](instruction_adherence_galileo.py) | `OPENAI_API_KEY`, `GALILEO_API_KEY` |

Keys load from env, repo `.env`, lab `.env`, or OpenClaw (`trinity_dizzy.load_runtime_keys`) unless `DIZZY_SKIP_DOTENV=1`.

OTel deployment patterns: [`docs/OTEL-DEPLOYMENT.md`](../../docs/OTEL-DEPLOYMENT.md).
