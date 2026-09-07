"""Bounded external-search providers used by the external research Skill."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.models import ResearchSource


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> list[ResearchSource]: ...


class DisabledSearchProvider:
    name = "unconfigured"

    async def search(self, query: str, max_results: int) -> list[ResearchSource]:
        return []


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str, base_url: str):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("TAVILY_BASE_URL 必须是有效的 HTTPS 地址")
        self.api_key = api_key
        normalized = base_url.rstrip("/")
        self.endpoint = normalized if normalized.endswith("/search") else f"{normalized}/search"

    async def search(self, query: str, max_results: int) -> list[ResearchSource]:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            response = await client.post(
                self.endpoint,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": min(max_results, 10),
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        sources: list[ResearchSource] = []
        for item in payload.get("results", [])[:max_results]:
            try:
                sources.append(
                    ResearchSource(
                        title=str(item.get("title") or "Untitled source"),
                        url=str(item.get("url") or ""),
                        snippet=str(item.get("content") or "")[:2_000],
                        published_at=(
                            str(item["published_date"]) if item.get("published_date") else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                continue
        return sources


def build_search_provider(settings: Settings) -> SearchProvider:
    if not settings.tavily_api_key:
        return DisabledSearchProvider()
    return TavilySearchProvider(settings.tavily_api_key, settings.tavily_base_url)
