"""Turns text into vectors with a small local model (no API key, runs on the CPU)."""

import os
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed import TextEmbedding

DIMENSIONS = 384


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """BAAI/bge-small-en-v1.5 through fastembed. The model loads on first use, not at startup."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self._model_name = model_name
        self._cache_dir = os.path.expanduser(cache_dir) if cache_dir else None
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    def _load(self) -> "TextEmbedding":
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self._model_name, cache_dir=self._cache_dir)
            return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._load().embed(list(texts), batch_size=8)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._load().query_embed(text)))]
