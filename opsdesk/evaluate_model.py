"""Evaluate local-model proposals against the rules baseline on scenarios-v1 (dev by default).

Model outputs are real inference from the pinned local model, not fixtures. Never run on scenarios-v2.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time
from collections import defaultdict
from pathlib import Path

from .baseline import RulesBaseline
from .evaluate import frac, macro_f1
from .model_proposer import MODEL_FILE, MODEL_REVISION, MODEL_REPO, MODEL_SHA256, PROMPT_VERSION, LocalModelProposer
from .schemas import DATA_DIR, Scenario, load_catalog

SCEN = DATA_DIR / "tickets" / "scenarios_v1.jsonl"


def score(rows, catalog):
    """rows: list of (scenario, proposal_or_None, violations, error)."""
    writes = {a for a, s in catalog["actions"].items() if s.get("writes")}
    n = len(rows)
    sup = [r for r in rows if r[0].expected_status == "proposed"]
    get = lambda p, f: None if p is None else f(p)
    cat = lambda p: get(p, lambda x: x.category and x.category.value)
    cite = lambda p: get(p, lambda x: tuple(c.passage_id for c in x.citations))
    full = lambda s, p: p is not None and p.status == s.expected_status and p.action == s.expected_action and \
        cat(p) == (s.expected_category and s.expected_category.value) and p.urgency == s.expected_urgency and cite(p) == tuple(s.expected_passages)
    mf1, _ = macro_f1([((s.expected_category and s.expected_category.value), cat(p)) for s, p, _, _ in rows])
    inappropriate = [s.ticket.ticket_id for s, p, v, _ in rows if p is not None and p.action in writes and p.action != s.expected_action]
    by_type = defaultdict(list)
    for s, p, v, e in rows:
        by_type[s.scenario_type].append(full(s, p))
    return {
        "n": n,
        "invalid_or_schema_rejected": sum(e is not None for *_, e in rows),
        "status_accuracy": frac(sum(p is not None and p.status == s.expected_status for s, p, _, _ in rows), n),
        "category_macro_f1_incl_none": round(mf1, 4),
        "urgency_accuracy": frac(sum(p is not None and p.urgency == s.expected_urgency for s, p, _, _ in rows), n),
        "action_accuracy": frac(sum(p is not None and p.action == s.expected_action for s, p, _, _ in rows), n),
        "cited_passage_correct_supported": frac(sum(cite(p) == tuple(s.expected_passages) for s, p, _, _ in sup), len(sup)),
        "all_fields_correct": frac(sum(full(s, p) for s, p, _, _ in rows), n),
        "proposals_with_check_violations": sum(bool(v) for *_, v, _ in rows),
        "inappropriate_write_action_proposals": {"n": len(inappropriate), "tickets": inappropriate},
        "by_scenario_type_all_fields_correct": {k: frac(sum(v), len(v)) for k, v in sorted(by_type.items())},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--model-path", default=os.environ.get("OPSDESK_MODEL_PATH", f"models/{MODEL_FILE}"))
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()
    scen = [s for s in (Scenario.model_validate_json(l) for l in SCEN.read_text().splitlines()) if s.split == a.split]
    catalog = load_catalog()
    cpu0, wall0 = time.process_time(), time.perf_counter()
    mp = LocalModelProposer(Path(a.model_path), n_threads=a.threads)
    load_s = time.perf_counter() - wall0
    bl = RulesBaseline()
    mrows, brows, records = [], [], []
    for s in scen:
        r = mp.propose(s.ticket)
        mrows.append((s, r.proposal, r.violations, r.error))
        bp, _ = bl.propose(s.ticket)
        from .schemas import check_proposal
        brows.append((s, bp, check_proposal(bp, bl.catalog, bl.passages), None))
        records.append({"ticket_id": s.ticket.ticket_id, "family": s.family, "type": s.scenario_type,
                        "expected": {"status": s.expected_status, "passages": list(s.expected_passages), "action": s.expected_action,
                                     "urgency": s.expected_urgency.value},
                        "retrieved": r.retrieved, "raw_model_output": r.raw, "error": r.error, "violations": r.violations,
                        "proposal": r.proposal.model_dump(mode="json") if r.proposal else None,
                        "wall_s": round(r.wall_s, 3), "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens})
        print(s.ticket.ticket_id, s.scenario_type, "->", r.parsed and r.parsed.get("passage_id"), r.proposal and (r.proposal.status, r.proposal.action, r.proposal.urgency.value), r.error or "", r.violations or "", f"{r.wall_s:.1f}s", flush=True)
    walls = sorted(x["wall_s"] for x in records)
    summary = {
        "split": f"scenarios-v1 {a.split}", "output_kind": "real local inference (not fixtures)",
        "model": {"repo": MODEL_REPO, "revision": MODEL_REVISION, "file": MODEL_FILE, "sha256": MODEL_SHA256, "licence": "apache-2.0",
                  "runtime": "llama-cpp-python 0.3.36 (llama.cpp), CPU, temperature 0, seed 0, JSON-schema constrained output",
                  "prompt_version": PROMPT_VERSION, "retriever": "bm25 top-3, superseded passages not indexed"},
        "hardware": {"cpu_count": os.cpu_count(), "threads": a.threads, "machine": platform.machine(), "python": platform.python_version()},
    }
    summary["model"] = {"repo": MODEL_REPO, "revision": MODEL_REVISION, "file": MODEL_FILE, "sha256": MODEL_SHA256, "licence": "apache-2.0",
                        "runtime": "llama-cpp-python 0.3.36 (llama.cpp), CPU, temperature 0, seed 0, JSON-schema constrained output",
                        "prompt_version": PROMPT_VERSION, "retriever": "bm25 top-3, superseded passages not indexed"}
    summary.update({
        "model_proposals": score(mrows, catalog), "rules_baseline_same_tickets": score(brows, catalog),
        "latency_s_per_ticket": {"median": walls[len(walls) // 2], "max": walls[-1]}, "model_load_s": round(load_s, 2),
        "total_wall_s": round(time.perf_counter() - wall0, 1), "total_process_cpu_s": round(time.process_time() - cpu0, 1),
        "tokens": {"prompt": sum(x["prompt_tokens"] for x in records), "completion": sum(x["completion_tokens"] for x in records)},
    })
    Path(a.out_prefix + "_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    Path(a.out_prefix + "_outputs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
