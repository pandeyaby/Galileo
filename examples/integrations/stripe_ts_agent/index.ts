/**
 * Stripe + OpenAI tool-calling agent → Galileo via OTLP (real SDKs, no mock).
 *
 * The agent exposes live Stripe tools (balance, customers.list, paymentIntents.list).
 * Missing credentials or packages exit non-zero — no fake success.
 *
 * Prerequisites:
 *   cd examples/integrations/stripe_ts_agent
 *   npm install
 *   export STRIPE_SECRET_KEY=sk_test_...   # or sk_live_...
 *   export OPENAI_API_KEY=...
 *   export GALILEO_API_KEY=...
 *   export GALILEO_PROJECT=rax-galileo-labs
 *   export GALILEO_LOG_STREAM=stripe-ts-agent
 *   npm run start
 *
 * Optional:
 *   STRIPE_AGENT_QUERY  — user prompt (default asks for balance + customer count)
 *   GALILEO_OTLP_ENDPOINT — self-hosted OTLP traces URL
 *   OPENAI_MODEL — default gpt-4o-mini
 */

import Stripe from "stripe";
import OpenAI from "openai";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { trace, SpanStatusCode } from "@opentelemetry/api";

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    console.error(`ERROR: ${name} required (no mock).`);
    process.exit(2);
  }
  return v;
}

const TOOLS: OpenAI.Chat.Completions.ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "stripe_balance",
      description: "Fetch the Stripe account balance (available + pending).",
      parameters: { type: "object", properties: {}, additionalProperties: false },
    },
  },
  {
    type: "function",
    function: {
      name: "stripe_list_customers",
      description: "List recent Stripe customers (id, email, name).",
      parameters: {
        type: "object",
        properties: {
          limit: { type: "number", description: "Max customers (1-20)", default: 5 },
        },
        additionalProperties: false,
      },
    },
  },
  {
    type: "function",
    function: {
      name: "stripe_list_payment_intents",
      description: "List recent PaymentIntents (id, amount, currency, status).",
      parameters: {
        type: "object",
        properties: {
          limit: { type: "number", description: "Max intents (1-20)", default: 5 },
        },
        additionalProperties: false,
      },
    },
  },
];

async function runTool(
  stripe: Stripe,
  name: string,
  argsJson: string,
): Promise<string> {
  const args = argsJson ? JSON.parse(argsJson) : {};
  const limit = Math.min(Math.max(Number(args.limit) || 5, 1), 20);

  if (name === "stripe_balance") {
    const bal = await stripe.balance.retrieve();
    return JSON.stringify({
      available: bal.available,
      pending: bal.pending,
      livemode: bal.livemode,
    });
  }
  if (name === "stripe_list_customers") {
    const customers = await stripe.customers.list({ limit });
    return JSON.stringify(
      customers.data.map((c) => ({ id: c.id, email: c.email, name: c.name })),
    );
  }
  if (name === "stripe_list_payment_intents") {
    const pis = await stripe.paymentIntents.list({ limit });
    return JSON.stringify(
      pis.data.map((p) => ({
        id: p.id,
        amount: p.amount,
        currency: p.currency,
        status: p.status,
      })),
    );
  }
  throw new Error(`Unknown tool: ${name}`);
}

async function main() {
  const stripeKey = requireEnv("STRIPE_SECRET_KEY");
  const openaiKey = requireEnv("OPENAI_API_KEY");
  const galileoKey = requireEnv("GALILEO_API_KEY");
  const project = process.env.GALILEO_PROJECT || "rax-galileo-labs";
  const logStream = process.env.GALILEO_LOG_STREAM || "stripe-ts-agent";
  const model = process.env.OPENAI_MODEL || "gpt-4o-mini";
  const query =
    process.env.STRIPE_AGENT_QUERY ||
    "Using Stripe tools, report the account balance summary and how many customers you can see (max 5). Be concise.";

  const endpoint =
    process.env.GALILEO_OTLP_ENDPOINT || "https://api.galileo.ai/otel/traces";

  const sdk = new NodeSDK({
    resource: resourceFromAttributes({
      "service.name": "dizzygraph-stripe-ts-agent",
      "galileo.project": project,
      "galileo.log_stream": logStream,
    }),
    traceExporter: new OTLPTraceExporter({
      url: endpoint,
      headers: {
        "Galileo-API-Key": galileoKey,
        project,
        logstream: logStream,
      },
    }),
  });
  sdk.start();

  const tracer = trace.getTracer("stripe-ts-agent", "0.1.0");
  const stripe = new Stripe(stripeKey);
  const openai = new OpenAI({ apiKey: openaiKey });

  const root = tracer.startSpan("stripe_ts_agent.run", {
    attributes: {
      "otel.span_name": "stripe_ts_agent.run",
      "galileo.project": project,
      "openinference.span.kind": "AGENT",
    },
  });

  try {
    const messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
      {
        role: "system",
        content:
          "You are a Stripe operations agent. Always call tools for live account data. Never invent IDs or balances.",
      },
      { role: "user", content: query },
    ];

    for (let turn = 0; turn < 6; turn++) {
      const llmSpan = tracer.startSpan("stripe_ts_agent.llm", {
        attributes: {
          "otel.span_name": "stripe_ts_agent.llm",
          "openinference.span.kind": "LLM",
          model,
          turn,
        },
      });
      let completion: OpenAI.Chat.Completions.ChatCompletion;
      try {
        completion = await openai.chat.completions.create({
          model,
          messages,
          tools: TOOLS,
          tool_choice: "auto",
          temperature: 0,
        });
        llmSpan.setStatus({ code: SpanStatusCode.OK });
      } catch (err) {
        llmSpan.setStatus({
          code: SpanStatusCode.ERROR,
          message: err instanceof Error ? err.message : String(err),
        });
        throw err;
      } finally {
        llmSpan.end();
      }

      const msg = completion.choices[0]?.message;
      if (!msg) {
        throw new Error("OpenAI returned empty message");
      }
      messages.push(msg);

      const toolCalls = msg.tool_calls;
      if (!toolCalls || toolCalls.length === 0) {
        const text = msg.content || "";
        console.log(`galileo otlp → ${project}/${logStream}`);
        console.log("── agent ──");
        console.log(text.slice(0, 1200));
        root.setStatus({ code: SpanStatusCode.OK });
        return;
      }

      for (const call of toolCalls) {
        const toolSpan = tracer.startSpan(`stripe_ts_agent.tool.${call.function.name}`, {
          attributes: {
            "otel.span_name": `stripe.tool.${call.function.name}`,
            "openinference.span.kind": "TOOL",
            "tool.name": call.function.name,
          },
        });
        try {
          const output = await runTool(
            stripe,
            call.function.name,
            call.function.arguments || "{}",
          );
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            content: output,
          });
          toolSpan.setStatus({ code: SpanStatusCode.OK });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          toolSpan.setStatus({ code: SpanStatusCode.ERROR, message });
          console.error(`ERROR: Stripe tool ${call.function.name} failed: ${message}`);
          process.exitCode = 2;
          throw err;
        } finally {
          toolSpan.end();
        }
      }
    }

    console.error("ERROR: agent exceeded max tool turns without a final answer.");
    process.exitCode = 2;
    root.setStatus({ code: SpanStatusCode.ERROR, message: "max_turns" });
  } catch (err) {
    console.error("ERROR:", err instanceof Error ? err.message : err);
    process.exitCode = 2;
    root.setStatus({
      code: SpanStatusCode.ERROR,
      message: err instanceof Error ? err.message : String(err),
    });
  } finally {
    root.end();
    await sdk.shutdown();
  }
}

main();
