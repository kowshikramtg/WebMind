"""Retrieval regression gate on the versioned demo dataset (tests/evaluation/demo_retrieval.jsonl).

Thresholds reflect the zero-dependency hashing embedder + FTS5 baseline; neural
embedding models are expected to score higher. Raise thresholds when quality improves.
"""

from pathlib import Path

from webmind.evaluation import run_retrieval_eval, score_case

DATASET = Path(__file__).resolve().parents[1] / "evaluation" / "demo_retrieval.jsonl"


def test_metric_math():
    s = score_case(["a#x", "b", "c"], ["b"], k=3)
    assert s == {"recall": 1.0, "precision": 1 / 3, "mrr": 0.5, "ndcg": 1 / __import__("math").log2(3)}
    assert score_case([], ["b"], k=3)["recall"] == 0.0


async def test_demo_retrieval_quality(runtime):
    report = await run_retrieval_eval(runtime, DATASET, k=5)
    assert report["cases"] == 15
    assert report["recall@5"] >= 0.9, report["failures"]
    assert report["mrr"] >= 0.75, report["failures"]
    assert report["latency_ms"]["p95"] < 2000
