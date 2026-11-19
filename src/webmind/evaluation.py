"""Retrieval evaluation: Recall@K, Precision@K, MRR, nDCG@K and latency percentiles.

Dataset format (JSON list or JSONL), one case per question::

    {"question": "Where are the eligibility requirements?",
     "expected": ["admissions.html#eligibility"],          # substrings of relevant result URLs
     "user": {"roles": ["employee"], "attributes": {}}}    # optional; anonymous otherwise

A result is relevant when its deep link (or URL) contains any expected substring.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from webmind.runtime import WebMindRuntime


def load_cases(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(pct / 100 * len(s)) - 1))
    return round(s[idx], 2)


def score_case(urls: list[str], expected: list[str], k: int) -> dict[str, float]:
    rel = [any(e in (u or "") for e in expected) for u in urls[:k]]
    hits = sum(rel)
    first = next((i for i, r in enumerate(rel) if r), None)
    dcg = sum(1 / math.log2(i + 2) for i, r in enumerate(rel) if r)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(expected), k)))
    matched_expected = sum(any(e in (u or "") for u in urls[:k]) for e in expected)
    return {
        "recall": matched_expected / len(expected) if expected else 0.0,
        "precision": hits / k,
        "mrr": 1 / (first + 1) if first is not None else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
    }


async def run_retrieval_eval(rt: WebMindRuntime, dataset: Path, *, k: int = 5) -> dict[str, Any]:
    cases = load_cases(dataset)
    rows = []
    latencies = []
    for case in cases:
        user = case.get("user")
        if user:
            p = rt.policy.build_principal(
                tenant_id=user.get("tenant", rt.cfg.app.default_tenant),
                user_id=user.get("id", "eval"),
                roles=user.get("roles", []),
                attributes=user.get("attributes", {}),
                authenticated=True,
            )
        else:
            p = rt.identity.anonymous()
        started = time.perf_counter()
        results = await rt.retriever.search(p, case["question"], k=k)
        latencies.append((time.perf_counter() - started) * 1000)
        urls = [r.anchor_url or r.url or "" for r in results]
        scores = score_case(urls, case["expected"], k)
        rows.append({"question": case["question"], **scores, "top": urls[:3]})
    agg = {
        m: round(statistics.fmean(r[m] for r in rows), 4) if rows else 0.0
        for m in ("recall", "precision", "mrr", "ndcg")
    }
    return {
        "cases": len(rows),
        "k": k,
        f"recall@{k}": agg["recall"],
        f"precision@{k}": agg["precision"],
        "mrr": agg["mrr"],
        f"ndcg@{k}": agg["ndcg"],
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "embedding_model": rt.embedder.model_id,
        "failures": [r for r in rows if r["recall"] < 1.0],
    }
