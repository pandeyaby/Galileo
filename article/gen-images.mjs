#!/usr/bin/env node
/**
 * gen-images.mjs — hands-off batch image generation from image-prompts.json
 *
 * You never prompt individual images. You run ONE command; it loops the whole file.
 *
 *   OpenAI (gpt-image-1, best quality):
 *     export OPENAI_API_KEY=sk-...
 *     node gen-images.mjs
 *
 *   Grok (xAI grok-2-image):
 *     export XAI_API_KEY=xai-...
 *     node gen-images.mjs --provider grok
 *
 * Useful flags:
 *   --provider openai|grok   (default: openai)
 *   --file <path>            prompts JSON (default: ./image-prompts.json)
 *   --out <dir>              output dir (default: ./images)
 *   --only hero,xl6-disguise comma list of ids to generate
 *   --all                    also generate "mix"/"diagram" entries (default skips them — keep the SVGs)
 *   --dry-run                print the composed prompts, call no API, spend nothing
 *
 * Requires Node 18+ (built-in fetch). No npm install needed.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

// ---- tiny arg parser ----
const args = process.argv.slice(2);
const flag = (name, def = undefined) => {
  const i = args.indexOf(`--${name}`);
  if (i === -1) return def;
  const v = args[i + 1];
  return v && !v.startsWith("--") ? v : true;
};
const provider = String(flag("provider", "openai")).toLowerCase();
const promptsFile = resolve(process.cwd(), String(flag("file", "image-prompts.json")));
const outDir = resolve(process.cwd(), String(flag("out", "images")));
const onlyIds = flag("only") ? String(flag("only")).split(",").map((s) => s.trim()) : null;
const includeMix = !!flag("all", false);
const dryRun = !!flag("dry-run", false);

// ---- load prompts ----
if (!existsSync(promptsFile)) {
  console.error(`Prompts file not found: ${promptsFile}`);
  process.exit(1);
}
const cfg = JSON.parse(readFileSync(promptsFile, "utf8"));
const styleWorld = cfg.style_world || "";
const constraints = cfg.global_constraints || "";
const defaults = cfg.defaults || { size: "1536x1024", quality: "high" };

let images = cfg.images || [];
if (onlyIds) images = images.filter((im) => onlyIds.includes(im.id));
else if (!includeMix) images = images.filter((im) => (im.mode || "editorial") === "editorial");

if (images.length === 0) {
  console.error("No images selected. (mix/diagram entries are skipped unless you pass --all or --only <id>.)");
  process.exit(1);
}

const designLang = cfg.design_language || "";
const compose = (im) => {
  const labelLine =
    Array.isArray(im.labels) && im.labels.length
      ? `ALLOWED LABELS (the only text in the image; big, plain, no other words): ${im.labels.map((l) => `"${l}"`).join(", ")}.`
      : "";
  return [styleWorld, designLang, constraints, labelLine, `SCENE: ${im.prompt}`]
    .filter(Boolean)
    .join("\n\n");
};

mkdirSync(outDir, { recursive: true });

// ---- provider adapters ----
async function genOpenAI(prompt, size) {
  const key = process.env.OPENAI_API_KEY;
  if (!key) throw new Error("OPENAI_API_KEY not set");
  const res = await fetch("https://api.openai.com/v1/images/generations", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: "gpt-image-1", prompt, size, n: 1, quality: defaults.quality || "high" }),
  });
  if (!res.ok) throw new Error(`OpenAI ${res.status}: ${(await res.text()).slice(0, 400)}`);
  const data = await res.json();
  const b64 = data?.data?.[0]?.b64_json;
  if (!b64) throw new Error("OpenAI returned no image data");
  return Buffer.from(b64, "base64");
}

async function genGrok(prompt) {
  const key = process.env.XAI_API_KEY;
  if (!key) throw new Error("XAI_API_KEY not set");
  // grok-2-image ignores size/quality; returns a URL we then fetch.
  const res = await fetch("https://api.x.ai/v1/images/generations", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: "grok-2-image", prompt, n: 1 }),
  });
  if (!res.ok) throw new Error(`xAI ${res.status}: ${(await res.text()).slice(0, 400)}`);
  const data = await res.json();
  const url = data?.data?.[0]?.url;
  if (!url) throw new Error("Grok returned no image url");
  const img = await fetch(url);
  if (!img.ok) throw new Error(`Grok image fetch ${img.status}`);
  return Buffer.from(await img.arrayBuffer());
}

// ---- run ----
console.log(`provider=${provider}  images=${images.length}  out=${outDir}${dryRun ? "  (DRY RUN)" : ""}\n`);
let ok = 0, fail = 0;
for (const im of images) {
  const prompt = compose(im);
  const size = im.size || defaults.size || "1536x1024";
  const dest = join(outDir, im.filename);
  if (dryRun) {
    console.log(`--- ${im.id} → ${im.filename} (${size}) ---\n${prompt}\n`);
    ok++;
    continue;
  }
  process.stdout.write(`[${im.id}] generating → ${im.filename} ... `);
  try {
    const buf = provider === "grok" ? await genGrok(prompt) : await genOpenAI(prompt, size);
    writeFileSync(dest, buf);
    console.log(`done (${(buf.length / 1024).toFixed(0)} KB)`);
    ok++;
  } catch (e) {
    console.log(`FAILED: ${e.message}`);
    fail++;
  }
}
console.log(`\n${ok} ok, ${fail} failed. Files in ${outDir}`);
if (fail) process.exit(1);
