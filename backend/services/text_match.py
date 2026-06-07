"""Lightweight, dependency-free lexical retrieval for the KO→past-paper blend.

Stage 1 of the hybrid matcher: instead of asking the model to scan the entire
past-paper corpus for every knowledge-organiser point (expensive, slow, and prone
to inventing IDs), we first shortlist the handful of lexically-closest exam
questions per KO point using BM25. The model then only judges that shortlist.

Pure-Python BM25 (Okapi) — no numpy/scikit dependency, keeping the project's
no-build, lean-image ethos. Corpora here are small (hundreds–low thousands of
questions), so plain Python is more than fast enough.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Common English + exam-instruction words carry no topic signal, so they only add
# noise to lexical scoring. Kept deliberately small and high-precision.
_STOPWORDS = frozenset(
    """a an the of to in on for and or is are was were be been being am
    with as at by from this that these those it its which what how why when where who whom
    do does did can could would should will shall may might must
    state give name describe explain define list outline identify suggest calculate work out
    show using use one two three four five your you their them they then than into out about
    not no but if so such each per any all both more most some many much
    question marks mark answer answers""".split()
)


def tokenize(text: str | None) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop 1-char tokens and stopwords."""
    return [
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) > 1 and t not in _STOPWORDS
    ]


class BM25:
    """Okapi BM25 over a fixed corpus of pre-tokenised documents."""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.N = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        self.tf = [Counter(d) for d in corpus_tokens]
        df: Counter = Counter()
        for toks in corpus_tokens:
            for t in set(toks):
                df[t] += 1
        # Okapi IDF with the +1 smoothing that keeps it non-negative.
        self.idf = {
            t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()
        }

    def score(self, query_tokens: list[str], idx: int) -> float:
        tf = self.tf[idx]
        dl = self.doc_len[idx]
        norm = self.k1 * (1 - self.b + self.b * (dl / self.avgdl if self.avgdl else 0.0))
        s = 0.0
        for t in query_tokens:
            f = tf.get(t, 0)
            if not f:
                continue
            s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / (f + norm)
        return s

    def top_k(self, query_tokens: list[str], k: int) -> list[tuple[int, float]]:
        """Return up to k (doc_index, score) pairs with score > 0, best first."""
        scored = [
            (i, self.score(query_tokens, i)) for i in range(self.N)
        ]
        scored = [pair for pair in scored if pair[1] > 0.0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


def _doc_text(q: dict) -> str:
    return f"{q.get('question_text') or ''} {q.get('answer_text') or ''}"


def bm25_shortlist(
    ko_questions: list[dict], pp_questions: list[dict], top_k: int = 12
) -> dict[int, list[dict]]:
    """Map each KO question id → its top_k most lexically-relevant past-paper dicts.

    Both inputs are dicts with at least 'id' and 'question_text' (optionally
    'answer_text'). A KO point with no lexical overlap maps to an empty list.
    """
    if not pp_questions:
        return {ko["id"]: [] for ko in ko_questions}

    corpus = [tokenize(_doc_text(q)) for q in pp_questions]
    bm = BM25(corpus)
    out: dict[int, list[dict]] = {}
    for ko in ko_questions:
        q_tokens = tokenize(_doc_text(ko))
        ranked = bm.top_k(q_tokens, top_k) if q_tokens else []
        out[ko["id"]] = [pp_questions[i] for i, _ in ranked]
    return out
