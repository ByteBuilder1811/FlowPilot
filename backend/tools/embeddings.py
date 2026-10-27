"""Embedding modes for knowledge bases.
  lexical : BM25 only (default).
  ngram   : BM25 + character-trigram vectors fused with reciprocal-rank-fusion. Typo/morphology tolerant FUZZY matching — NOT neural semantic search.
  remote  : BM25 + vectors from an OpenAI-compatible /v1/embeddings endpoint (EMBEDDINGS_URL, EMBEDDINGS_MODEL, EMBEDDINGS_API_KEY)."""
import hashlib
import math
import os
import re

from backend.tools.base import CallInfo, ToolError

DIM = 384
MODES = ("lexical", "ngram", "remote")


def _ngram_vec(text: str) -> list[float]:
    t = " " + re.sub(r"\s+", " ", text.lower()) + " "
    v = [0.0] * DIM
    for i in range(len(t) - 2):
        g = t[i:i + 3]
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=4).digest(), "big")
        v[h % DIM] += 1.0 if (h >> 31) & 1 else -1.0  # signed hashing trick
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _remote(texts: list[str]) -> list[list[float]]:
    from backend.security import vault
    from backend.tools.builtin import http_request
    url = os.environ.get("EMBEDDINGS_URL")
    if not url:
        raise ToolError("not_configured", "Set EMBEDDINGS_URL (an OpenAI-compatible /v1/embeddings endpoint) to use remote embeddings", False, False)
    headers = {"Content-Type": "application/json"}
    key = vault.get("EMBEDDINGS_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    out: list[list[float]] = []
    for i in range(0, len(texts), 32):
        r = http_request(CallInfo("emb", "emb", 0, 1, None, None, lambda: False), {"url": url, "method": "POST", "headers": headers, "timeout_s": 30,
                                                                                    "body": {"input": texts[i:i + 32], "model": os.environ.get("EMBEDDINGS_MODEL", "text-embedding-3-small")}})
        data = (r.get("json") or {}).get("data")
        if not isinstance(data, list) or len(data) != len(texts[i:i + 32]):
            raise ToolError("malformed_response", "embedding endpoint returned an unexpected shape", False, None)
        out += [d["embedding"] for d in data]
    return out


def embed(texts: list[str], mode: str) -> list[list[float]] | None:
    if mode == "ngram":
        return [_ngram_vec(t) for t in texts]
    if mode == "remote":
        return _remote(texts)
    return None


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))
