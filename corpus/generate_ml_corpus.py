#!/usr/bin/env python3
"""
Generate a lab/org-scale ML-platform engineering knowledge corpus.

Template-driven (no LLM calls). Emits docs with structured sections that are
split into retrieval chunks. Cost stays controllable because embeddings are
cached by content hash under ``.vector_cache/``.

Usage:
  python corpus/generate_ml_corpus.py
  python corpus/generate_ml_corpus.py --target 900 --out knowledge_base.json

No secrets. Content is synthetic but realistic ML-platform engineering KB text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "knowledge_base.json"
STATS_OUT = pathlib.Path(__file__).resolve().parent / "STATS.md"

# Canonical seed docs — same IDs as the original Trinity KB so drills / Protect
# baselines that cite tr1–in3 keep working.
SEED_DOCS: list[dict[str, str]] = [
    {
        "id": "tr1",
        "category": "training",
        "content": (
            "A CUDA out-of-memory error during training is most often fixed by reducing "
            "per-GPU batch size, enabling gradient checkpointing (activation recomputation), "
            "or sharding optimizer state with ZeRO/FSDP. Use `torch.cuda.memory_summary()` "
            "to find the largest allocations; fragmentation can be mitigated with "
            "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True."
        ),
    },
    {
        "id": "tr2",
        "category": "training",
        "content": (
            "NCCL collective hangs in multi-node training usually trace to a network/interface "
            "mismatch. Set NCCL_DEBUG=INFO to surface the chosen transport, pin "
            "NCCL_SOCKET_IFNAME to the high-speed interface, and confirm all ranks call the "
            "same collective in the same order. A single rank that diverges (e.g. an uneven "
            "data shard) will deadlock the whole job."
        ),
    },
    {
        "id": "tr3",
        "category": "training",
        "content": (
            "Mixed-precision training with bf16 is preferred over fp16 on A100/H100 because "
            "bf16 has the same exponent range as fp32 and avoids loss-scaling instability. "
            "fp16 requires a dynamic loss scaler; NaNs after a few steps usually mean the "
            "scaler collapsed or a layer needs to stay in fp32 (e.g. softmax/layernorm)."
        ),
    },
    {
        "id": "tr4",
        "category": "training",
        "content": (
            "Checkpoint frequently to object storage and keep the last N plus periodic "
            "permanent snapshots. For large models, use sharded/distributed checkpointing "
            "(each rank writes its shard) to avoid a single-rank gather bottleneck. Always "
            "store the optimizer state and RNG state alongside weights so a resumed run is "
            "bitwise-reproducible."
        ),
    },
    {
        "id": "if1",
        "category": "inference",
        "content": (
            "vLLM achieves high throughput via PagedAttention, which stores the KV cache in "
            "non-contiguous GPU memory pages and avoids reserving max-sequence-length buffers "
            "per request. Continuous batching lets new requests join in-flight batches at "
            "token boundaries, keeping GPUs saturated under variable load."
        ),
    },
    {
        "id": "if2",
        "category": "inference",
        "content": (
            "KV-cache memory per request ≈ 2 (key+value) × num_layers × num_kv_heads × "
            "head_dim × seq_len × dtype_bytes. For long-context serving this dominates GPU "
            "memory. Reduce it with grouped-query attention (fewer KV heads), KV-cache "
            "quantization (fp8/int8), or paged/streaming eviction of old tokens."
        ),
    },
    {
        "id": "if3",
        "category": "inference",
        "content": (
            "Time-to-first-token (TTFT) is gated by the prefill (prompt) pass; inter-token "
            "latency is gated by the decode loop. To cut TTFT under load, cap max concurrent "
            "prefills and use chunked prefill so long prompts don't block the decode queue. "
            "Speculative decoding raises throughput when a small draft model agrees with the "
            "target most of the time."
        ),
    },
    {
        "id": "if4",
        "category": "inference",
        "content": (
            "Autoscale inference replicas on a queue/latency signal (e.g. requests-waiting or "
            "p95 TTFT), not raw GPU utilization, because a GPU can read 100% busy while "
            "latency is still healthy. Keep a warm pool to absorb the cold-start cost of "
            "loading large weights, which can take tens of seconds."
        ),
    },
    {
        "id": "in1",
        "category": "infra",
        "content": (
            "On Kubernetes, GPUs are exposed via the NVIDIA device plugin and requested as "
            "the extended resource nvidia.com/gpu. Pods without a GPU request can still be "
            "scheduled onto GPU nodes, so use taints/tolerations (e.g. nvidia.com/gpu:NoSchedule) "
            "plus node affinity to keep non-GPU workloads off expensive nodes."
        ),
    },
    {
        "id": "in2",
        "category": "infra",
        "content": (
            "For multi-GPU jobs, topology matters: GPUs on the same NVLink/NVSwitch domain "
            "communicate far faster than across PCIe or across nodes. Use the Kubernetes "
            "topology-aware scheduling / gang scheduling (e.g. Volcano, Kueue) so all-or-nothing "
            "jobs get all their GPUs co-located, and verify placement with `nvidia-smi topo -m`."
        ),
    },
    {
        "id": "in3",
        "category": "infra",
        "content": (
            "GPU node health: a falling SM clock or rising ECC error count is an early sign of "
            "a degrading device. Run DCGM (dcgm-exporter) to scrape per-GPU metrics into "
            "Prometheus, alert on XID errors in dmesg, and cordon+drain a node automatically "
            "when uncorrectable ECC errors appear, since a silent bad GPU corrupts training."
        ),
    },
]

# ── Template libraries (category → list of section templates) ─────────────────
# Each template has title + body with {placeholders}. Combinations expand coverage.

GPUS = ["A100-40GB", "A100-80GB", "H100-80GB", "H100-NVL", "L40S", "A10", "V100-32GB", "B200"]
FRAMEWORKS = ["PyTorch", "DeepSpeed", "FSDP", "Megatron-LM", "JAX", "Lightning", "Horovod"]
ORCHESTRATORS = ["Kubernetes", "Slurm", "Ray", "Kueue", "Volcano", "Flyte", "Kubeflow"]
SERVING = ["vLLM", "TensorRT-LLM", "TGI", "Triton", "TorchServe", "SGLang", "lmdeploy"]
CLUSTER = ["us-east-1a", "us-west-2b", "eu-central-1a", "training-rack-07", "inference-pool-3"]
SEVERITIES = ["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
MODELS = ["7B", "13B", "70B", "8x7B-MoE", "405B", "embedding-large", "reranker-v2"]

TRAINING_TEMPLATES = [
    (
        "distributed_strategy",
        "Choosing {framework} sharding for {model} on {gpu}",
        (
            "For {model}-class models on {gpu}, prefer {framework} with ZeRO stage {stage} "
            "when optimizer state dominates memory. Enable activation checkpointing every "
            "{ckpt_every} layers. Gradient accumulation steps={accum} keeps global batch "
            "stable when per-GPU microbatch must shrink. Watch all-reduce time vs compute: "
            "if communication exceeds 30% of step time, check topology and NCCL env."
        ),
    ),
    (
        "oom_playbook",
        "CUDA OOM runbook — {gpu} / {framework}",
        (
            "Symptom: torch.cuda.OutOfMemoryError mid-step on {gpu}. First reduce microbatch, "
            "then enable gradient checkpointing in {framework}, then shard optimizer state. "
            "Set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True to cut fragmentation. "
            "Capture `torch.cuda.memory_summary()` before kill; compare peak reserved vs allocated. "
            "If OOM only on rank 0, suspect an unsharded gather or logging buffer."
        ),
    ),
    (
        "nccl_debug",
        "NCCL hang triage — cluster {cluster}",
        (
            "On cluster {cluster}, set NCCL_DEBUG=INFO and NCCL_DEBUG_SUBSYS=INIT,GRAPH. "
            "Pin NCCL_SOCKET_IFNAME to the RDMA/Ethernet fabric used by training pods. "
            "Confirm identical collective order across ranks; uneven DataLoader lengths cause "
            "deadlocks. For {framework}, enable timeout (TORCH_NCCL_BLOCKING_WAIT / "
            "NCCL_ASYNC_ERROR_HANDLING) so hung jobs fail closed instead of burning GPU-hours."
        ),
    ),
    (
        "precision",
        "Mixed precision policy — {gpu} / {model}",
        (
            "On {gpu}, train {model} in bf16 when supported; keep LayerNorm/softmax in fp32 if "
            "you see NaNs. fp16 needs a dynamic loss scaler — if scale collapses to 1 within "
            "a few hundred steps, identify the unstable layer. Log grad-norm histograms per "
            "parameter group. Prefer {framework} native amp APIs over ad-hoc casts."
        ),
    ),
    (
        "checkpointing",
        "Checkpoint & resume — {framework} / {model}",
        (
            "Write sharded checkpoints every {ckpt_every} steps to object storage for {model}. "
            "Keep last {keep_n} rolling + weekly permanent. Persist optimizer, scheduler, RNG, "
            "and DataLoader epoch offsets. Validate restore on a single node before fleet-wide "
            "resume. Prefer {framework} distributed checkpoint formats to avoid rank-0 gather."
        ),
    ),
    (
        "data_pipeline",
        "Training data pipeline SLOs — {cluster}",
        (
            "On {cluster}, keep DataLoader workers ≥ 4× GPUs per node and pin memory. Alert when "
            "input pipeline stall time > 8% of step. Prefer local NVMe scratch for shuffle "
            "shards; avoid NFS for random seeks. Track tokens/sec and examples/sec separately "
            "from GPU util so starved jobs are not mistaken for healthy saturation."
        ),
    ),
]

INFERENCE_TEMPLATES = [
    (
        "serving_throughput",
        "{serving} throughput tuning for {model}",
        (
            "Serve {model} with {serving}. Raise max_num_seqs until KV-cache headroom drops "
            "below 15%. Prefer continuous batching; cap concurrent prefills to protect decode "
            "p95. Measure tokens/s and TTFT separately. For long prompts, enable chunked "
            "prefill. Warm replicas before attaching to the load balancer."
        ),
    ),
    (
        "kv_cache",
        "KV-cache budget for {model} on {gpu}",
        (
            "On {gpu}, KV-cache for {model} often dominates HBM. Estimate 2 × layers × kv_heads "
            "× head_dim × seq × dtype_bytes. Mitigations: GQA, fp8/int8 KV quant, shorter "
            "max_model_len, or prefix caching for shared system prompts. Evict idle sequences "
            "aggressively under memory pressure."
        ),
    ),
    (
        "latency_slos",
        "Inference latency SLOs — {serving}",
        (
            "Define TTFT p95 and inter-token latency p95 for {serving}. Autoscale on "
            "requests-waiting / queue depth, not GPU util alone. Keep a warm pool sized for "
            "cold-start of large weights. Shed load with 429 before queues explode; prefer "
            "degraded shorter-max-tokens over total outage."
        ),
    ),
    (
        "speculative",
        "Speculative decoding for {model}",
        (
            "Pair a small draft with target {model} when acceptance rate stays > 60%. Track "
            "tokens accepted per speculation round; if acceptance collapses after a prompt "
            "domain shift, disable speculation rather than burning draft FLOPs. Verify "
            "numerical parity on a gold set before enabling in production {serving}."
        ),
    ),
    (
        "routing",
        "Model routing / A-B for {model}",
        (
            "Route traffic to {model} canaries at 5% with identical Protect / eval gates. "
            "Compare groundedness, refusal rate, and p95 TTFT for 24h before full cutover. "
            "Keep previous revision hot for instant rollback. Tag Galileo traces with "
            "model_revision and serving_stack={serving}."
        ),
    ),
]

INFRA_TEMPLATES = [
    (
        "gpu_scheduling",
        "GPU scheduling on {orch} — {gpu}",
        (
            "Request nvidia.com/gpu for {gpu} workloads on {orch}. Apply NoSchedule taints so "
            "CPU-only pods stay off GPU nodes. Use topology-aware / gang scheduling so multi-GPU "
            "jobs land on the same NVLink domain. Quotas should separate training and inference "
            "pools to stop training from starving online serving."
        ),
    ),
    (
        "node_health",
        "GPU node health — {cluster} / {gpu}",
        (
            "On {cluster}, scrape DCGM for SM clocks, ECC, XID, power, and throttling on {gpu}. "
            "Auto-cordon on uncorrectable ECC or repeated XID 79/94. Drain before maintenance; "
            "verify `nvidia-smi` and persistence mode after reboot. Silent bad GPUs corrupt "
            "checkpoints — prefer fail-closed eviction."
        ),
    ),
    (
        "storage",
        "Checkpoint storage layout — {cluster}",
        (
            "Use object storage for durable checkpoints; local NVMe only for scratch. Enforce "
            "lifecycle: hot 7d, warm 30d, cold archive. Measure restore bandwidth per rank; "
            "stragglers dominate resume time. Encrypt at rest; no API keys or customer PII in "
            "artifact metadata. Document bucket prefixes per project and env."
        ),
    ),
    (
        "network",
        "Training fabric notes — {cluster}",
        (
            "Cluster {cluster}: prefer RDMA/RoCE for multi-node all-reduce. Validate MTU and "
            "PFC; packet loss shows up as NCCL timeouts not as clean TCP errors. Keep "
            "training and east-west service meshes on separate planes when possible. Record "
            "iperf and NCCL tests in the change ticket before capacity adds."
        ),
    ),
]

CUDA_TEMPLATES = [
    (
        "xid_errors",
        "XID triage on {gpu}",
        (
            "Recurring XID on {gpu} usually means driver/firmware, ECC, or thermal events. "
            "Collect dmesg + nvidia-bug-report.sh, note CUDA toolkit vs driver skew, and "
            "repro with a single-GPU burn-in (gemm + all-reduce). Do not keep a flapping "
            "device in the training pool. Track XID counts per node in Prometheus."
        ),
    ),
    (
        "driver_compat",
        "CUDA / driver compatibility — {gpu}",
        (
            "Pin a tested CUDA toolkit + driver pair for {gpu} images. Rebuild containers when "
            "bumping either; mixed nodes cause cryptic kernel load failures. Record "
            "nvidia-smi CUDA Version vs compile-time toolkit in the image SBOM. Canary one "
            "rack before fleet rollout."
        ),
    ),
    (
        "mps_mig",
        "MPS / MIG sharing for {gpu}",
        (
            "Use MIG on {gpu} for strong isolation of small inference jobs; use MPS only when "
            "workloads are cooperative and latency SLOs allow sharing. Never mix training "
            "all-reduce with MIG partitions on the same physical device. Document partition "
            "profiles in the capacity spreadsheet."
        ),
    ),
    (
        "alloc_conf",
        "Allocator & fragmentation — {gpu}",
        (
            "Fragmentation OOMs on {gpu} often clear with expandable_segments and fewer "
            "cached CUDA contexts. Avoid creating many short-lived streams per step. "
            "Profile with memory_summary and nsys. Prefer one process per GPU for training; "
            "multi-tenancy belongs at the orchestrator layer."
        ),
    ),
]

ORCH_TEMPLATES = [
    (
        "job_spec",
        "{orch} job template for {framework}",
        (
            "Submit {framework} jobs via {orch} with explicit GPU, CPU, memory, and RDMA "
            "requests. Set activeDeadlineSeconds / max runtime to kill stuck collectives. "
            "Propagate experiment_id and git_sha as labels for cost attribution. Prefer "
            "preemption-safe checkpointing before voluntary eviction."
        ),
    ),
    (
        "queueing",
        "Queue & fair-share — {orch} / {cluster}",
        (
            "On {cluster}, configure {orch} queues with fair-share between teams. Priority "
            "classes: interactive debug < batch train < production inference backfill. "
            "Surface queue wait time in the platform UI; silent multi-day waits look like "
            "user error but are capacity signals."
        ),
    ),
    (
        "ray_notes",
        "Ray / distributed actors on {orch}",
        (
            "When running Ray atop {orch}, size object store carefully and avoid putting "
            "giant tensors in the plasma store. Use placement groups for multi-GPU actors. "
            "Health-check head node separately; a healthy worker pool with a dead head "
            "looks 'up' in node metrics but serves nothing."
        ),
    ),
]

EVAL_TEMPLATES = [
    (
        "offline_eval",
        "Offline eval harness for {model}",
        (
            "Score {model} on a frozen gold set: grounding, refusal, toxicity, and task "
            "accuracy. Gate deploy on non-regression vs previous revision within ε. Log "
            "per-example failures to Galileo with doc_ids cited. Never train on the eval "
            "split; rotate challenge sets quarterly."
        ),
    ),
    (
        "online_eval",
        "Online eval sampling — {serving}",
        (
            "Sample 1–5% of {serving} traffic for asynchronous judges (adherence, "
            "completeness). Alert when rolling adherence drops > 0.1 vs 7-day baseline. "
            "Correlate with retrieval corpus version and model_revision. Protect remains "
            "the synchronous gate; online eval is the trend detector."
        ),
    ),
    (
        "regression",
        "Silent regression checklist — {model}",
        (
            "Before promoting {model}: compare Protect trigger rate, judge scores, p95 "
            "TTFT, and error taxonomy. A green fleet heartbeat does not imply answer "
            "quality. Re-run XL-style poisoned-retrieval and out-of-scope drills in staging."
        ),
    ),
]

INCIDENT_TEMPLATES = [
    (
        "incident_template",
        "{severity} incident pattern — {topic}",
        (
            "Severity {severity}. Symptom: {topic} on cluster {cluster}. Immediate: freeze "
            "deploys, page on-call, capture traces (Galileo stream + GPU metrics). Mitigate "
            "with rollback or traffic shed. Root-cause within 24h; write RB follow-up. "
            "Do not paste secrets into incident channels — redacted configs only."
        ),
    ),
    (
        "postmortem",
        "Postmortem outline — {topic}",
        (
            "Title: {topic}. Impact window, user-facing effect, detection path (monitor vs "
            "user report), timeline, contributing factors (corpus mismatch, bad GPU, "
            "misroute, Protect misconfig), action items with owners. Link Galileo example "
            "traces. Close only when action items have tickets."
        ),
    ),
]

RUNBOOK_TEMPLATES = [
    (
        "rb_process",
        "Runbook: agent process dead — {cluster}",
        (
            "Check systemd/k8s restart counts on {cluster}. Confirm OPENAI/GALILEO keys in "
            "the secret store (not in git). Tail application logs for import or OOM kills. "
            "Fleet green ≠ healthy answers — verify recent Galileo traces exist. Restart "
            "once; if crash-loop, roll back image."
        ),
    ),
    (
        "rb_corpus",
        "Runbook: retriever corpus mismatch",
        (
            "Compare knowledge_base.json hash and embedding cache key under .vector_cache/. "
            "If XL-2 poison left off-domain docs, run `python app.py --restore-corpus`. "
            "Rebuild embeddings after corpus edits. Validate TOP_K hits on a known seed "
            "query (CUDA OOM → tr1). Protect adherence crash often means wrong index."
        ),
    ),
    (
        "rb_protect",
        "Runbook: Protect / adherence blocks",
        (
            "When Protect blocks: inspect stage trinity-protect, adherence floor, and "
            "retrieved doc_ids. Distinguish true hallucination from corpus gaps. For "
            "out-of-scope queries, blocking is correct. For in-domain misses, expand "
            "corpus sections or fix routing — do not lower the floor casually."
        ),
    ),
    (
        "rb_slow_tool",
        "Runbook: slow tool / latency spike",
        (
            "If corpus-search or code tool latency spikes, check thread pool saturation, "
            "embedding API errors, and downstream GPU queue. Add timeouts and shed load. "
            "Correlate with DizzyGraph / LangGraph node durations and Galileo spans named "
            "dizzygraph.<node> or langchain."
        ),
    ),
]


def _fill(template: str, **kw: Any) -> str:
    return template.format(**kw)


def _chunk_doc(
    *,
    doc_id: str,
    category: str,
    title: str,
    body: str,
    section_key: str,
) -> list[dict[str, str]]:
    """Split a structured doc into retrieval chunks (overview + detail)."""
    overview = f"{title}. Category: {category}. Summary: {body[:220].rstrip()}…"
    detail = f"## {title}\n\n{body}\n\nRelated section key: {section_key}."
    return [
        {"id": f"{doc_id}-overview", "category": category, "content": overview},
        {"id": f"{doc_id}-detail", "category": category, "content": detail},
    ]


def _expand_category(
    category: str,
    templates: list[tuple[str, str, str]],
    combos: list[dict[str, Any]],
    prefix: str,
    start_idx: int,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    i = start_idx
    for combo in combos:
        for section_key, title_t, body_t in templates:
            title = _fill(title_t, **combo)
            body = _fill(body_t, **combo)
            doc_id = f"{prefix}{i:04d}"
            i += 1
            out.extend(
                _chunk_doc(
                    doc_id=doc_id,
                    category=category,
                    title=title,
                    body=body,
                    section_key=section_key,
                )
            )
    return out


def _combos(n: int) -> list[dict[str, Any]]:
    """Deterministic combo grid — enough variety without randomness."""
    combos: list[dict[str, Any]] = []
    idx = 0
    while len(combos) < n:
        combos.append(
            {
                "gpu": GPUS[idx % len(GPUS)],
                "framework": FRAMEWORKS[idx % len(FRAMEWORKS)],
                "orch": ORCHESTRATORS[idx % len(ORCHESTRATORS)],
                "serving": SERVING[idx % len(SERVING)],
                "cluster": CLUSTER[idx % len(CLUSTER)],
                "severity": SEVERITIES[idx % len(SEVERITIES)],
                "model": MODELS[idx % len(MODELS)],
                "stage": 1 + (idx % 3),
                "ckpt_every": 100 * (1 + (idx % 5)),
                "accum": 1 + (idx % 8),
                "keep_n": 3 + (idx % 5),
                "topic": [
                    "NCCL timeout storm",
                    "Protect adherence cliff",
                    "KV-cache exhaustion",
                    "scheduler deadlock",
                    "embedding API 429s",
                    "bad GPU ECC flood",
                    "corpus version skew",
                    "cold-start latency",
                ][idx % 8],
            }
        )
        idx += 1
    return combos


def generate(target_chunks: int = 1000) -> list[dict[str, str]]:
    """Build corpus: seed docs + generated chunks until target size."""
    docs: list[dict[str, str]] = [dict(d) for d in SEED_DOCS]
    seen_ids = {d["id"] for d in docs}

    # Rough: each combo×template yields 2 chunks; size combos to approach target.
    remaining = max(0, target_chunks - len(docs))
    # Weighted category budgets
    budgets = {
        "training": 0.22,
        "inference": 0.20,
        "infra": 0.14,
        "gpu_cuda": 0.12,
        "orchestration": 0.10,
        "evals": 0.08,
        "incidents": 0.07,
        "runbooks": 0.07,
    }
    plan = [
        ("training", TRAINING_TEMPLATES, "trgen", budgets["training"]),
        ("inference", INFERENCE_TEMPLATES, "ifgen", budgets["inference"]),
        ("infra", INFRA_TEMPLATES, "ingen", budgets["infra"]),
        ("gpu_cuda", CUDA_TEMPLATES, "cuggen", budgets["gpu_cuda"]),
        ("orchestration", ORCH_TEMPLATES, "orchgen", budgets["orchestration"]),
        ("evals", EVAL_TEMPLATES, "evgen", budgets["evals"]),
        ("incidents", INCIDENT_TEMPLATES, "incgen", budgets["incidents"]),
        ("runbooks", RUNBOOK_TEMPLATES, "rbgen", budgets["runbooks"]),
    ]

    generated: list[dict[str, str]] = []
    for category, templates, prefix, weight in plan:
        want = int(remaining * weight)
        # 2 chunks per template application
        apps = max(1, want // (2 * max(1, len(templates))))
        combos = _combos(apps)
        chunk_docs = _expand_category(category, templates, combos, prefix, 1)
        generated.extend(chunk_docs)

    # Top up if short
    filler_idx = 1
    while len(docs) + len(generated) < target_chunks:
        combo = _combos(1)[0]
        combo["topic"] = f"capacity note {filler_idx}"
        title = f"Platform capacity note {filler_idx} — {combo['cluster']}"
        body = (
            f"Capacity note {filler_idx} for {combo['cluster']}: track reserved vs free "
            f"{combo['gpu']} inventory, pending {combo['orch']} queue depth, and inference "
            f"headroom on {combo['serving']}. Rebalance training vs serving pools weekly. "
            f"No secrets in capacity sheets — use project IDs only."
        )
        generated.extend(
            _chunk_doc(
                doc_id=f"cap{filler_idx:04d}",
                category="infra",
                title=title,
                body=body,
                section_key="capacity",
            )
        )
        filler_idx += 1
        if filler_idx > 5000:
            break

    # Trim excess (prefer keeping seeds)
    room = max(0, target_chunks - len(docs))
    generated = generated[:room]

    for d in generated:
        if d["id"] in seen_ids:
            continue
        seen_ids.add(d["id"])
        docs.append(d)

    return docs


def corpus_stats(docs: list[dict[str, str]]) -> dict[str, Any]:
    cats = Counter(d["category"] for d in docs)
    lengths = [len(d["content"]) for d in docs]
    blob = json.dumps([d["content"] for d in docs], sort_keys=True).encode()
    return {
        "docs": len(docs),
        "chunks": len(docs),  # one retrieval unit per entry
        "categories": dict(sorted(cats.items())),
        "avg_chars": round(sum(lengths) / max(1, len(lengths))),
        "total_chars": sum(lengths),
        "content_sha16": hashlib.sha256(blob).hexdigest()[:16],
        "seed_ids": [d["id"] for d in SEED_DOCS],
    }


def write_stats(stats: dict[str, Any], path: pathlib.Path = STATS_OUT) -> None:
    lines = [
        "# ML-platform corpus stats",
        "",
        "Generated by `corpus/generate_ml_corpus.py`. Embeddings cache under "
        "`.vector_cache/` (keyed by content hash) so re-runs stay cheap.",
        "",
        f"- **Docs / chunks:** {stats['docs']}",
        f"- **Avg chars / chunk:** {stats['avg_chars']}",
        f"- **Total chars:** {stats['total_chars']}",
        f"- **Content sha16:** `{stats['content_sha16']}`",
        f"- **Seed IDs preserved:** {', '.join(stats['seed_ids'])}",
        "",
        "## Categories",
        "",
    ]
    for k, v in stats["categories"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend(
        [
            "",
            "## Loader paths",
            "",
            "- Live KB: `knowledge_base.json` (repo root)",
            "- Generator: `corpus/generate_ml_corpus.py`",
            "- Poison fixture (XL-2): still 5 off-domain docs via `app.py`",
            "- Restore: prefers `.bak`, else regenerates / copies generated KB",
            "",
            "## Cost note",
            "",
            "First embed of ~1k chunks with `text-embedding-3-small` is typically "
            "well under $0.05; thereafter disk cache hits. No secrets in corpus text.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", type=int, default=1000, help="Target chunk/doc count")
    p.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    p.add_argument("--stats", type=pathlib.Path, default=STATS_OUT)
    args = p.parse_args(argv)

    docs = generate(target_chunks=args.target)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    stats = corpus_stats(docs)
    write_stats(stats, args.stats)
    # Also stash a canonical copy next to the generator for restore.
    canon = pathlib.Path(__file__).resolve().parent / "ml_platform_kb.json"
    if args.out.resolve() != canon.resolve():
        canon.write_text(json.dumps(docs, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=2))
    print(f"Wrote {args.out} and {args.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
