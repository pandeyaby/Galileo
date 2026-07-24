# Stripe TypeScript agent → Galileo

Real Stripe + OpenAI tool-calling agent with OTLP export to Galileo.
**No mocks** — missing env vars exit with code 2.

## Setup

```bash
cd examples/integrations/stripe_ts_agent
npm install
export STRIPE_SECRET_KEY=sk_test_...
export OPENAI_API_KEY=...
export GALILEO_API_KEY=...
export GALILEO_PROJECT=rax-galileo-labs
export GALILEO_LOG_STREAM=stripe-ts-agent
npm run start
```

## Env

| Variable | Required | Notes |
|----------|----------|--------|
| `STRIPE_SECRET_KEY` | yes | Test or live secret key |
| `OPENAI_API_KEY` | yes | Tool-calling chat model |
| `GALILEO_API_KEY` | yes | OTLP auth |
| `GALILEO_PROJECT` | no | default `rax-galileo-labs` |
| `GALILEO_LOG_STREAM` | no | default `stripe-ts-agent` |
| `GALILEO_OTLP_ENDPOINT` | no | Cloud default `https://api.galileo.ai/otel/traces` |
| `OPENAI_MODEL` | no | default `gpt-4o-mini` |
| `STRIPE_AGENT_QUERY` | no | Override user prompt |

## Tools (live Stripe)

- `stripe_balance` — `balance.retrieve`
- `stripe_list_customers` — `customers.list`
- `stripe_list_payment_intents` — `paymentIntents.list`

Blocked without your Stripe + OpenAI + Galileo credentials.
