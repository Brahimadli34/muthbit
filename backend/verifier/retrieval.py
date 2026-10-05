"""فهرس البحث: مطابقة لفظية (مقاطع حروف) مع مطابقة دلالية اختيارية.

- المطابقة اللفظية (TF-IDF على مقاطع الحروف + نسبة الاحتواء) خفيفة وتعمل
  على الاستضافة المجانية، وتلتقط اختلاف الإملاء والتشكيل والأجزاء المقتطعة.
- المطابقة الدلالية (تضمينات متعددة اللغات) تلتقط الرواية بالمعنى،
  وتُفعّل بـ MUTHBIT_USE_EMBEDDINGS=1.
"""
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .arabic import phrase_containment

INDEX_FILE = "lexical.joblib"
EMB_FILE = "embeddings.npy"
MIN_CONTAINMENT_CHARS = 20  # الاستعلامات الأقصر لا يُعتمد احتواؤها كاملاً


@dataclass
class Candidate:
    text_id: int
    score: float
    lexical: float
    semantic: float | None


def lexical_score(query_norm: str, doc_norm: str, cosine: float) -> float:
    """الأعلى بين التشابه الجيبي واحتواء العبارات.

    الاحتواء يلتقط الجزء المقتطع من حديث طويل، ويُقاس بأزواج الكلمات المتتالية
    لا بمقاطع الحروف، حتى لا تكفي الكلمات الشائعة وحدها لمطابقة نص غير ذي صلة.
    """
    contain = phrase_containment(query_norm, doc_norm)
    contain *= min(1.0, len(query_norm) / MIN_CONTAINMENT_CHARS)
    return max(cosine, contain)


class _Embedder:
    _model = None

    @classmethod
    def get(cls, name):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer  # اعتماد اختياري
            cls._model = SentenceTransformer(name)
        return cls._model

    @classmethod
    def encode(cls, name, texts, is_query):
        prefix = "query: " if is_query else "passage: "  # صيغة نماذج e5
        vecs = cls.get(name).encode([prefix + t for t in texts], normalize_embeddings=True,
                                    batch_size=32, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class SearchIndex:
    def __init__(self, ids, norms, vectorizer, matrix, embeddings=None):
        self.ids = ids
        self.norms = norms
        self.vectorizer = vectorizer
        self.matrix = matrix
        self.embeddings = embeddings

    # ---------- البناء والحفظ ----------
    @classmethod
    def build(cls, ids, norms, embedding_model=None):
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=1)
        matrix = vec.fit_transform(norms)
        emb = _Embedder.encode(embedding_model, norms, is_query=False) if embedding_model else None
        return cls(list(ids), list(norms), vec, matrix, emb)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump({"ids": self.ids, "norms": self.norms, "vectorizer": self.vectorizer,
                     "matrix": self.matrix}, directory / INDEX_FILE)
        emb_path = directory / EMB_FILE
        if self.embeddings is not None:
            np.save(emb_path, self.embeddings)
        elif emb_path.exists():
            emb_path.unlink()

    @classmethod
    def load(cls, directory: Path):
        data = joblib.load(directory / INDEX_FILE)
        emb_path = directory / EMB_FILE
        emb = np.load(emb_path) if emb_path.exists() else None
        return cls(data["ids"], data["norms"], data["vectorizer"], data["matrix"], emb)

    # ---------- البحث ----------
    def search(self, query_norm: str, k: int = 5, lexical_weight: float = 0.6,
               embedding_model: str | None = None, prefilter: int = 50) -> list[Candidate]:
        if not query_norm or not self.ids:
            return []
        cos = linear_kernel(self.vectorizer.transform([query_norm]), self.matrix).ravel()
        pool = set(np.argsort(-cos)[:prefilter].tolist())

        sem = None
        if embedding_model and self.embeddings is not None:
            q = _Embedder.encode(embedding_model, [query_norm], is_query=True)[0]
            sem = self.embeddings @ q
            pool |= set(np.argsort(-sem)[:prefilter].tolist())

        out = []
        for i in pool:
            lex = lexical_score(query_norm, self.norms[i], float(cos[i]))
            s = None
            score = lex
            if sem is not None:
                s = float(sem[i])
                score = max(lex, lexical_weight * lex + (1 - lexical_weight) * s)
            out.append(Candidate(self.ids[i], round(score, 4), round(lex, 4),
                                 None if s is None else round(s, 4)))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:k]


_lock = threading.Lock()
_cache = {"index": None, "mtime": None}


def get_index(directory: Path) -> SearchIndex | None:
    """يحمّل الفهرس مرة واحدة، ويعيد تحميله إذا أُعيد بناؤه."""
    path = directory / INDEX_FILE
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    with _lock:
        if _cache["index"] is None or _cache["mtime"] != mtime:
            _cache["index"] = SearchIndex.load(directory)
            _cache["mtime"] = mtime
        return _cache["index"]
