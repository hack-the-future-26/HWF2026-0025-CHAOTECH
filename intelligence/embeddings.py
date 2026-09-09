"""
P3 Step 1 -- sentence embeddings.

Turns each citizen_request.raw_text into a vector so that reports meaning the
same thing land near each other regardless of the language they were written
in. Verified on the build plan's own sanity check: "सड़क बहुत खराब है" and
"the road is very bad" score 0.971 cosine similarity, an unrelated water
complaint scores 0.105.

Kept in memory rather than a pgvector column, which the build plan explicitly
allows ("or keep an in-memory array for MVP simplicity -- pgvector only if
time allows"). This database is SQLite, so pgvector is not available anyway.
"""

from __future__ import annotations

import numpy as np

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def get_model():
    """Load the sentence-transformer once and reuse it."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """
    Embed a list of texts into an L2-normalised matrix of shape (n, dim).

    Normalising here means cosine similarity is just a dot product later,
    which keeps the distance matrix in clustering.py cheap and readable.
    """
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)

    vectors = get_model().encode(
        texts,
        batch_size=64,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def cosine_similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity for already-normalised vectors."""
    if len(vectors) == 0:
        return np.zeros((0, 0), dtype=np.float32)
    return np.clip(vectors @ vectors.T, -1.0, 1.0)
