"""Our own web search + page reader for the AI research passes.

The API gateway's server-side web_search / web_fetch tools return
`error_code='unavailable'` (confirmed 2026-10-15 on every model and tool version), so
the research passes were coming back empty ("форма, составы, таблица ... отсутствуют").
Instead the model gets two ordinary client tools that we execute ourselves:

- web_search(query): DuckDuckGo's plain-HTML endpoint (no key, no JS), top results
  with title / url / snippet; restricted to allowed domains via `site:` when the
  pass is source-specific (HLTV, Flashscore, football-lineups...).
- fetch_page(url): GET the page and return its visible text, trimmed.

Pages behind bot protection (Cloudflare 403 etc.) simply come back as an error string
-- never bypassed.
"""
from __future__ import annotations

import html as _html
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MAX_RESULTS = 8
PAGE_CHARS = 7000

TOOLS = [
    {
        "name": "web_search",
        "description": "Поиск в интернете (DuckDuckGo). Возвращает до 8 результатов: заголовок, ссылка, "
                       "фрагмент. Пиши конкретные запросы, можно на английском и русском.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "fetch_page",
        "description": "Открыть страницу по ссылке и получить её текст (первые ~7000 символов).",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
]

_RESULT = re.compile(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _clean(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _real_url(href: str) -> str:
    if "uddg=" in href:
        return unquote(parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query).get("uddg", [href])[0])
    return href if href.startswith("http") else "https:" + href


def _allowed(url: str, domains: list[str] | None) -> bool:
    if not domains:
        return True
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


SEARCH_MODEL = os.environ.get("WEBSEARCH_MODEL", "gpt-5.6-luna")


async def _gateway_search(client: httpx.AsyncClient, query: str, domains: list[str] | None) -> str:
    """Web search through the gateway's OpenAI-compatible Responses API with the
    built-in web_search tool (2026-10-25): DuckDuckGo and Bing started serving the
    server a captcha, so this is the primary search now. Cheap model (×0.38)."""
    base = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not base or not key:
        return ""
    tool: dict = {"type": "web_search"}
    if domains:
        tool["filters"] = {"allowed_domains": domains[:10]}
    prompt = (f"Найди в интернете: {query}\n"
              + (f"Ищи только на сайтах: {', '.join(domains)}.\n" if domains else "")
              + "Дай 5-8 самых полезных результатов: заголовок, ссылка и ключевые факты с цифрами "
                "(счета, даты, статистика). Только найденное, без выдумок, по-русски.")
    try:
        r = await client.post(f"{base}/v1/responses", headers={"Authorization": f"Bearer {key}"},
                              json={"model": SEARCH_MODEL, "tools": [tool], "input": prompt}, timeout=120)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return ""
    texts = [c.get("text", "") for o in data.get("output", []) if o.get("type") == "message"
             for c in o.get("content", []) if c.get("text")]
    return "\n".join(texts).strip()


async def search(client: httpx.AsyncClient, query: str, domains: list[str] | None = None) -> str:
    found = await _gateway_search(client, query, domains)
    if found:
        return found
    q = query
    if domains:
        q += " " + " OR ".join(f"site:{d}" for d in domains[:3])
    try:
        r = await client.post("https://html.duckduckgo.com/html/", data={"q": q},
                              headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return f"Ошибка поиска: {e.__class__.__name__}"
    out = []
    for href, title, snippet in _RESULT.findall(r.text):
        url = _real_url(_html.unescape(href))
        if "duckduckgo.com/y.js" in url or not _allowed(url, domains):
            continue
        out.append(f"- {_clean(title)}\n  {url}\n  {_clean(snippet)}")
        if len(out) >= MAX_RESULTS:
            break
    return "\n".join(out) or "Ничего не найдено."


async def fetch(client: httpx.AsyncClient, url: str, domains: list[str] | None = None) -> str:
    if not url.startswith("http"):
        return "Некорректная ссылка."
    if not _allowed(url, domains):
        return "Эта ссылка вне разрешённых сайтов для этого поиска."
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"},
                             timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return f"Страница недоступна: {e.__class__.__name__}"
    if r.status_code != 200:
        return f"Страница недоступна (HTTP {r.status_code})."
    text = r.text
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", text)
    text = _clean(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text[:PAGE_CHARS] or "Пустая страница."
