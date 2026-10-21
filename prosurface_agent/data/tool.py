"""
LLM-facing data_search tool. No LLM calls, purely local retrieval.
"""

from __future__ import annotations

from .store import DataStore


class DataSearchTool:
    def __init__(self, store: DataStore) -> None:
        self.store = store

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "data_search",
                "description": (
                    "Search the website knowledge base (prosurfaceworks.com pages). "
                    "Use this FIRST for every user question. Returns matching passages "
                    "with source page names. Only answer from these passages. "
                    "If results look thin, retry with simpler keywords."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "User question or keywords to look up in the knowledge base.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Max passages to return (default 5).",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def run(self, arguments: dict) -> dict:
        query = arguments.get("query", "")
        try:
            top_k = int(arguments.get("top_k", 5))
        except (TypeError, ValueError):
            top_k = 5
        return self.store.search(query, top_k=min(max(top_k, 1), 8))
