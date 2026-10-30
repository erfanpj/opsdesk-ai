"""Passage retrievers compared in P1.03. All run locally on CPU; none generates text.

- keyword: the P1.02 rules baseline's scoring (handbook keyword phrases, TF-IDF tie-break)
- bm25:    BM25Okapi over passage title + text + keywords (rank_bm25)
- dense:   BAAI/bge-small-en-v1.5 sentence embeddings, cosine similarity (pinned revision)
- hybrid:  reciprocal-rank fusion of bm25 and dense

Superseded passages are not indexed. `resolve_current` follows `superseded_by` links for any
citation that names an outdated passage, so an outdated policy can never be the cited source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .baseline import RulesBaseline, _tokens
from .handbook import load_handbook

DENSE_MODEL = "BAAI/bge-small-en-v1.5"
DENSE_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class Hit:
    passage_id: str
    score: float


def resolve_current(passage_id: str, passages) -> str:
    seen = set()
    while passages[passage_id].superseded_by:
        if passage_id in seen:
            raise ValueError(f"superseded_by cycle at {passage_id}")
        seen.add(passage_id)
        passage_id = passages[passage_id].superseded_by
    return passage_id


def _doc(p):
    return f"{p.title}. {p.text} {' '.join(p.keywords)}"


class KeywordRetriever:
    name = "keyword"

    def __init__(self):
        self.bl = RulesBaseline()

    def search(self, text, k=3):
        return [Hit(r.passage_id, r.keyword_score + r.tfidf) for r in self.bl.retrieve(text, k)]


class BM25Retriever:
    name = "bm25"

    def __init__(self, include_superseded=False):
        from rank_bm25 import BM25Okapi
        passages, _ = load_handbook()
        self.ids = [pid for pid, p in passages.items() if p.category is not None and (include_superseded or not p.superseded_by)]
        self.bm25 = BM25Okapi([_tokens(_doc(passages[i])) for i in self.ids])

    def search(self, text, k=3):
        scores = self.bm25.get_scores(_tokens(text))
        order = sorted(range(len(self.ids)), key=lambda i: (-scores[i], self.ids[i]))[:k]
        return [Hit(self.ids[i], float(scores[i])) for i in order]


class DenseRetriever:
    name = "dense"

    def __init__(self, include_superseded=False):
        from sentence_transformers import SentenceTransformer
        passages, _ = load_handbook()
        self.ids = [pid for pid, p in passages.items() if p.category is not None and (include_superseded or not p.superseded_by)]
        self.model = SentenceTransformer(DENSE_MODEL, revision=DENSE_REVISION, device="cpu")
        self.emb = self.model.encode([f"{passages[i].title}. {passages[i].text}" for i in self.ids], normalize_embeddings=True)

    def search(self, text, k=3):
        q = self.model.encode([BGE_QUERY_PREFIX + text], normalize_embeddings=True)[0]
        sims = self.emb @ q
        order = sorted(range(len(self.ids)), key=lambda i: (-sims[i], self.ids[i]))[:k]
        return [Hit(self.ids[i], float(sims[i])) for i in order]


class HybridRetriever:
    name = "hybrid"

    def __init__(self, bm25=None, dense=None, k_rrf=60):
        self.bm25, self.dense, self.k_rrf = bm25 or BM25Retriever(), dense or DenseRetriever(), k_rrf

    def search(self, text, k=3):
        n = len(self.bm25.ids)
        fused = {}
        for r in (self.bm25.search(text, n), self.dense.search(text, n)):
            for rank, h in enumerate(r):
                fused[h.passage_id] = fused.get(h.passage_id, 0.0) + 1.0 / (self.k_rrf + rank + 1)
        order = sorted(fused, key=lambda p: (-fused[p], p))[:k]
        return [Hit(p, fused[p]) for p in order]


def ticket_text(t) -> str:
    return re.sub(r"\s+", " ", f"{t.subject}. {t.body}").strip()
