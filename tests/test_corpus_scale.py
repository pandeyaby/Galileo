"""Corpus scale + loader contract (no network / no embeddings)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_knowledge_base_lab_scale():
    kb = json.loads((ROOT / "knowledge_base.json").read_text())
    assert len(kb) >= 500
    ids = {d["id"] for d in kb}
    for seed in ("tr1", "tr2", "tr3", "tr4", "if1", "if2", "if3", "if4", "in1", "in2", "in3"):
        assert seed in ids
    for d in kb:
        assert "id" in d and "category" in d and "content" in d
        assert d["content"].strip()
        # No obvious secrets
        low = d["content"].lower()
        assert "api_key" not in low
        assert "sk-" not in d["content"]


def test_generate_corpus_deterministic_size():
    from corpus.generate_ml_corpus import generate, corpus_stats

    docs = generate(target_chunks=200)
    stats = corpus_stats(docs)
    assert stats["docs"] == 200
    assert "training" in stats["categories"]
