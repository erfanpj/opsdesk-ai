import pytest

from opsdesk.handbook import load_handbook
from opsdesk.retrieval import resolve_current

rank_bm25 = pytest.importorskip("rank_bm25")
from opsdesk.retrieval import BM25Retriever  # noqa: E402


def test_resolve_current_follows_supersession():
    passages, _ = load_handbook()
    assert resolve_current("HB-BIL-01", passages) == "HB-BIL-03"
    assert resolve_current("HB-BIL-03", passages) == "HB-BIL-03"


def test_superseded_not_indexed_by_default():
    assert "HB-BIL-01" not in BM25Retriever().ids
    assert "HB-BIL-01" in BM25Retriever(include_superseded=True).ids


def test_outdated_query_resolves_to_current_policy():
    passages, _ = load_handbook()
    r = BM25Retriever(include_superseded=True)
    top = r.search("refund within 14 days of delivery for unused items", 1)[0].passage_id
    assert passages[resolve_current(top, passages)].superseded_by is None


def test_bm25_routes_obvious_ticket():
    assert BM25Retriever().search("Please resend my invoice", 1)[0].passage_id == "HB-BIL-02"
