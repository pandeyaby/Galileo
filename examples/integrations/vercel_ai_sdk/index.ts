/**
 * Vercel AI SDK → Galileo via OpenTelemetry (real SDK, no mock).
 *
 * Prerequisites:
 *   cd examples/integrations/vercel_ai_sdk
 *   npm install
 *   export OPENAI_API_KEY=... GALILEO_API_KEY=...
 *   export GALILEO_PROJECT=rax-galileo-labs GALILEO_LOG_STREAM=vercel-ai-sdk
 *   npm run start
 *
 * Fail-loud: missing keys or packages exit non-zero. No fake success.
 */

import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";

function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    console.error(`ERROR: ${name} required (no mock).`);
    process.exit(2);
  }
  return v;
}

async function main() {
  const openaiKey = requireEnv("OPENAI_API_KEY");
  const galileoKey = requireEnv("GALILEO_API_KEY");
  const project = process.env.GALILEO_PROJECT || "rax-galileo-labs";
  const logStream = process.env.GALILEO_LOG_STREAM || "vercel-ai-sdk";
  process.env.OPENAI_API_KEY = openaiKey;

  // Galileo OTLP ingest (Cloud default). Self-hosted: set GALILEO_OTLP_ENDPOINT.
  const endpoint =
    process.env.GALILEO_OTLP_ENDPOINT || "https://api.galileo.ai/otel/traces";

  const sdk = new NodeSDK({
    resource: resourceFromAttributes({
      "service.name": "dizzygraph-vercel-ai-sdk",
      "galileo.project": project,
      "galileo.log_stream": logStream,
    }),
    traceExporter: new OTLPTraceExporter({
      url: endpoint,
      headers: {
        "Galileo-API-Key": galileoKey,
        "project": project,
        "logstream": logStream,
      },
    }),
  });
  sdk.start();

  const query = "In one sentence: what is gradient checkpointing?";
  try {
    const { text } = await generateText({
      model: openai(process.env.OPENAI_MODEL || "gpt-4o-mini"),
      prompt: query,
      experimental_telemetry: {
        isEnabled: true,
        functionId: "vercel-ai-dizzygraph-starter",
        metadata: {
          "otel.span_name": "vercel_ai.generateText",
          framework: "vercel-ai-sdk",
        },
      },
    });
    console.log(`galileo otlp → ${project}/${logStream}`);
    console.log("── answer ──");
    console.log((text || "").slice(0, 800));
  } catch (err) {
    console.error("ERROR:", err instanceof Error ? err.message : err);
    process.exitCode = 2;
  } finally {
    await sdk.shutdown();
  }
}

main();
