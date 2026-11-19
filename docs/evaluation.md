# Evaluation

## Retrieval

`webmind eval questions.jsonl -k 5` reports Recall@K, Precision@K, MRR, nDCG@K and p50/p95
latency. One case per line:

```json
{"question": "Where are the eligibility requirements?", "expected": ["admissions#eligibility"], "user": {"roles": ["employee"]}}
```

A result is relevant if its deep link contains any `expected` substring. `user` is optional
(anonymous otherwise), so you can evaluate what each role actually retrieves.

The repository gates regressions on `tests/evaluation/demo_retrieval.jsonl` (15 cases).
Measured on 2026-11-19 with the built-in hashing embedder: Recall@5 1.00, MRR 0.93,
nDCG@5 0.95, p95 ≈ 6 ms on a laptop. That is a small, author-written set on a tiny site, so treat it as a
regression guard rather than a quality claim. Build a question set from your own content.

## Security

`pytest -m security` runs the release-blocking boundary tests: token forgery and algorithm
confusion, tenant isolation, restricted-content retrieval (vector and keyword), row/date
policies, query allowlisting and injection, read-only sources, export ownership and
formula injection, audit of denials, and privilege escalation by a model-proposed plan.

## Not yet covered

Groundedness/citation-correctness scoring against a live LLM, load testing, and
synchronization fault-injection beyond new/changed/deleted/unchanged/truncated-crawl cases
are not part of 0.1.0. See [status.md](status.md).
