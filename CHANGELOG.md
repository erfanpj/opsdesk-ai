# Changelog

## 0.1.0 (not yet published)

First public version. A local demo with fictional data only.

### Added
- Records for tickets, handbook passages, citations, proposals, human approvals and mock executions, with validation (`opsdesk/schemas.py`).
- A fictional handbook (17 passages) and 87 labelled synthetic tickets in a dev split and a test split, grouped by family.
- A deterministic rules baseline and its evaluation script.
- A retrieval comparison: keyword, BM25, a small dense model (bge-small) and a hybrid.
- Structured proposals from a small open-weights model (Qwen2.5-1.5B-Instruct, Q4_K_M) running locally on the CPU. The model
  picks a handbook passage or says the ticket is not covered or too vague, and code fills in the action from the handbook.
- A review desk: reviewer roles, explicit human approval, checks that an action stays within the ticket's own customer, mock actions that
  run at most once, and a hash-chained audit log in SQLite (`opsdesk/desk.py`).
- A local review screen and JSON API (FastAPI), and an MCP server, both going through the same desk checks.
- A held-out set of 60 tickets in 25 new families, hashed before the model work and evaluated once.
- `scripts/download_model.py` (downloads the model at a fixed revision and checks its sha256) and `scripts/record_demo.py` (`docs/demo.gif`).

### Results (details in the README)
- Held-out, every field correct: rules baseline 37/60, local model 25/60. On the earlier test split the figures were 56/58 and 31/58.
- No unapproved or repeated mock action ran in the scripted checks, including on the injection tickets.

### Known limits
- All tickets and the handbook are synthetic and small, and they were written for this project.
- The rules baseline relies on handbook keywords and drops sharply on newly worded tickets.
- The local model proposed 20 wrong write actions on the held-out set; in the scripted review check all 20 were rejected before anything ran, but only 11 were flagged automatically.
- The review screen has no login. It is meant to run on 127.0.0.1 only.
