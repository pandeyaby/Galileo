"""
drills/xl4b_out_of_scope.py — XL-4b: Force a REAL Galileo Protect block.

XL-4 proved the Protect pipeline is wired but Protect correctly did NOT fire,
because even the "answer confidently" prompt produced grounded answers against a
tight corpus. To demonstrate a genuine BLOCK, we pair the hallucination-prone
prompt with an OUT-OF-SCOPE query set: questions about products/specs that do NOT
exist in the corpus. The retriever returns low-similarity junk, the bad prompt
forbids "I don't know", so the model fabricates -> context_adherence drops below
the 0.5 floor -> the REAL invoke_protect() rule fires -> the answer is blocked.

Run (from the trinity-stack root, venv active, keys set):
    python drills/xl4b_out_of_scope.py

Expected: several queries return protect_status = "triggered" (blocked).
Then read the Console (tag: xl4b-out-of-scope) for the real context_adherence
scores and the blocked count. This is the follow-up the article's XL-4 flags.
"""
import sys, pathlib
# make `from app import ...` and the sibling xl4 import resolve when run directly
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "drills"))

from app import protect_node, PROJECT, LOG_STREAM
from xl4_eval_to_protect import (
    HALLUCINATION_SYSTEM_PROMPT,
    build_variant_graph,
    run_q,
    print_result,
    banner,
)

# Out-of-scope: engineering-flavored questions about things that DO NOT exist in
# the ML-infra corpus, so there is no document to ground on. Adjust freely.
OUT_OF_SCOPE_QUERIES = [
    "What exact --hypertorch-shard flag enables 12-way tensor parallelism on the Zephyr-9 interconnect?",
    "What NCCL_QUANTUM_BUFFER value does the Fooblitz scheduler recommend for an 8,000-GPU pod?",
    "What's the precise KV-cache memory formula for Mistral-Nemotron-405B on TPU v7 slices?",
    "Which flag enables chunked prefill in the AcmeServe 3.2 inference runtime?",
    "What Kubernetes CRD name requests a Groq LPU accelerator, exactly?",
    "What's the recommended gradient-compression ratio for the Helios-X optical fabric?",
]

TAG = "xl4b-out-of-scope"

def run_drill():
    print("\nXL-4b DRILL: Force a real Protect block (bad prompt + out-of-scope queries)")
    print(f"   Project: {PROJECT}  |  Stream: {LOG_STREAM}  |  Tag: {TAG}")
    banner("PROD + Protect: hallucination-prone prompt, no supporting docs")

    # bad prompt + REAL protect node (blocks when context_adherence < 0.5)
    graph = build_variant_graph(HALLUCINATION_SYSTEM_PROMPT, protect_node)

    results = []
    for q in OUT_OF_SCOPE_QUERIES:
        r = run_q(graph, q, tag=TAG, label="bad-prompt-out-of-scope")
        print_result(q, r, "prod-with-protect")
        results.append(r)

    triggered = sum(1 for r in results if r.get("protect_status") == "triggered")
    passed    = sum(1 for r in results if r.get("protect_status") == "not_triggered")
    other     = len(results) - triggered - passed

    banner("RESULT")
    print(f"  Protect results:  BLOCKED (triggered): {triggered}  |  passed: {passed}  |  other: {other}")
    print(f"  → If triggered > 0, XL-4b demonstrates a real block on ungrounded answers.")
    print(f"  → Read the Console (tag: {TAG}) for the actual context_adherence values,")
    print(f"    then report: blocked count, passed count, and the adherence range.")
    print(f"  Console: https://app.galileo.ai  →  {PROJECT}  →  {LOG_STREAM}  (filter tag={TAG})")

if __name__ == "__main__":
    run_drill()
