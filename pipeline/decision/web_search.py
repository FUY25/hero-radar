from __future__ import annotations

import html
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any


class _DuckDuckGoHtmlParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = max(0, int(limit))
        self.results: list[dict[str, str]] = []
        self._capture_title = False
        self._capture_snippet = False
        self._active_title: list[str] = []
        self._active_snippet: list[str] = []
        self._pending_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            if len(self.results) >= self.limit:
                return
            self._pending_href = attr_map.get("href", "")
            self._active_title = []
            self._capture_title = True
            return
        if "result__snippet" in classes and self.results and not self.results[-1]["description"]:
            self._active_snippet = []
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self._capture_title and tag == "a":
            title = _clean_text("".join(self._active_title))
            url = _clean_result_url(self._pending_href)
            if title and url:
                self.results.append({"title": title, "url": url, "description": ""})
            self._capture_title = False
            self._active_title = []
            self._pending_href = ""
            return
        if self._capture_snippet and self.results and tag in {"a", "div"}:
            self.results[-1]["description"] = _clean_text("".join(self._active_snippet))
            self._capture_snippet = False
            self._active_snippet = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._active_title.append(data)
        if self._capture_snippet:
            self._active_snippet.append(data)


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def _clean_result_url(value: str) -> str:
    raw = html.unescape(str(value or "").strip())
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urllib.parse.urlparse(raw)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    return raw


def parse_duckduckgo_html(body: str, *, limit: int = 5) -> list[dict[str, str]]:
    parser = _DuckDuckGoHtmlParser(limit=limit)
    parser.feed(body or "")
    return parser.results[: max(0, int(limit))]


class DuckDuckGoSearchClient:
    def __init__(
        self,
        *,
        opener: Any | None = None,
        timeout: int = 20,
        endpoint: str = "https://duckduckgo.com/html/",
    ) -> None:
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.endpoint = endpoint

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        max_results = max(0, int(limit or 0))
        if not query or max_results <= 0:
            return []
        url = f"{self.endpoint}?{urllib.parse.urlencode({'q': query})}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "HeroRadar/0.1 (+https://localhost)",
            },
        )
        with self.opener(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return parse_duckduckgo_html(body, limit=max_results)
