"""Deterministic rules baseline: route, prioritise and cite handbook passages without any model.

Retrieval uses only the handbook's own `keywords:` lines (phrase matches, longer phrases weigh more),
with a small TF-IDF cosine score over passage text as a tie-breaker. Superseded passages are never
retrieved. Urgency follows the priority guide HB-PRI-01, encoded as a table of passage ids.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

from .handbook import PassageMeta, first_sentence, load_handbook
from .schemas import Citation, Proposal, SourcePassage, Ticket, Urgency, load_catalog

# HB-PRI-01 written out as a lookup. Passage ids not listed are "normal".
URGENCY_BY_PASSAGE = {
    "HB-ACC-03": Urgency.critical, "HB-OUT-01": Urgency.critical,   # takeover; outage stopping orders
    "HB-PRV-01": Urgency.high, "HB-ACC-02": Urgency.high,           # privacy; locked-out customer
    "HB-DMG-01": Urgency.high, "HB-DMG-02": Urgency.high,           # damaged on arrival
    "HB-ORD-02": Urgency.high,                                      # lost parcel
    "HB-BIL-02": Urgency.low, "HB-SUB-01": Urgency.low,             # invoice copy; plan change
}
REFUND_LIMIT_PASSAGE = "HB-BIL-04"
STOP = set("a an the i me my we our you your it its is are was were be been am to of in on for and or but "
           "with at by from this that there here can could would should please hi hello do does did not no "
           "have has had so as if just also any some".split())
# Tuned on the dev split only: tickets with fewer content words than this and no keyword hit
# are treated as too vague (ask for clarification) rather than out of scope (escalate).
MIN_CONTENT_WORDS_FOR_UNSUPPORTED = 6
AMOUNT = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:eur\b|€)", re.I)
PLAN = re.compile(r"\b(basic|plus|pro)\b", re.I)
PLAN_TARGET = re.compile(r"\bto (?:the )?(basic|plus|pro)\b", re.I)


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9€]+", " ", text.lower()) + " "


def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if t not in STOP and len(t) > 1]


@dataclass
class Retrieval:
    passage_id: str
    keyword_score: float
    tfidf: float


class RulesBaseline:
    proposer = "rules_baseline"

    def __init__(self):
        self.passages, self.meta = load_handbook()
        self.catalog = load_catalog()
        self.index = {pid: p for pid, p in self.passages.items() if p.category is not None and not p.superseded_by}
        docs = {pid: _tokens(p.title + " " + p.text + " " + " ".join(p.keywords)) for pid, p in self.index.items()}
        df = Counter(t for toks in docs.values() for t in set(toks))
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = {pid: self._vec(toks) for pid, toks in docs.items()}

    def _vec(self, toks):
        tf = Counter(toks)
        v = {t: c * self.idf.get(t, 0.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def retrieve(self, text: str, k: int = 3) -> list[Retrieval]:
        norm, q = _norm(text), self._vec(_tokens(text))
        out = []
        for pid, p in self.index.items():
            ks = sum(len(kw.split()) for kw in p.keywords if f" {_norm(kw).strip()} " in norm)
            cos = sum(w * self.vecs[pid].get(t, 0.0) for t, w in q.items())
            out.append(Retrieval(pid, float(ks), cos))
        out.sort(key=lambda r: (-r.keyword_score, -r.tfidf, r.passage_id))
        return out[:k]

    def _cite(self, pid: str) -> Citation:
        p = self.passages[pid]
        return Citation(passage_id=pid, doc_version=p.doc_version, quote=first_sentence(p.text))

    def propose(self, t: Ticket) -> tuple[Proposal, list[Retrieval]]:
        text = f"{t.subject}. {t.body}"
        ranked = self.retrieve(text)
        base = dict(proposal_id="P-" + hashlib.sha256(t.ticket_id.encode()).hexdigest()[:12], ticket_id=t.ticket_id,
                    proposer=self.proposer, catalog_version=self.catalog["catalog_version"], created_at=t.created_at)

        def fallback(status, reason):
            action = "request_clarification" if status == "needs_clarification" else "escalate_to_human"
            return Proposal(**base, status=status, category=None, urgency=Urgency.normal, action=action,
                            rationale=reason), ranked

        if not ranked or ranked[0].keyword_score == 0:
            if len(_tokens(text)) < MIN_CONTENT_WORDS_FOR_UNSUPPORTED:
                return fallback("needs_clarification", "No handbook keyword matched and the ticket is too vague.")
            return fallback("unsupported", "No handbook passage covers this request; a person must decide.")

        pid = ranked[0].passage_id
        params: dict[str, str | float] = {}
        action = self.meta[pid].action
        amount = AMOUNT.search(text)
        if action == "issue_refund" and amount:
            value = float(amount.group(1).replace(",", "."))
            if value > self.catalog["actions"]["issue_refund"]["limits"]["amount_eur_max"]:
                pid, action = REFUND_LIMIT_PASSAGE, self.meta[REFUND_LIMIT_PASSAGE].action
            params["amount_eur"] = value
        p = self.passages[pid]
        spec = self.catalog["actions"][action]
        if t.customer_id:
            params["customer_id"] = t.customer_id
        if t.order_id:
            params["order_id"] = t.order_id
        if action == "change_plan":
            m = PLAN_TARGET.search(text) or (PLAN.search(text) if len(set(x.lower() for x in PLAN.findall(text))) == 1 else None)
            if m:
                params["new_plan"] = m.group(1).capitalize()
        if action == "open_privacy_request":
            params["request_type"] = "delete" if re.search(r"delet|erasure|forgotten|remove", text, re.I) else "export"
        if action == "link_to_incident" and self.meta[pid].incident_id:
            params["incident_id"] = self.meta[pid].incident_id
        missing = [n for n in spec["required_params"] if n not in params]
        if missing:
            return fallback("needs_clarification", f"Matched {pid} but the ticket lacks: {', '.join(missing)}.")
        params = {k: v for k, v in params.items() if k in spec["required_params"]}
        return Proposal(**base, status="proposed", category=p.category, urgency=URGENCY_BY_PASSAGE.get(pid, Urgency.normal),
                        action=action, params=params, citations=(self._cite(pid),),
                        rationale=f"Matched handbook passage {pid} ({p.title})."), ranked
