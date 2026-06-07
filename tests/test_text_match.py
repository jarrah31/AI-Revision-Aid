"""Tests for the pure-Python BM25 lexical shortlister."""
from backend.services.text_match import BM25, bm25_shortlist, tokenize


def test_tokenize_drops_stopwords_and_short_tokens():
    toks = tokenize("Describe the function of the mitochondria in a cell")
    assert "mitochondria" in toks
    assert "cell" in toks
    assert "function" in toks
    # stopwords / instruction verbs dropped
    assert "the" not in toks
    assert "describe" not in toks
    assert "in" not in toks
    assert "a" not in toks


def test_bm25_ranks_topically_relevant_doc_highest():
    corpus = [
        tokenize("Explain how osmosis moves water across a membrane"),
        tokenize("State the products of aerobic respiration"),
        tokenize("Describe the structure of a plant cell wall"),
    ]
    bm = BM25(corpus)
    ranked = bm.top_k(tokenize("What is osmosis and how does water move?"), 3)
    assert ranked  # at least one hit
    assert ranked[0][0] == 0  # the osmosis doc ranks first


def test_shortlist_respects_top_k_and_returns_dicts():
    ko = [{"id": 1, "question_text": "Define photosynthesis", "answer_text": "glucose + oxygen"}]
    pp = [
        {"id": 10, "question_text": "Write the word equation for photosynthesis", "answer_text": "..."},
        {"id": 11, "question_text": "What gas is produced by photosynthesis?", "answer_text": "oxygen"},
        {"id": 12, "question_text": "State Newton's second law", "answer_text": "F=ma"},
    ]
    out = bm25_shortlist(ko, pp, top_k=2)
    cand_ids = [c["id"] for c in out[1]]
    assert len(cand_ids) <= 2
    # the two photosynthesis questions should be preferred over the physics one
    assert 12 not in cand_ids
    assert set(cand_ids) <= {10, 11}


def test_shortlist_empty_corpus_is_graceful():
    ko = [{"id": 1, "question_text": "Define photosynthesis"}]
    assert bm25_shortlist(ko, [], top_k=5) == {1: []}


def test_shortlist_no_overlap_returns_empty_for_that_ko():
    ko = [{"id": 1, "question_text": "Define photosynthesis", "answer_text": ""}]
    pp = [{"id": 10, "question_text": "Calculate the momentum of a trolley", "answer_text": "kg m/s"}]
    out = bm25_shortlist(ko, pp, top_k=5)
    assert out[1] == []  # no shared topic words → no candidates
