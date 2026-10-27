"""Local knowledge base: chunking + BM25 ranking (no embeddings, no network). Content is user data, treated as untrusted by agents."""
import math
import re
from collections import Counter

from backend.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDoc
from backend.db.session import new_id, now, session
from backend.tools import embeddings
from backend.tools.base import CallInfo, ToolError

MAX_DOC_CHARS = 300_000
CHUNK = 900
OVERLAP = 120
_TOK = re.compile(r"[a-z0-9]+")


def tokens(s: str) -> list[str]:
    return [t for t in _TOK.findall(s.lower()) if len(t) > 1]


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    paras, chunks, cur = re.split(r"\n\s*\n", text), [], ""
    for p in paras:
        while len(p) > CHUNK:
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(p[:CHUNK]); p = p[CHUNK - OVERLAP:]
        if len(cur) + len(p) + 2 > CHUNK and cur:
            chunks.append(cur); cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def add_document(kb_id: str, title: str, text: str, source: str = "") -> dict:
    if len(text) > MAX_DOC_CHARS:
        raise ValueError(f"document exceeds {MAX_DOC_CHARS} characters")
    parts = chunk_text(text)
    if not parts:
        raise ValueError("document is empty")
    did = new_id("doc")
    with session() as s:
        base = s.get(KnowledgeBase, kb_id)
        if not base:
            raise KeyError(kb_id)
        mode = base.embedding
    vecs = embeddings.embed(parts, mode) or [None] * len(parts)  # may raise ToolError (remote misconfigured) before anything is stored
    with session() as s:
        s.add(KnowledgeDoc(id=did, kb_id=kb_id, title=title[:300], source=source[:500], created_at=now()))
        s.flush()
        for i, (p, v) in enumerate(zip(parts, vecs)):
            s.add(KnowledgeChunk(kb_id=kb_id, doc_id=did, idx=i, text=p, vec=v))
    return {"id": did, "chunks": len(parts), "embedding": mode}


def reindex(kb_id: str) -> dict:
    with session() as s:
        base = s.get(KnowledgeBase, kb_id)
        if not base:
            raise KeyError(kb_id)
        mode = base.embedding
        rows = [(c.id, c.text) for c in s.query(KnowledgeChunk).filter_by(kb_id=kb_id).all()]
    vecs = embeddings.embed([t for _, t in rows], mode) or [None] * len(rows)
    with session() as s:
        for (cid, _), v in zip(rows, vecs):
            s.query(KnowledgeChunk).filter_by(id=cid).update({"vec": v})
    return {"chunks": len(rows), "embedding": mode}


def search(kb: str, query: str, limit: int = 4) -> list[dict]:
    with session() as s:
        base = s.get(KnowledgeBase, kb) or s.query(KnowledgeBase).filter_by(name=kb).first()
        if not base:
            raise ToolError("not_found", f"Knowledge base '{kb}' does not exist", transient=False, executed=False)
        mode = base.embedding
        rows = s.query(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id).filter(KnowledgeChunk.kb_id == base.id).all()
        docs = [(c.text, d.title, d.id, c.idx, c.vec) for c, d in rows]
    q = tokens(query)
    if not docs or not q:
        return []
    toks = [tokens(t[0]) for t in docs]
    n, avg = len(docs), (sum(len(t) for t in toks) / len(docs)) or 1
    df = Counter(w for t in toks for w in set(t))
    scored = []
    for (text, title, did, idx, vec), tk in zip(docs, toks):
        tf, bm = Counter(tk), 0.0
        for w in set(q):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                bm += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(tk) / avg))
        scored.append({"bm25": bm, "cos": 0.0, "title": title, "doc_id": did, "chunk": idx, "text": text, "vec": vec})
    if mode != "lexical":
        qv = (embeddings.embed([query], mode) or [None])[0]
        if qv:
            for r in scored:
                r["cos"] = embeddings.cosine(qv, r["vec"]) if r["vec"] else 0.0
    if mode == "lexical":
        for r in scored:
            r["score"] = r["bm25"]
        hits = [r for r in scored if r["score"] > 0]
    else:  # reciprocal rank fusion of BM25 and vector similarity
        rank_b = {id(r): i for i, r in enumerate(sorted(scored, key=lambda r: -r["bm25"]))}
        rank_c = {id(r): i for i, r in enumerate(sorted(scored, key=lambda r: -r["cos"]))}
        for r in scored:
            r["score"] = (1 / (60 + rank_b[id(r)]) if r["bm25"] > 0 else 0) + (1 / (60 + rank_c[id(r)]) if r["cos"] > 0.05 else 0)
        hits = [r for r in scored if r["score"] > 0]
    hits.sort(key=lambda r: -r["score"])
    return [{"score": round(r["score"], 4), "title": r["title"], "doc_id": r["doc_id"], "chunk": r["chunk"], "text": r["text"], "mode": mode} for r in hits[:limit]]


def tool_search(info: CallInfo, args: dict):
    hits = search(str(args["kb"]), str(args["query"]), max(1, min(int(args.get("limit", 4)), 10)))
    return {"kb": args["kb"], "query": args["query"], "results": [{"title": h["title"], "snippet": h["text"][:600], "url": f"knowledge://{h['doc_id']}#{h['chunk']}", "score": h["score"]} for h in hits]}
