"""Evaluate the rules baseline on a split of scenarios-v1 and print/write honest metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections import Counter, defaultdict
from pathlib import Path

from .baseline import RulesBaseline
from .schemas import DATA_DIR, Scenario, check_proposal

SCEN = DATA_DIR / "tickets" / "scenarios_v1.jsonl"


def macro_f1(pairs):
    labels = sorted({g for g, _ in pairs} | {p for _, p in pairs}, key=str)
    f1s = {}
    for lab in labels:
        tp = sum(g == lab and p == lab for g, p in pairs)
        fp = sum(g != lab and p == lab for g, p in pairs)
        fn = sum(g == lab and p != lab for g, p in pairs)
        f1s[str(lab)] = 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)
    return sum(f1s.values()) / len(f1s), f1s


def frac(n, d):
    return {"n": n, "d": d, "value": round(n / d, 4) if d else None}


def run(split: str):
    raw = SCEN.read_bytes()
    scenarios = [Scenario.model_validate_json(l) for l in raw.decode().splitlines() if l.strip()]
    scenarios = [s for s in scenarios if s.split == split]
    bl = RulesBaseline()
    rows, lat = [], []
    for s in scenarios:
        t0 = time.perf_counter()
        prop, ranked = bl.propose(s.ticket)
        lat.append((time.perf_counter() - t0) * 1000)
        rows.append(dict(s=s, p=prop, ranked=[r.passage_id for r in ranked],
                         violations=check_proposal(prop, bl.catalog, bl.passages)))
    sup = [r for r in rows if r["s"].expected_status == "proposed"]
    cat_pairs = [(r["s"].expected_category and r["s"].expected_category.value, r["p"].category and r["p"].category.value) for r in rows]
    mf1, per_cat = macro_f1(cat_pairs)
    status_pairs = [(r["s"].expected_status, r["p"].status) for r in rows]

    def pr(label):
        tp = sum(g == label and p == label for g, p in status_pairs)
        return {"precision": frac(tp, sum(p == label for _, p in status_pairs)),
                "recall": frac(tp, sum(g == label for g, _ in status_pairs))}

    by_type = defaultdict(list)
    for r in rows:
        by_type[r["s"].scenario_type].append(r)
    full = lambda r: (r["p"].status == r["s"].expected_status and r["p"].action == r["s"].expected_action
                      and (r["p"].category and r["p"].category.value) == (r["s"].expected_category and r["s"].expected_category.value)
                      and r["p"].urgency == r["s"].expected_urgency
                      and tuple(c.passage_id for c in r["p"].citations) == tuple(r["s"].expected_passages))
    m = {
        "split": split, "dataset": "scenarios-v1", "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "n_scenarios": len(rows), "n_families": len({r["s"].family for r in rows}),
        "category_accuracy_incl_none": frac(sum(g == p for g, p in cat_pairs), len(rows)),
        "category_macro_f1_incl_none": round(mf1, 4), "category_f1": {k: round(v, 4) for k, v in per_cat.items()},
        "status_accuracy": frac(sum(g == p for g, p in status_pairs), len(rows)),
        "status_unsupported": pr("unsupported"), "status_needs_clarification": pr("needs_clarification"),
        "urgency_accuracy": frac(sum(r["p"].urgency == r["s"].expected_urgency for r in rows), len(rows)),
        "action_accuracy": frac(sum(r["p"].action == r["s"].expected_action for r in rows), len(rows)),
        "passage_hit_at_1_supported": frac(sum(bool(r["s"].expected_passages) and r["ranked"][:1] == [r["s"].expected_passages[0]] for r in sup), len(sup)),
        "passage_recall_at_3_supported": frac(sum(r["s"].expected_passages[0] in r["ranked"][:3] for r in sup), len(sup)),
        "cited_passage_correct_supported": frac(sum(tuple(c.passage_id for c in r["p"].citations) == tuple(r["s"].expected_passages) for r in sup), len(sup)),
        "all_fields_correct": frac(sum(full(r) for r in rows), len(rows)),
        "check_proposal_violations": sum(len(r["violations"]) for r in rows),
        "superseded_citations": sum(any(bl.passages[c.passage_id].superseded_by for c in r["p"].citations) for r in rows),
        "by_scenario_type": {k: {"n": len(v), "all_fields_correct": frac(sum(full(r) for r in v), len(v))} for k, v in sorted(by_type.items())},
        "latency_ms": {"median": round(sorted(lat)[len(lat) // 2], 3), "max": round(max(lat), 3)},
        "python": platform.python_version(),
        "errors": [dict(ticket=r["s"].ticket.ticket_id, family=r["s"].family, type=r["s"].scenario_type,
                        expected=[r["s"].expected_status, r["s"].expected_category and r["s"].expected_category.value, r["s"].expected_urgency.value, list(r["s"].expected_passages), r["s"].expected_action],
                        got=[r["p"].status, r["p"].category and r["p"].category.value, r["p"].urgency.value, [c.passage_id for c in r["p"].citations], r["p"].action],
                        top3=r["ranked"]) for r in rows if not full(r)],
    }
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    m = run(a.split)
    s = json.dumps(m, indent=2, sort_keys=True, default=str)
    if a.out:
        Path(a.out).write_text(s + "\n")
    print(s)


if __name__ == "__main__":
    main()
