# OpsDesk AI (work in progress)

A small operations-desk demo: an assistant reads a support ticket, finds the relevant procedure in a
handbook, and **proposes** an action. A person approves or rejects the proposal, and only an approved proposal
can run, against mock actions. Nothing here touches a real system.

**All data in this repository is fictional.** "Lumen Lane Cycles", its handbook, customers, orders and
tickets were written for this demo. Any resemblance to a real company or person is accidental.

## Status

| Step | What exists | State |
|---|---|---|
| Schemas (P1.01) | Ticket, handbook passage, citation, proposal, human approval and mock execution records, with validation and a lifecycle table (`opsdesk/schemas.py`) | done, 26 tests |
| Data and rules baseline (P1.02) | Fictional handbook (17 passages), 87 labelled synthetic tickets, deterministic rules baseline, evaluation script | done, 10 more tests |
| Model-based proposals | not started | – |

No language model is used anywhere yet.

## Data

- `data/handbook/` – fictional procedures, one Markdown file per area. Each passage has an id
  (`HB-BIL-03`), a version, keywords and the permitted action. `HB-BIL-01` is an outdated refund rule
  kept on purpose, marked as superseded by `HB-BIL-03`.
- `data/actions_v1.json` – the versioned action catalogue: which mock actions are allowed for which ticket category,
  their required parameters, and limits (refunds up to 200 EUR).
- `data/tickets/scenarios_v1.jsonl` – 87 synthetic tickets in 37 families, each labelled with the expected
  status, category, urgency, handbook passage and action. A family is one situation phrased a few ways; whole
  families go to either the dev split (15 families, 29 tickets) or the test split (22 families, 58 tickets).
  Ticket types: ordinary (61), out of scope (8), too vague (5), outdated policy quoted by the customer (3),
  instructions injected into the ticket text (6), repeated requests (4). The file's sha256 is in `SHA256SUMS`.

## Rules baseline

`opsdesk/baseline.py` is deterministic and uses no model:

1. Match the handbook's own keyword phrases in the ticket (longer phrases weigh more); TF-IDF similarity breaks ties.
   Superseded passages are never retrieved.
2. No keyword match: a ticket with fewer than 6 content words gets "needs clarification", a longer one
   "unsupported" (escalate to a person). The threshold of 6 was chosen on the dev split.
3. Urgency comes from the priority guide `HB-PRI-01`, written as a lookup table.
4. The action comes from the matched passage. Refund requests above 200 EUR switch to the large-refund
   passage and escalate, whatever the ticket says about prior approval.
5. Required parameters (order, customer, amount, plan, request type, incident) are filled from the ticket fields
   or simple patterns. If one is missing, the baseline asks for clarification.

Every proposal is checked with `check_proposal` (permitted action, parameters, limits, citation exists,
version matches, quote is verbatim, not superseded).

## Results

The protocol was: write and hash the scenarios, write the baseline, tune the single threshold on dev,
commit the baseline, then run the test split once. Full output: `results/metrics_dev.json`, `results/metrics_test.json`.

| Metric (test split, 58 tickets) | Result |
|---|---|
| Status (proposed / unsupported / clarification) correct | 56/58 |
| Category correct (8 categories plus "none") | 58/58 |
| Urgency correct | 58/58 |
| Top retrieved passage correct, supported tickets | 48/49 |
| Expected passage in top 3, supported tickets | 49/49 |
| Cited passage correct, supported tickets | 49/49 |
| Action correct | 56/58 |
| Every field correct | 56/58 |
| `check_proposal` problems, superseded citations | 0, 0 |
| Median time per ticket | under 1 ms |

The two errors are both out-of-scope questions ("Can you ship an e-bike to Japan?", "Are you hiring bike
mechanics?"). They are short, so the baseline asked for clarification instead of escalating. On dev every field
was correct for 29/29, but the threshold was tuned there.

**Read these numbers with care.** The same author wrote the handbook keywords, the tickets and the rules, so
the tickets probably use the handbook's wording more often than real customers would. The test set is small, and
the families are hand-written variations, not a sample of real traffic. These results show that the baseline works
as a reference point for later comparisons. They do not show how it would do on real tickets.

## Running it

```bash
uv venv --python 3.13 .venv && . .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest -q tests
python scripts/build_scenarios.py        # rebuilds the scenario file; the hash must match SHA256SUMS
python -m opsdesk.evaluate --split test
```

## AI assistance

The code, handbook and tickets were drafted with an AI coding assistant (Grok Bot, working as a portfolio agent).
<!-- REVIEW: Erfan to confirm or reword this section before publishing. -->

## Licence

MIT, see `LICENSE`. The fictional data is covered by the same licence.
