"""P1.06: one-time evaluation on the frozen held-out set scenarios-v2.

Runs each system once: the rules baseline, the local model (prompt v5, BM25 top-3), retrieval only
(keyword and BM25), and the desk safety check with both systems' proposals. Before running it checks that the
held-out file and every frozen component match the hashes recorded in data/FROZEN_P1_06.json, and it
refuses to run again if results already exist (no second look, no tuning).
"""
import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from opsdesk.baseline import RulesBaseline  # noqa: E402
from opsdesk.evaluate import macro_f1  # noqa: E402
from opsdesk.evaluate_model import score  # noqa: E402
from opsdesk.retrieval import ticket_text  # noqa: E402
from opsdesk.schemas import DATA_DIR, Scenario, check_proposal, load_catalog  # noqa: E402

HELDOUT = DATA_DIR / "tickets" / "scenarios_v2_heldout.jsonl"
FROZEN = DATA_DIR / "FROZEN_P1_06.json"
OUT = ROOT / "results" / "p1_06"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_frozen(model_path: Path) -> dict:
    f = json.loads(FROZEN.read_text())
    bad = {k: (v, sha(ROOT / k)) for k, v in f["files"].items() if sha(ROOT / k) != v}
    if bad:
        raise SystemExit(f"frozen files changed: {bad}")
    if sha(model_path) != f["model_sha256"]:
        raise SystemExit("model file hash differs")
    return f


def per_category_f1(rows):
    _, f1 = macro_f1([((s.expected_category and s.expected_category.value), p and p.category and p.category.value) for s, p, _, _ in rows])
    return {k: round(v, 4) for k, v in f1.items()}


def failures(rows, records=None):
    out = []
    for i, (s, p, v, e) in enumerate(rows):
        exp = (s.expected_status, s.expected_action, tuple(s.expected_passages))
        got = None if p is None else (p.status, p.action, tuple(c.passage_id for c in p.citations))
        if got != exp:
            item = {"ticket": s.ticket.ticket_id, "family": s.family, "type": s.scenario_type, "subject": s.ticket.subject,
                    "body": s.ticket.body, "expected": list(exp), "got": got and list(got), "check_problems": v, "error": e}
            if records:
                item["retrieved"] = records[i]["retrieved"]
                item["model_raw"] = records[i]["raw_model_output"]
            out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", default=os.environ.get("OPSDESK_MODEL_PATH", "models/qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    a = ap.parse_args()
    if OUT.exists():
        raise SystemExit(f"{OUT} exists: the held-out set has already been evaluated once; refusing to run again")
    frozen = verify_frozen(ROOT / a.model_path)
    scen = [Scenario.model_validate_json(l) for l in HELDOUT.read_text().splitlines()]
    catalog = load_catalog()
    OUT.mkdir(parents=True)
    t_start = time.perf_counter()

    # 1) rules baseline
    bl = RulesBaseline()
    rrows, rlat = [], []
    for s in scen:
        t0 = time.perf_counter()
        p, _ = bl.propose(s.ticket)
        rlat.append(time.perf_counter() - t0)
        rrows.append((s, p, check_proposal(p, bl.catalog, bl.passages), None))

    # 2) retrieval only
    from opsdesk.retrieval import BM25Retriever, KeywordRetriever
    retr = {}
    sup = [s for s in scen if s.expected_status == "proposed"]
    for r in (KeywordRetriever(), BM25Retriever()):
        ids = [[h.passage_id for h in r.search(ticket_text(s.ticket), 3)] for s in sup]
        retr[r.name] = {"hit_at_1": {"n": sum(i[0] == s.expected_passages[0] for i, s in zip(ids, sup)), "d": len(sup)},
                        "recall_at_3": {"n": sum(s.expected_passages[0] in i for i, s in zip(ids, sup)), "d": len(sup)}}

    # 3) local model, prompt v5
    from opsdesk.model_proposer import PROMPT_VERSION, LocalModelProposer
    assert PROMPT_VERSION.startswith("p1.03-v5"), PROMPT_VERSION
    cpu0, w0 = time.process_time(), time.perf_counter()
    mp = LocalModelProposer(ROOT / a.model_path, n_threads=8)
    mrows, records = [], []
    for s in scen:
        res = mp.propose(s.ticket)
        mrows.append((s, res.proposal, res.violations, res.error))
        records.append({"ticket_id": s.ticket.ticket_id, "family": s.family, "type": s.scenario_type, "retrieved": res.retrieved,
                        "raw_model_output": res.raw, "error": res.error, "violations": res.violations,
                        "proposal": res.proposal.model_dump(mode="json") if res.proposal else None,
                        "wall_s": round(res.wall_s, 3), "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens})
        print(s.ticket.ticket_id, s.scenario_type, res.parsed and res.parsed.get("passage_id"), res.violations or "", flush=True)
    model_wall, model_cpu = time.perf_counter() - w0, time.process_time() - cpu0
    (OUT / "model_outputs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (OUT / "rules_outputs.jsonl").write_text("".join(json.dumps({"ticket_id": s.ticket.ticket_id, "proposal": p.model_dump(mode="json"),
                                                                    "problems": v}) + "\n" for s, p, v, _ in rrows))

    # 4) desk safety check with both systems' proposals
    import importlib.util
    spec = importlib.util.spec_from_file_location("bc", ROOT / "scripts" / "behaviour_check.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    perms = json.loads((DATA_DIR / "permissions_v1.json").read_text())
    desk_rules = bc.run("p1_06_rules_v2", [(s, p) for s, p, _, _ in rrows], perms)
    desk_model = bc.run("p1_06_model_v2", [(s, p) for s, p, _, _ in mrows], perms)
    for n in ("p1_06_rules_v2", "p1_06_model_v2"):
        src = ROOT / "results" / f"p1_04_audit_{n}.jsonl"
        src.rename(OUT / f"audit_{n}.jsonl")

    wl = sorted(r["wall_s"] for r in records)
    rl = sorted(rlat)
    summary = {
        "dataset": "scenarios-v2 (held-out)", "dataset_sha256": sha(HELDOUT), "n": len(scen), "n_families": len({s.family for s in scen}),
        "by_type_counts": {t: sum(s.scenario_type == t for s in scen) for t in sorted({s.scenario_type for s in scen})},
        "frozen": frozen, "run_once": True, "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rules_baseline": {**score(rrows, catalog), "category_f1": per_category_f1(rrows),
                           "latency_ms": {"median": round(rl[len(rl) // 2] * 1000, 3), "max": round(rl[-1] * 1000, 3)}},
        "local_model_v5": {**score(mrows, catalog), "category_f1": per_category_f1(mrows),
                           "latency_s": {"median": wl[len(wl) // 2], "max": wl[-1]},
                           "wall_s": round(model_wall, 1), "process_cpu_s": round(model_cpu, 1),
                           "tokens": {"prompt": sum(r["prompt_tokens"] for r in records), "completion": sum(r["completion_tokens"] for r in records)}},
        "retrieval_only": retr,
        "desk_safety": {"rules_baseline": desk_rules, "local_model_v5": desk_model,
                        "note": "scripted reviewer approves only proposals whose action and citation match the label; checks the gate, not reviewer skill"},
        "hardware": {"cpu_count": os.cpu_count(), "machine": platform.machine(), "python": platform.python_version()},
        "total_wall_s": round(time.perf_counter() - t_start, 1),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (OUT / "failures_rules.json").write_text(json.dumps(failures(rrows), indent=2) + "\n")
    (OUT / "failures_model.json").write_text(json.dumps(failures(mrows, records), indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "frozen"}, indent=2, default=str))


if __name__ == "__main__":
    main()
