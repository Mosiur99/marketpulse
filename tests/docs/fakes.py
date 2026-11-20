import hashlib
import math
import re
from collections.abc import Sequence

from marketpulse.docs.embedder import DIMENSIONS

WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Offline stand-in for the real model: a normalised bag of hashed words."""

    def __init__(self) -> None:
        self.documents_embedded = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in WORD.findall(text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.documents_embedded += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)
