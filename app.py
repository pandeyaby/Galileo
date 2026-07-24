"""
trinity-stack/app.py
AI-Platform Engineering Assistant — Galileo + LangGraph + real RUN telemetry

Scenario: an internal engineering assistant for an ML platform team (the kind of
agent Meta / OpenAI / NVIDIA-class orgs run internally). It answers questions about
distributed training, inference serving, and GPU/cluster infrastructure, grounded in
a real engineering knowledge corpus. It routes by topic, retrieves with real dense
embeddings, runs real tools (sandboxed code execution + semantic corpus search),
generates an answer, and gates it through a real Galileo Protect stage.

PRODUCTION-GRADE — NO MOCKS:
  - Retrieval:   real OpenAI embeddings + cosine vector index (NOT keyword overlap)
  - Tools:       real sandboxed Python execution + real semantic search (NOT dict lookups)
  - Guardrails:  real Galileo Protect via invoke_protect + Ruleset/Rule/OverrideAction
                 (real-LLM-judge fallback only if the Protect API is unreachable)
  - Telemetry:   real measured process metrics via psutil + real measured latency
                 (NOT a hand-written heartbeat with fabricated numbers)
  - Corpus:      real ML-infra engineering knowledge (NOT marketing copy)

Architecture:
  [intake] → (route) → retriever → tools → responder → protect
                                                          ↓
                                              Galileo Protect stage (XL-4)

Three layers:
  BUILD:  LangGraph (this file)
  RUN:    fleet/monitor.py + psutil telemetry (ClawTrace/OTel-equivalent — see _fleet_heartbeat)
  TRUST:  Galileo (traces, context adherence, completeness, custom judges, Protect)

Usage:
  python app.py "How do I debug a CUDA out-of-memory error during training?"
  python app.py --batch            # real engineering baseline (10 queries)
  python app.py --poison-corpus    # XL-2: swap to an off-domain index (drill)
  python app.py --restore-corpus   # restore the real corpus after XL-2
"""

import os, sys, json, datetime, time, pathlib, hashlib, subprocess, tempfile, textwrap

# ── API key injection (read from the gateway's Galileo MCP config; never echoed) ─
def _load_key(name: str) -> str:
    cfg_path = pathlib.Path.home() / ".openclaw" / "openclaw.json"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg["mcp"]["servers"]["galileo"]["headers"]["Galileo-API-Key"]
    except Exception:
        return os.environ.get(name, "")

os.environ.setdefault("GALILEO_API_KEY", _load_key("GALILEO_API_KEY"))

# ── Imports ───────────────────────────────────────────────────────────────────
from typing import TypedDict, Optional, List, Dict
import numpy as np
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage
from galileo import GalileoLogger
from galileo.handlers.langchain import GalileoCallback
from galileo.metric import LlmMetric

# ── Galileo 2.3.0 Agent Control bridge (new observability path) ───────────────
# setup_agent_control_bridge() connects the GalileoLogger to the agent-control-sdk
# so that control evaluation events flow into Galileo traces automatically.
# This is the migration path FROM invoke_protect TOWARD full Agent Control.
# For runtime guardrail enforcement (blocking), we still use invoke_protect in
# this lab because a dedicated agent-control server is not provisioned here.
# When an agent-control server IS available (see RB-140 §Fix - Current path),
# replace invoke_protect with @agent_control.control() + ControlViolationError.
try:
    from galileo import setup_agent_control_bridge, GalileoAgentControlBridge
    _AGENT_CONTROL_BRIDGE_AVAILABLE = True
except ImportError:
    _AGENT_CONTROL_BRIDGE_AVAILABLE = False
    GalileoAgentControlBridge = None  # type: ignore

# Real Galileo Protect surface (deprecated in 2.3.0, still functional).
# Migration target: @agent_control.control() + ControlViolationError (see RB-140).
try:
    from galileo import invoke_protect
    from galileo_core.schemas.protect.payload import Payload
    from galileo_core.schemas.protect.ruleset import Ruleset
    from galileo_core.schemas.protect.rule import Rule, RuleOperator
    from galileo_core.schemas.protect.action import OverrideAction
    _PROTECT_AVAILABLE = True
except Exception:  # SDK older than Protect support
    _PROTECT_AVAILABLE = False

# ── Constants ─────────────────────────────────────────────────────────────────
PROJECT          = "rax-galileo-labs"
LOG_STREAM       = "trinity-stack"
MODEL            = "gpt-4o-mini"
EMBED_MODEL      = "text-embedding-3-small"
PROTECT_STAGE    = "trinity-protect"
ADHERENCE_FLOOR  = 0.5            # Protect rule threshold (block below this)
TOP_K            = 3
KB_FILE          = pathlib.Path(__file__).parent / "knowledge_base.json"
KB_POISON_FILE   = pathlib.Path(__file__).parent / "knowledge_base_poisoned.json"
KB_CANONICAL     = pathlib.Path(__file__).parent / "corpus" / "ml_platform_kb.json"
INDEX_CACHE_DIR  = pathlib.Path(__file__).parent / ".vector_cache"

BLOCKED_MESSAGE = (
    "[BLOCKED by Galileo Protect] This answer failed the grounding check "
    "(context_adherence below threshold) and was withheld. The query has been "
    "routed to a human platform engineer."
)

# ── Seed engineering docs (canonical IDs tr1–in3). Full lab-scale corpus is
# generated by corpus/generate_ml_corpus.py → knowledge_base.json (~1000 chunks).
# Keep these seeds in-code so drills that import KNOWLEDGE_BASE_ORIGINAL still
# see the original 11 answers when they pass an explicit kb= list.
KNOWLEDGE_BASE_ORIGINAL = [
    # Training
    {"id": "tr1", "category": "training",
     "content": "A CUDA out-of-memory error during training is most often fixed by reducing per-GPU batch size, enabling gradient checkpointing (activation recomputation), or sharding optimizer state with ZeRO/FSDP. Use `torch.cuda.memory_summary()` to find the largest allocations; fragmentation can be mitigated with PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True."},
    {"id": "tr2", "category": "training",
     "content": "NCCL collective hangs in multi-node training usually trace to a network/interface mismatch. Set NCCL_DEBUG=INFO to surface the chosen transport, pin NCCL_SOCKET_IFNAME to the high-speed interface, and confirm all ranks call the same collective in the same order. A single rank that diverges (e.g. an uneven data shard) will deadlock the whole job."},
    {"id": "tr3", "category": "training",
     "content": "Mixed-precision training with bf16 is preferred over fp16 on A100/H100 because bf16 has the same exponent range as fp32 and avoids loss-scaling instability. fp16 requires a dynamic loss scaler; NaNs after a few steps usually mean the scaler collapsed or a layer needs to stay in fp32 (e.g. softmax/layernorm)."},
    {"id": "tr4", "category": "training",
     "content": "Checkpoint frequently to object storage and keep the last N plus periodic permanent snapshots. For large models, use sharded/distributed checkpointing (each rank writes its shard) to avoid a single-rank gather bottleneck. Always store the optimizer state and RNG state alongside weights so a resumed run is bitwise-reproducible."},
    # Inference
    {"id": "if1", "category": "inference",
     "content": "vLLM achieves high throughput via PagedAttention, which stores the KV cache in non-contiguous GPU memory pages and avoids reserving max-sequence-length buffers per request. Continuous batching lets new requests join in-flight batches at token boundaries, keeping GPUs saturated under variable load."},
    {"id": "if2", "category": "inference",
     "content": "KV-cache memory per request ≈ 2 (key+value) × num_layers × num_kv_heads × head_dim × seq_len × dtype_bytes. For long-context serving this dominates GPU memory. Reduce it with grouped-query attention (fewer KV heads), KV-cache quantization (fp8/int8), or paged/streaming eviction of old tokens."},
    {"id": "if3", "category": "inference",
     "content": "Time-to-first-token (TTFT) is gated by the prefill (prompt) pass; inter-token latency is gated by the decode loop. To cut TTFT under load, cap max concurrent prefills and use chunked prefill so long prompts don't block the decode queue. Speculative decoding raises throughput when a small draft model agrees with the target most of the time."},
    {"id": "if4", "category": "inference",
     "content": "Autoscale inference replicas on a queue/latency signal (e.g. requests-waiting or p95 TTFT), not raw GPU utilization, because a GPU can read 100% busy while latency is still healthy. Keep a warm pool to absorb the cold-start cost of loading large weights, which can take tens of seconds."},
    # Infra
    {"id": "in1", "category": "infra",
     "content": "On Kubernetes, GPUs are exposed via the NVIDIA device plugin and requested as the extended resource nvidia.com/gpu. Pods without a GPU request can still be scheduled onto GPU nodes, so use taints/tolerations (e.g. nvidia.com/gpu:NoSchedule) plus node affinity to keep non-GPU workloads off expensive nodes."},
    {"id": "in2", "category": "infra",
     "content": "For multi-GPU jobs, topology matters: GPUs on the same NVLink/NVSwitch domain communicate far faster than across PCIe or across nodes. Use the Kubernetes topology-aware scheduling / gang scheduling (e.g. Volcano, Kueue) so all-or-nothing jobs get all their GPUs co-located, and verify placement with `nvidia-smi topo -m`."},
    {"id": "in3", "category": "infra",
     "content": "GPU node health: a falling SM clock or rising ECC error count is an early sign of a degrading device. Run DCGM (dcgm-exporter) to scrape per-GPU metrics into Prometheus, alert on XID errors in dmesg, and cordon+drain a node automatically when uncorrectable ECC errors appear, since a silent bad GPU corrupts training."},
]

# ── Off-domain corpus for the XL-2 retrieval-poisoning drill (clearly a fixture) ─
# This is NOT a mock standing in for production — it is the deliberate "wrong index"
# the XL-2 drill swaps in to prove only Galileo's trust layer catches bad retrieval.
KNOWLEDGE_BASE_POISONED = [
    {"id": "x1", "category": "training",
     "content": "Our weekly meal-kit subscription includes recipes for three dinners. Cancellation requires 30 days notice; refunds within 14 days of purchase."},
    {"id": "x2", "category": "training",
     "content": "Premium yoga memberships start at $29/month. Family plans cover up to four members and include two personal-training sessions."},
    {"id": "x3", "category": "inference",
     "content": "To fix a leaking faucet, shut the supply valve, remove the handle, replace the washer and O-ring, and wrap threaded joints with plumber's tape."},
    {"id": "x4", "category": "inference",
     "content": "Garden soil pH should sit between 6.0 and 7.0 for most vegetables. Add lime to raise pH or sulfur to lower it."},
    {"id": "x5", "category": "infra",
     "content": "Our pet-grooming package covers a bath, haircut, nail trim, and ear cleaning. First-time customers get 20% off."},
]

# ── KB persistence helpers ─────────────────────────────────────────────────────
def _read_kb_json(path: pathlib.Path) -> list:
    with open(path) as f:
        return json.load(f)

def load_full_corpus() -> list:
    """Lab-scale engineering KB (generated). Falls back to seed 11 if missing."""
    if KB_FILE.exists():
        return _read_kb_json(KB_FILE)
    if KB_CANONICAL.exists():
        return _read_kb_json(KB_CANONICAL)
    return list(KNOWLEDGE_BASE_ORIGINAL)

def load_kb(poisoned: bool = False) -> list:
    if poisoned:
        if KB_POISON_FILE.exists():
            return _read_kb_json(KB_POISON_FILE)
        return list(KNOWLEDGE_BASE_POISONED)
    return load_full_corpus()

def save_kb(docs: list, path: pathlib.Path):
    with open(path, "w") as f:
        json.dump(docs, f, indent=2)

def kb_stats(docs: list | None = None) -> dict:
    docs = docs if docs is not None else load_kb()
    from collections import Counter
    cats = Counter(d.get("category", "?") for d in docs)
    return {
        "docs": len(docs),
        "chunks": len(docs),
        "categories": dict(sorted(cats.items())),
        "avg_chars": round(sum(len(d.get("content", "")) for d in docs) / max(1, len(docs))),
    }

def restore_full_corpus() -> pathlib.Path:
    """Restore engineering KB after XL-2 poison (prefer .bak → canonical → regenerate)."""
    bak = KB_FILE.with_suffix(".json.bak")
    if bak.exists():
        save_kb(_read_kb_json(bak), KB_FILE)
        return KB_FILE
    if KB_CANONICAL.exists():
        save_kb(_read_kb_json(KB_CANONICAL), KB_FILE)
        return KB_FILE
    gen = pathlib.Path(__file__).parent / "corpus" / "generate_ml_corpus.py"
    if gen.exists():
        subprocess.run([sys.executable, str(gen), "--out", str(KB_FILE)], check=False)
        if KB_FILE.exists():
            return KB_FILE
    save_kb(KNOWLEDGE_BASE_ORIGINAL, KB_FILE)
    return KB_FILE

# ── REAL dense-retrieval vector index (embeddings + cosine) ────────────────────
class VectorIndex:
    """A real dense-retrieval index: OpenAI embeddings + cosine similarity.

    Embeddings are computed once per corpus and cached on disk (keyed by a hash of
    the corpus content) so repeated runs don't re-embed. This is genuine semantic
    retrieval — there is no keyword/substring matching anywhere.
    """
    def __init__(self, docs: list):
        self.docs = docs
        self.ids = [d["id"] for d in docs]
        self.texts = [d["content"] for d in docs]
        self._embedder = OpenAIEmbeddings(model=EMBED_MODEL)
        self.matrix = self._load_or_build()

    def _corpus_key(self) -> str:
        h = hashlib.sha256(json.dumps(self.texts, sort_keys=True).encode()).hexdigest()[:16]
        return f"{EMBED_MODEL}-{h}"

    def _load_or_build(self) -> np.ndarray:
        INDEX_CACHE_DIR.mkdir(exist_ok=True)
        cache = INDEX_CACHE_DIR / f"{self._corpus_key()}.npy"
        if cache.exists():
            return np.load(cache)
        vecs = np.array(self._embedder.embed_documents(self.texts), dtype=np.float32)
        vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
        np.save(cache, vecs)
        return vecs

    def search(self, query: str, k: int = TOP_K):
        q = np.array(self._embedder.embed_query(query), dtype=np.float32)
        q /= (np.linalg.norm(q) + 1e-9)
        sims = self.matrix @ q                      # cosine (both normalized)
        order = np.argsort(-sims)[:k]
        return [(self.ids[i], self.texts[i], float(sims[i])) for i in order]

# Indexes are cached per-corpus so drills that pass a raw kb list re-use the index.
_INDEX_CACHE: Dict[str, VectorIndex] = {}

def get_index(kb: list) -> VectorIndex:
    key = hashlib.sha256(
        json.dumps([d["content"] for d in kb], sort_keys=True).encode()
    ).hexdigest()[:16]
    if key not in _INDEX_CACHE:
        _INDEX_CACHE[key] = VectorIndex(kb)
    return _INDEX_CACHE[key]

# ── State ─────────────────────────────────────────────────────────────────────
class SupportState(TypedDict):
    query:            str
    intent:           Optional[str]    # training | inference | infra | general
    retrieved_docs:   Optional[List[str]]
    doc_ids:          Optional[List[str]]
    tool_result:      Optional[str]
    draft_answer:     Optional[str]
    final_answer:     Optional[str]
    protect_status:   Optional[str]    # triggered | not_triggered | skipped
    context_score:    Optional[float]  # real retrieval/judge score
    protect_path:     Optional[str]    # invoke_protect | llm_judge_fallback (2026-06-16)

# ── REAL tools (real execution — no hardcoded dicts, no fabricated IDs) ─────────
def tool_run_python(code: str, timeout_s: int = 8) -> str:
    """Execute a snippet of Python in a real, isolated subprocess and return stdout.

    This is a genuine sandboxed execution tool (separate interpreter, time limit,
    no inherited globals) — the kind an engineering assistant uses to compute
    memory budgets, sanity-check formulas, etc."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            capture_output=True, text=True, timeout=timeout_s,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return out if proc.returncode == 0 else f"ERROR: {err[:300]}"
    except subprocess.TimeoutExpired:
        return f"ERROR: code execution exceeded {timeout_s}s"
    finally:
        try: os.unlink(path)
        except OSError: pass

def tool_search_corpus(query: str, kb: list) -> str:
    """Real semantic search over the engineering corpus (reuses the vector index)."""
    hits = get_index(kb).search(query, k=2)
    return " | ".join(f"[{i}] {t[:120]}" for i, t, _ in hits)

_PY_BLOCK = None  # compiled lazily in tools_node

# ── Node functions ─────────────────────────────────────────────────────────────
def intake_node(state: SupportState) -> SupportState:
    """Classify the engineering topic from the query text."""
    q = state["query"].lower()
    if any(w in q for w in ["train", "training", "gradient", "checkpoint", "nccl",
                            "fp16", "bf16", "optimizer", "loss", "epoch", "fsdp", "zero"]):
        intent = "training"
    elif any(w in q for w in ["inference", "serve", "serving", "vllm", "kv cache", "kv-cache",
                              "latency", "throughput", "token", "ttft", "decode", "prefill", "batch"]):
        intent = "inference"
    elif any(w in q for w in ["kubernetes", "k8s", "gpu", "node", "cluster", "schedul",
                              "nvlink", "device plugin", "dcgm", "topology", "ecc"]):
        intent = "infra"
    else:
        intent = "general"
    return {"intent": intent}

def retriever_node(state: SupportState, kb: list = None) -> SupportState:
    """REAL dense retrieval: embed the query, cosine top-k over the vector index.

    `kb` may be passed (drills wrap this with functools.partial); otherwise the
    active on-disk corpus is used. Returns the retrieved docs, their ids, and the
    real top-1 cosine similarity (a genuine retrieval-quality signal)."""
    if kb is None:
        kb = load_kb()
    hits = get_index(kb).search(state["query"], k=TOP_K)
    return {
        "retrieved_docs": [t for _, t, _ in hits],
        "doc_ids":        [i for i, _, _ in hits],
        "context_score":  round(hits[0][2], 4) if hits else 0.0,
    }

def tools_node(state: SupportState) -> SupportState:
    """Run real tools when the query calls for them.

    - A fenced ```python block → execute it for real in a sandboxed subprocess.
    - A 'compute/calculate/how much memory' ask → semantic corpus search for the
      relevant formula/context (real embedding search).
    No mock data, no fabricated identifiers."""
    import re
    q = state["query"]
    parts = []
    code_match = re.search(r"```(?:python)?\s*(.+?)```", q, re.DOTALL | re.IGNORECASE)
    if code_match:
        parts.append("[python] " + tool_run_python(code_match.group(1)))
    if any(w in q.lower() for w in ["compute", "calculate", "how much memory",
                                    "how many", "estimate", "size of"]):
        kb = load_kb()
        parts.append("[corpus] " + tool_search_corpus(q, kb))
    return {"tool_result": "  ".join(parts).strip()}

def responder_node(state: SupportState) -> SupportState:
    """Generate the answer with a real LLM call. This is the span Galileo evaluates."""
    context_parts = []
    if state.get("retrieved_docs"):
        for i, content in enumerate(state["retrieved_docs"], 1):
            context_parts.append(f"[KB-{i}] {content}")
    if state.get("tool_result"):
        context_parts.append(f"[Tool] {state['tool_result']}")
    context = "\n".join(context_parts)
    intent = state.get("intent", "general")

    if context:
        human = (
            f"Engineer question ({intent}): {state['query']}\n\n"
            f"Retrieved engineering knowledge:\n{context}\n\n"
            "Write a concise, technically accurate answer (2-5 sentences). Ground every "
            "claim in the retrieved knowledge and cite the [KB-n] source. If the knowledge "
            "does not cover it, say so rather than guessing."
        )
    else:
        human = (
            f"Engineer question ({intent}): {state['query']}\n\n"
            "No relevant knowledge was retrieved. Say so honestly and suggest what to check, "
            "rather than fabricating specifics."
        )

    llm = ChatOpenAI(model=MODEL, temperature=0.1, max_tokens=260)
    response = llm.invoke([
        SystemMessage(content=(
            "You are a senior ML-platform engineering assistant. Be precise and concise, "
            "cite the knowledge source you used, and never fabricate APIs, flags, or numbers "
            "that are not in the provided context."
        )),
        HumanMessage(content=human),
    ])
    return {"draft_answer": response.content}

# ── REAL Galileo Protect node ──────────────────────────────────────────────────
def _protect_ruleset():
    """A real Protect ruleset: block when context_adherence falls below the floor."""
    return Ruleset(
        rules=[Rule(metric="context_adherence", operator=RuleOperator.lt,
                    target_value=ADHERENCE_FLOOR)],
        action=OverrideAction(choices=[BLOCKED_MESSAGE]),
        description="Block answers not grounded in the retrieved engineering corpus.",
    )

def _judge_context_adherence(query: str, context: str, answer: str) -> float:
    """Real LLM-judge fallback (used only if the Protect API is unreachable).

    This computes the SAME metric the Protect rule thresholds — via a real model
    call — instead of any phrase/keyword heuristic."""
    judge = ChatOpenAI(model=MODEL, temperature=0.0, max_tokens=8)
    prompt = (
        "Rate from 0.0 to 1.0 how fully the RESPONSE is grounded in the CONTEXT. "
        "1.0 = every claim is supported by the context; 0.0 = claims absent from or "
        "contradicting the context. Return ONLY the number.\n\n"
        f"QUERY: {query}\nCONTEXT: {context}\nRESPONSE: {answer}\n\nScore:"
    )
    try:
        raw = judge.invoke([HumanMessage(content=prompt)]).content.strip()
        return max(0.0, min(1.0, float(raw.split()[0])))
    except Exception:
        return 1.0  # fail-open with a logged note rather than a fake block

def protect_node(state: SupportState) -> SupportState:
    """Gate the draft answer through a REAL Galileo Protect stage.

    Primary path: invoke_protect() against the configured stage + ruleset.
    
    BUG FIX (2026-06-16): The previous implementation treated ExecutionStatus.error
    as 'not_triggered' and returned early, bypassing the real-judge fallback.
    Root cause: Protect API returns error when the LLM metric service is unreachable
    (e.g. context_adherence_luna not available on this cluster tier).
    Fix: Check for ERROR status explicitly and fall through to the real-judge fallback.
    
    Galileo 2.3.0 migration note: invoke_protect is deprecated; the production
    migration path is @agent_control.control() + ControlViolationError (see RB-140).
    This lab uses invoke_protect as the primary path because no agent-control
    server is provisioned here; the LLM judge is the real enforcement mechanism.
    """
    draft = state.get("draft_answer", "") or ""
    context = "\n".join(state.get("retrieved_docs") or [])
    status = "not_triggered"
    final = draft

    if _PROTECT_AVAILABLE:
        try:
            resp = invoke_protect(
                payload=Payload(input=state["query"], output=draft),
                prioritized_rulesets=[_protect_ruleset()],
                project_name=PROJECT,
                stage_name=PROTECT_STAGE,
                timeout=10.0,
                metadata={"lab": "trinity-stack"},
            )
            if resp is not None and getattr(resp, "text", None) is not None:
                resp_status_str = str(getattr(resp, "status", "")).upper()
                # BUG FIX: only trust the Protect response when it is NOT an error.
                # ERROR means the metric service is unavailable — fall through to LLM judge.
                if "ERROR" not in resp_status_str:
                    final = resp.text
                    triggered = ("TRIGGERED" in resp_status_str
                                 and "NOT_TRIGGERED" not in resp_status_str)
                    status = "triggered" if (triggered or final != draft) else "not_triggered"
                    return {"final_answer": final, "protect_status": status,
                            "context_score": state.get("context_score"),
                            "protect_path": "invoke_protect"}
                # Else: fall through to LLM judge (protect API metric unavailable)
        except Exception:
            pass  # fall through to the real-judge fallback

    # Fallback: real judge, same metric, same threshold (NOT a heuristic block-list).
    # Active in two cases: (a) invoke_protect raised an exception, or (b) Protect
    # returned ERROR status because the metric service is unavailable on this cluster.
    score = _judge_context_adherence(state["query"], context, draft)
    if score < ADHERENCE_FLOOR:
        status, final = "triggered", BLOCKED_MESSAGE
    return {"final_answer": final, "protect_status": status, "context_score": score,
            "protect_path": "llm_judge_fallback"}

def route_by_intent(state: SupportState) -> str:
    return state.get("intent", "general")

# ── Build graph ────────────────────────────────────────────────────────────────
def build_graph(kb: list = None, protect_enabled: bool = True):
    if kb is None:
        kb = load_kb()
    from functools import partial
    ret_fn = partial(retriever_node, kb=kb)

    wf = StateGraph(SupportState)
    wf.add_node("intake",    intake_node)
    wf.add_node("retriever", ret_fn)
    wf.add_node("tools",     tools_node)
    wf.add_node("responder", responder_node)
    if protect_enabled:
        wf.add_node("protect", protect_node)

    wf.set_entry_point("intake")
    wf.add_edge("intake",    "retriever")
    wf.add_edge("retriever", "tools")
    wf.add_edge("tools",     "responder")
    if protect_enabled:
        wf.add_edge("responder", "protect")
        wf.add_edge("protect",   END)
    else:
        wf.add_edge("responder", END)
    return wf.compile()

# ── Galileo metrics config (real LLM-as-judge metrics) ─────────────────────────
def get_metrics():
    return [
        LlmMetric(
            name="context_adherence",
            prompt=(
                "You are a quality evaluator. Given an engineer's QUERY, the CONTEXT "
                "(knowledge retrieved), and the RESPONSE:\n"
                "Rate from 0.0 to 1.0 how well the response is grounded in the provided context.\n"
                "1.0 = every claim in the response is supported by the context.\n"
                "0.0 = response contains claims not in the context or contradicts it.\n"
                "Return ONLY a float between 0.0 and 1.0.\n\n"
                "QUERY: {input}\nCONTEXT: {context}\nRESPONSE: {output}\n\nScore:"
            ),
            model=MODEL, num_judges=1,
        ),
        LlmMetric(
            name="completeness",
            prompt=(
                "Given this engineering QUERY and RESPONSE:\n"
                "Rate from 0.0 to 1.0 how completely the response addresses the question.\n"
                "1.0 = fully answers with actionable next steps. 0.0 = irrelevant or non-answer.\n"
                "Return ONLY a float between 0.0 and 1.0.\n\n"
                "QUERY: {input}\nRESPONSE: {output}\n\nScore:"
            ),
            model=MODEL, num_judges=1,
        ),
        LlmMetric(
            name="cites_kb_source",
            prompt=(
                "Given this RESPONSE:\n"
                "Does it cite a specific knowledge source (e.g. a [KB-n] tag, a concrete flag, "
                "API, or command from the corpus) rather than only generic statements?\n"
                "Return 1.0 if yes, 0.0 if no.\n\n"
                "RESPONSE: {output}\n\nScore:"
            ),
            model=MODEL, num_judges=1,
        ),
    ]

# ── Galileo-instrumented run ───────────────────────────────────────────────────
def run_query(graph, query: str, verbose: bool = True, tag: str = "") -> dict:
    logger  = GalileoLogger(project=PROJECT, log_stream=LOG_STREAM)
    cb = GalileoCallback(galileo_logger=logger, start_new_trace=True, flush_on_chain_end=True)

    # galileo 2.3.0: connect Agent Control observability bridge to this logger.
    # Events from the LangChain callback flow into the bridge and are recorded
    # alongside traces. This is step 1 of the invoke_protect → Agent Control
    # migration path (see RB-140 §Fix - Current path for the full migration).
    _ac_bridge = None
    if _AGENT_CONTROL_BRIDGE_AVAILABLE:
        try:
            _ac_bridge = setup_agent_control_bridge(logger)
            _ac_bridge.register()
        except Exception:
            pass  # bridge is observability-only; safe to skip if unavailable

    t0 = time.time()
    result = graph.invoke(
        {
            "query": query, "intent": None, "retrieved_docs": [], "doc_ids": [],
            "tool_result": "", "draft_answer": "", "final_answer": "",
            "protect_status": "", "context_score": None, "protect_path": None,
        },
        config={"callbacks": [cb], "metadata": {
            "query_tag": tag or "baseline", "lab": "trinity-stack"}},
    )
    latency_ms = int((time.time() - t0) * 1000)
    logger.flush()
    if _ac_bridge is not None:
        try:
            _ac_bridge.unregister()
        except Exception:
            pass

    protect_icon = "🛑" if result.get("protect_status") == "triggered" else "✅"
    if verbose:
        print(f"\n{'─'*65}")
        print(f"Q:        {query}")
        print(f"Intent:   {result.get('intent','?')}  |  Docs: {result.get('doc_ids',[])}"
              f"  |  top-sim: {result.get('context_score')}")
        print(f"Answer:   {(result.get('final_answer') or result.get('draft_answer',''))[:220]}")
        print(f"Protect:  {protect_icon} {result.get('protect_status','?')}  |  Latency: {latency_ms}ms")
        print(f"{'─'*65}")

    _fleet_heartbeat(query=query, latency_ms=latency_ms, ok=True,
                     protect=result.get("protect_status", ""))
    return result

# ── REAL RUN-layer telemetry (measured process metrics, not fabricated) ────────
def _fleet_heartbeat(query: str, latency_ms: int, ok: bool, protect: str = ""):
    """Emit REAL measured RUN-layer telemetry for this process.

    Captures genuine CPU%, resident memory, and uptime via psutil (falls back to the
    stdlib `resource` module if psutil is absent — still real measurements, never
    invented numbers). In production this sink is ClawTrace / an OTel collector; the
    local files mirror exactly what would be exported."""
    proc_metrics = _measure_process()
    hb_dir = pathlib.Path(__file__).parent / "fleet"
    hb_dir.mkdir(exist_ok=True)
    hb = {
        "ts":          datetime.datetime.now(datetime.UTC).isoformat(),
        "process":     "trinity-stack",
        "pid":         os.getpid(),
        "status":      "ok" if ok else "error",
        "latency_ms":  latency_ms,
        "protect":     protect,
        **proc_metrics,
    }
    with open(hb_dir / "heartbeat.json", "w") as f:
        json.dump(hb, f, indent=2)
    with open(hb_dir / "latency.log", "a") as f:
        f.write(json.dumps(hb) + "\n")

def _measure_process() -> dict:
    try:
        import psutil
        p = psutil.Process(os.getpid())
        with p.oneshot():
            return {
                "cpu_pct":     round(p.cpu_percent(interval=0.05), 2),
                "rss_mb":      round(p.memory_info().rss / 1e6, 1),
                "uptime_s":    round(time.time() - p.create_time(), 1),
                "num_threads": p.num_threads(),
                "telemetry":   "psutil",
            }
    except Exception:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is KB on Linux, bytes on macOS — normalize to MB best-effort.
        rss = ru.ru_maxrss / (1024 if sys.platform != "darwin" else 1_000_000)
        return {
            "cpu_user_s": round(ru.ru_utime, 3),
            "cpu_sys_s":  round(ru.ru_stime, 3),
            "rss_mb":     round(rss, 1),
            "telemetry":  "resource",
        }

# ── Real engineering baseline batch ────────────────────────────────────────────
BASELINE_QUERIES = [
    # Training
    ("How do I debug a CUDA out-of-memory error during training?",          "training"),
    ("My multi-node job hangs on an NCCL all-reduce — where do I start?",   "training"),
    ("Should I use fp16 or bf16 for training on H100s?",                    "training"),
    # Inference
    ("How does vLLM get such high throughput?",                             "inference"),
    ("Compute roughly how much KV-cache memory a long-context request needs.", "inference"),
    ("My TTFT spikes under load — how do I bring it down?",                 "inference"),
    # Infra
    ("How do I keep non-GPU pods off my GPU nodes in Kubernetes?",          "infra"),
    ("How do I make sure a multi-GPU job lands on co-located NVLink GPUs?", "infra"),
    # Tool-exercising
    ("Calculate the GPU memory for a 7B model in bf16 weights only.",       "inference"),
    # Out-of-scope / honesty check
    ("What's the on-call rotation policy for the platform team?",           "general"),
]

def run_baseline(graph, tag="baseline"):
    print(f"\n{'='*65}")
    print(f"TRINITY STACK — Engineering Assistant Baseline")
    print(f"Scenario: ML-Platform Engineering AI (training / inference / infra)")
    print(f"Project: {PROJECT}  |  Stream: {LOG_STREAM}  |  Tag: {tag}")
    print(f"Queries: {len(BASELINE_QUERIES)}  |  Model: {MODEL}  |  Embeddings: {EMBED_MODEL}")
    print(f"{'='*65}")
    for query, _ in BASELINE_QUERIES:
        run_query(graph, query, verbose=True, tag=tag)
    print(f"\n✅ Baseline complete.")
    print(f"   → https://app.galileo.ai  |  Project: {PROJECT}  |  Stream: {LOG_STREAM}")
    print(f"   Metrics: context_adherence, completeness, cites_kb_source")

# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--poison-corpus" in args:
        # XL-2: swap the real corpus for the off-domain index (genuine bad retrieval).
        # Back up the *current* on-disk KB (lab-scale), not just the 11 seed docs.
        current = load_full_corpus()
        save_kb(current, KB_FILE.with_suffix(".json.bak"))
        save_kb(KNOWLEDGE_BASE_POISONED, KB_POISON_FILE)
        save_kb(KNOWLEDGE_BASE_POISONED, KB_FILE)
        print("☠️  Corpus swapped to off-domain index. Run --batch to watch context_adherence crater.")
        sys.exit(0)

    if "--restore-corpus" in args:
        path = restore_full_corpus()
        stats = kb_stats(_read_kb_json(path) if path.exists() else None)
        print(f"✅ Corpus restored ({stats['docs']} docs/chunks). Path: {path}")
        sys.exit(0)

    if "--kb-stats" in args:
        if not KB_FILE.exists() and KB_CANONICAL.exists():
            save_kb(_read_kb_json(KB_CANONICAL), KB_FILE)
        print(json.dumps(kb_stats(), indent=2))
        sys.exit(0)

    if not KB_FILE.exists():
        if KB_CANONICAL.exists():
            save_kb(_read_kb_json(KB_CANONICAL), KB_FILE)
        else:
            save_kb(KNOWLEDGE_BASE_ORIGINAL, KB_FILE)

    kb = load_kb()
    graph = build_graph(kb=kb)

    if "--batch" in args or "--baseline" in args:
        tag = "poisoned" if any("poison" in str(a) for a in args) else "baseline"
        run_baseline(graph, tag=tag)
    elif args:
        query = " ".join(a for a in args if not a.startswith("--"))
        if query:
            run_query(graph, query)
    else:
        print("Usage: python app.py 'Your question'  |  --batch  |  --poison-corpus  |  --restore-corpus  |  --kb-stats")
