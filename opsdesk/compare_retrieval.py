"""Compare retrievers on the dev (practice) split of scenarios-v1 only.

Metrics on supported tickets: hit@1, recall@3, MRR@3 (first expected passage).
No-evidence check: can the top score separate supported tickets from unsupported/vague ones? We report
the best achievable ('oracle') balanced accuracy of a single threshold on the evaluated split (optimistic by construction),
and, off dev, the balanced accuracy of the dev-chosen threshold applied unchanged,
and the score ranges, rather than claiming a working detector.
Outdated-policy check: the same retrievers with the superseded passage indexed; how often an outdated
passage ranks first, and whether resolve_current maps every hit to the current version.
"""
from __future__ import annotations

import argparse
import json
import time

from .handbook import load_handbook
from .retrieval import BM25Retriever, DenseRetriever, HybridRetriever, KeywordRetriever, resolve_current, ticket_text
from .schemas import DATA_DIR, Scenario


def best_threshold(sup, unsup):
    best = (0.0, None)
    for t in sorted(set(sup + unsup)):
        tpr = sum(s >= t for s in sup) / len(sup)
        tnr = sum(s < t for s in unsup) / len(unsup)
        best = max(best, ((tpr + tnr) / 2, t), key=lambda x: x[0])
    return best


def dev_bal(name, sup, unsup):
    """Balanced accuracy of the threshold chosen on dev, applied unchanged (only meaningful off dev)."""
    import pathlib
    f = pathlib.Path(__file__).resolve().parents[1] / "results" / "p1_03_retrieval_dev.json"
    if not f.exists():
        return None
    thr = json.loads(f.read_text())["retrievers"][name]["no_evidence_top_score"]["oracle_threshold"]
    return {"threshold_from_dev": thr, "value": round((sum(x >= thr for x in sup) / len(sup) + sum(x < thr for x in unsup) / len(unsup)) / 2, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--split", choices=["dev", "test"], default="dev",
                    help="test = the v1 test split already used once in P1.02; report only, never tune on it")
    a = ap.parse_args()
    raw = (DATA_DIR / "tickets" / "scenarios_v1.jsonl").read_text().splitlines()
    dev = [s for s in (Scenario.model_validate_json(l) for l in raw) if s.split == a.split]
    passages, _ = load_handbook()
    t0 = time.process_time()
    bm25, dense = BM25Retriever(), DenseRetriever()
    build_cpu = time.process_time() - t0
    rets = [KeywordRetriever(), bm25, dense, HybridRetriever(bm25, dense)]
    out = {"split": f"scenarios-v1 {a.split}", "n": len(dev), "dense_model": "BAAI/bge-small-en-v1.5@5c38ec7c", "index_build_cpu_s": round(build_cpu, 2), "retrievers": {}}
    for r in rets:
        sup_rows, s_sup, s_uns, lat = [], [], [], []
        for s in dev:
            t = time.perf_counter(); hits = r.search(ticket_text(s.ticket), 3); lat.append((time.perf_counter() - t) * 1000)
            ids = [h.passage_id for h in hits]
            if s.expected_status == "proposed":
                exp = s.expected_passages[0]
                sup_rows.append((ids[:1] == [exp], exp in ids, next((1 / (i + 1) for i, p in enumerate(ids) if p == exp), 0.0), s.ticket.ticket_id, exp, ids))
                s_sup.append(hits[0].score)
            else:
                s_uns.append(hits[0].score)
        n = len(sup_rows)
        bal, thr = best_threshold(s_sup, s_uns)
        out["retrievers"][r.name] = {
            "hit_at_1": {"n": sum(x[0] for x in sup_rows), "d": n}, "recall_at_3": {"n": sum(x[1] for x in sup_rows), "d": n},
            "mrr_at_3": round(sum(x[2] for x in sup_rows) / n, 4),
            "no_evidence_top_score": {"supported_min": round(min(s_sup), 4), "unsupported_or_vague_max": round(max(s_uns), 4),
                                       "n_unsupported_or_vague": len(s_uns), "oracle_threshold_balanced_acc_on_this_split": round(bal, 4), "oracle_threshold": thr,
                                       "dev_threshold_balanced_acc": dev_bal(r.name, s_sup, s_uns)},
            "median_latency_ms": round(sorted(lat)[len(lat) // 2], 2),
            "misses_at_1": [{"ticket": x[3], "expected": x[4], "top3": x[5]} for x in sup_rows if not x[0]],
        }
    # outdated-policy check: index includes the superseded passage
    conflict = [s for s in dev if s.scenario_type == "conflicting_sources"]
    ob, od = BM25Retriever(include_superseded=True), DenseRetriever(include_superseded=True)
    oc = {}
    for r in (ob, od):
        rows = []
        for s in dev:
            if s.expected_status != "proposed":
                continue
            top = r.search(ticket_text(s.ticket), 1)[0].passage_id
            rows.append((s.scenario_type, top, resolve_current(top, passages), s.expected_passages[0]))
        oc[r.name] = {"top1_is_superseded": sum(passages[t].superseded_by is not None for _, t, _, _ in rows),
                      "top1_superseded_on_conflicting_tickets": sum(passages[t].superseded_by is not None for ty, t, _, _ in rows if ty == "conflicting_sources"),
                      "n_conflicting_tickets": len(conflict),
                      "resolved_hits_still_superseded": sum(passages[c].superseded_by is not None for _, _, c, _ in rows),
                      "d": len(rows)}
    out["outdated_policy_check_with_superseded_indexed"] = oc
    out["total_cpu_s"] = round(time.process_time() - t0, 2)
    s = json.dumps(out, indent=2)
    if a.out:
        open(a.out, "w").write(s + "\n")
    print(s)


if __name__ == "__main__":
    main()
