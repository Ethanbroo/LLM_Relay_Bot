"""
Web research module — Tavily search + Jina Reader for real web research.

Provides TavilyClient, JinaReader, and WebResearcher that orchestrate
search + URL reading to produce structured research results.

Both APIs are optional — if keys are not configured, the module degrades
gracefully and callers get empty results with a flag indicating no web
search was available.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Character budget for research context injected into browser agent.
# ~2000 tokens at ~3 chars/token.
MAX_RESEARCH_CONTEXT_CHARS = 6000


@dataclass
class SearchHit:
    """A single search result from Tavily."""

    title: str
    url: str
    snippet: str
    content: str = ""
    score: float = 0.0


@dataclass
class ResearchResult:
    """Structured result of a web research operation."""

    query: str
    hits: list[SearchHit] = field(default_factory=list)
    page_contents: dict[str, str] = field(default_factory=dict)  # url -> markdown
    summary: str = ""
    web_search_available: bool = True
    cost_usd: float = 0.0


class TavilyClient:
    """Async client for Tavily Search API."""

    BASE_URL = "https://api.tavily.com"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key if api_key is not None else os.environ.get("TAVILY_API_KEY", "")
        self._http = httpx.AsyncClient(timeout=15.0)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        """Search via Tavily. Returns empty list if unavailable or on error."""
        if not self._api_key:
            return []

        try:
            resp = await self._http.post(
                f"{self.BASE_URL}/search",
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_raw_content": False,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchHit(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    score=r.get("score", 0.0),
                )
                for r in data.get("results", [])
            ]
        except Exception as e:
            logger.error("Tavily search failed: %s", e)
            return []

    async def close(self):
        await self._http.aclose()


class JinaReader:
    """Async client for Jina Reader API (URL -> clean markdown)."""

    BASE_URL = "https://r.jina.ai"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key if api_key is not None else os.environ.get("JINA_API_KEY", "")
        self._http = httpx.AsyncClient(timeout=20.0)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def read_url(self, url: str, max_chars: int = 3000) -> str:
        """Read a URL and return clean markdown. Returns empty string on error."""
        if not self._api_key:
            return ""

        try:
            resp = await self._http.get(
                f"{self.BASE_URL}/{url}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "text/markdown",
                    "X-Return-Format": "markdown",
                },
            )
            resp.raise_for_status()
            return resp.text[:max_chars]
        except Exception as e:
            logger.error("Jina read failed for %s: %s", url, e)
            return ""

    async def close(self):
        await self._http.aclose()


class WebResearcher:
    """Orchestrates Tavily search + Jina read for a research query."""

    def __init__(
        self,
        tavily: TavilyClient | None = None,
        jina: JinaReader | None = None,
    ):
        self._tavily = tavily or TavilyClient()
        self._jina = jina or JinaReader()

    @property
    def available(self) -> bool:
        return self._tavily.available

    async def research(
        self,
        query: str,
        max_search_results: int = 5,
        max_read_urls: int = 2,
    ) -> ResearchResult:
        """Run search + read pipeline. Returns structured result."""
        if not self._tavily.available:
            return ResearchResult(query=query, web_search_available=False)

        # Step 1: Search
        hits = await self._tavily.search(query, max_results=max_search_results)
        if not hits:
            return ResearchResult(query=query, hits=[])

        # Step 2: Read top N URLs concurrently via Jina
        page_contents: dict[str, str] = {}
        if self._jina.available and max_read_urls > 0:
            urls_to_read = [h.url for h in hits[:max_read_urls]]
            tasks = [self._jina.read_url(url) for url in urls_to_read]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for url, content in zip(urls_to_read, results):
                if isinstance(content, str) and content:
                    page_contents[url] = content

        # Step 3: Build condensed summary
        summary = self._build_summary(hits, page_contents)

        # Step 4: Estimate cost (Tavily: ~$0.01/search, Jina: ~$0.001/read)
        cost = 0.01 if hits else 0.0
        cost += 0.001 * len(page_contents)

        return ResearchResult(
            query=query,
            hits=hits,
            page_contents=page_contents,
            summary=summary,
            cost_usd=cost,
        )

    def _build_summary(
        self, hits: list[SearchHit], page_contents: dict[str, str]
    ) -> str:
        """Build a condensed summary capped at MAX_RESEARCH_CONTEXT_CHARS."""
        parts: list[str] = []

        parts.append("Web search findings:")
        for i, hit in enumerate(hits[:5], 1):
            snippet = hit.snippet[:300]
            parts.append(f"  {i}. [{hit.title}]({hit.url})")
            parts.append(f"     {snippet}")

        if page_contents:
            parts.append("\nDetailed page content:")
            for url, content in page_contents.items():
                parts.append(f"\n--- {url} ---")
                parts.append(content[:2000])

        full = "\n".join(parts)
        if len(full) > MAX_RESEARCH_CONTEXT_CHARS:
            full = full[:MAX_RESEARCH_CONTEXT_CHARS] + "\n[...truncated]"
        return full

    async def close(self):
        await self._tavily.close()
        await self._jina.close()
