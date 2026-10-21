"""
Website knowledge-base store: index and search crawled site pages.

Single source of facts: `prosurfaceworks_knowledge_base/pages/*.md`
(built by scripts/crawl_website.py). No LLM calls, no network.

Search is a lightweight keyword-overlap rank over chunks, good enough
for a small local knowledge base.

2-layer design:
- skills/  = HOW to behave (procedural, via skill_view)
- KB pages = WHAT is true (facts, via data_search)
There is no third Data/ store; legacy Data/*.pdf files are NOT indexed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is",
    "are", "what", "how", "why", "when", "where", "who", "do", "does",
    "did", "can", "could", "would", "should", "it", "its", "this", "that",
    "with", "by", "from", "about", "tell", "me", "give", "show", "please",
    "you", "your", "i", "we",
}


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]{2,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


@dataclass
class DataChunk:
    source: str
    chunk_id: int
    text: str


@dataclass
class DataFileInfo:
    name: str
    chars: int
    chunks: int
    error: str = ""


class DataStore:
    """Load KB pages once, then keyword-search over chunks."""

    def __init__(self, project_dir: Path | str | None = None) -> None:
        base = Path(project_dir) if project_dir else Path.cwd()
        # Website knowledge base (built by scripts/crawl_website.py).
        kb = base / "prosurfaceworks_knowledge_base" / "pages"
        if not kb.is_dir():
            kb_fallback = Path(__file__).resolve().parents[2] / "prosurfaceworks_knowledge_base" / "pages"
            if kb_fallback.is_dir():
                kb = kb_fallback
        self.kb_dir = kb
        self.chunks: List[DataChunk] = []
        self.files: List[DataFileInfo] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> dict:
        self.chunks = []
        self.files = []

        # Website knowledge base: recursive *.md with source_url frontmatter.
        kb_files = 0
        if self.kb_dir.is_dir():
            for path in sorted(self.kb_dir.rglob("*.md")):
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    self.files.append(DataFileInfo(f"KB:{path.name}", 0, 0, str(e)))
                    continue
                source_url = ""
                m = re.search(r"^source_url:\s*(\S+)", raw, re.M)
                if m:
                    source_url = m.group(1).strip()
                text = re.sub(r"\s+", " ", raw).strip()
                if len(text) < 100:
                    continue
                label = f"KB:{path.stem}"
                if source_url:
                    text = f"{text} [source: {source_url}]"
                file_chunks = self._chunk(label, text)
                self.files.append(DataFileInfo(label, len(text), len(file_chunks)))
                self.chunks.extend(file_chunks)
                kb_files += 1

        self._loaded = True
        return {
            "ok": True,
            "kb_dir": str(self.kb_dir),
            "kb_pages": kb_files,
            "files": [f.name for f in self.files],
            "chunks": len(self.chunks),
        }

    @staticmethod
    def _chunk(source: str, text: str) -> List[DataChunk]:
        out: List[DataChunk] = []
        start = 0
        idx = 0
        n = len(text)
        while start < n:
            end = min(start + CHUNK_CHARS, n)
            piece = text[start:end].strip()
            if piece:
                out.append(DataChunk(source=source, chunk_id=idx, text=piece))
                idx += 1
            if end >= n:
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
        return out

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 4) -> dict:
        self.ensure_loaded()
        query = (query or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        if not self.chunks:
            return {
                "ok": True,
                "query": query,
                "results": [],
                "files_indexed": [f.name for f in self.files],
                "note": "Knowledge base is empty or unreadable",
            }

        tokens = _tokenize(query)
        if not tokens:
            tokens = re.findall(r"[a-z0-9]{2,}", query.lower())

        scored = []
        q_lower = query.lower()
        for ch in self.chunks:
            text_lower = ch.text.lower()
            freq: Dict[str, int] = {}
            for t in tokens:
                c = text_lower.count(t)
                if c:
                    freq[t] = c
            if not freq:
                continue
            # Score: distinct token hits matter most, then total frequency,
            # plus a bonus for the exact phrase appearing.
            distinct = len(freq)
            total = sum(freq.values())
            phrase_bonus = 5 if q_lower in text_lower else 0
            score = distinct * 10 + total + phrase_bonus
            scored.append((score, ch, sorted(freq)))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [
            {
                "source": ch.source,
                "chunk_id": ch.chunk_id,
                "score": score,
                "matched_terms": matched,
                "text": ch.text[:1200],
            }
            for score, ch, matched in scored[: max(top_k, 1)]
        ]
        return {
            "ok": True,
            "query": query,
            "results": results,
            "files_indexed": [f.name for f in self.files],
        }

    def get_index_text(self) -> str:
        self.ensure_loaded()
        if not self.files:
            return f"{self.kb_dir} (no KB pages found)"
        total_chunks = sum(f.chunks for f in self.files if not f.error)
        return (
            f"Website KB: {len(self.files)} prosurfaceworks.com pages, "
            f"{total_chunks} chunks "
            f"(services, FAQ, company, areas; each chunk carries [source: URL])"
        )
