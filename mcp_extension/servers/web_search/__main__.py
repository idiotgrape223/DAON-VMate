"""
웹 검색 MCP 서버. 기본은 Google(공식 CSE API 또는 googlesearch-python), 실패 시 DuckDuckGo.

공식 Google 검색 API 사용 시(권장): MCP 서버 프로세스 환경변수
  VMATE_GOOGLE_CSE_API_KEY  — Google Cloud API 키
  VMATE_GOOGLE_CSE_CX       — Programmable Search Engine ID (검색엔진 ID)

fragment의 web_search 항목에 "env": { "VMATE_GOOGLE_CSE_API_KEY": "...", "VMATE_GOOGLE_CSE_CX": "..." } 를 넣을 수 있습니다.

실행: ``python -m mcp_extension.servers.web_search``
"""

from __future__ import annotations

import logging
import os

import requests

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daon-web-search")

mcp = FastMCP("daon-web-search")


def _rows_from_google_cse(q: str, n: int) -> list[dict[str, str]]:
    key = os.environ.get("VMATE_GOOGLE_CSE_API_KEY", "").strip()
    cx = os.environ.get("VMATE_GOOGLE_CSE_CX", "").strip()
    if not key or not cx:
        return []
    n = max(1, min(10, int(n)))
    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": key, "cx": cx, "q": q, "num": n},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("Google CSE 요청 실패: %s", e)
        return []

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    rows: list[dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append(
            {
                "title": str(it.get("title") or "제목 없음").strip(),
                "href": str(it.get("link") or "URL 없음").strip(),
                "body": str(it.get("snippet") or "내용 요약 없음").strip(),
            }
        )
    return rows


def _rows_from_googlesearch_pkg(q: str, n: int) -> list[dict[str, str]]:
    try:
        from googlesearch import search
    except ImportError:
        return []

    rows: list[dict[str, str]] = []
    try:
        gen = search(
            term=q,
            num_results=n,
            lang="ko",
            sleep_interval=1,
            advanced=True,
        )
        for r in gen:
            title = getattr(r, "title", None) or "제목 없음"
            href = getattr(r, "url", None) or ""
            body = getattr(r, "description", None) or "내용 요약 없음"
            rows.append(
                {
                    "title": str(title).strip(),
                    "href": str(href).strip(),
                    "body": str(body).strip(),
                }
            )
            if len(rows) >= n:
                break
    except TypeError:
        try:
            for url in search(term=q, num_results=n, lang="ko", sleep_interval=1):
                rows.append(
                    {
                        "title": "제목 없음",
                        "href": str(url).strip(),
                        "body": "내용 요약 없음",
                    }
                )
                if len(rows) >= n:
                    break
        except Exception as e:
            logger.warning("googlesearch (URL 전용) 실패: %s", e)
    except Exception as e:
        logger.warning("googlesearch 실패: %s", e)
    return rows


def _rows_from_duckduckgo(q: str, n: int) -> list[dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []

    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                q,
                region="kr-kr",
                safesearch="moderate",
                timelimit=None,
                max_results=n,
            )
            raw = list(results)
    except Exception as e:
        logger.warning("DuckDuckGo 검색 실패: %s", e)
        return []

    rows: list[dict[str, str]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        rows.append(
            {
                "title": str(r.get("title") or "제목 없음").strip(),
                "href": str(r.get("href") or r.get("url") or "URL 없음").strip(),
                "body": str(r.get("body") or "내용 요약 없음").strip(),
            }
        )
    return rows


def _format_rows(rows: list[dict[str, str]], q: str) -> str:
    if not rows:
        return (
            f"'{q}'에 대한 검색 결과가 없습니다. "
            "Google Custom Search를 쓰려면 VMATE_GOOGLE_CSE_API_KEY / VMATE_GOOGLE_CSE_CX 환경변수를 설정하거나, "
            "네트워크·차단 여부를 확인해 보세요."
        )
    parts: list[str] = []
    for i, r in enumerate(rows, 1):
        entry = [
            f"결과 {i}: {r['title']}",
            f"출처: {r['href']}",
            f"요약: {r['body']}",
        ]
        parts.append("\n".join(entry))
    return "\n\n" + "\n\n---\n\n".join(parts) + "\n\n"


@mcp.tool()
def web_search(query: str, max_results: int = 10) -> str:
    """
    웹에서 정보를 검색합니다. 기본적으로 Google을 우선 사용합니다
    (환경변수로 설정한 공식 Custom Search API → googlesearch-python → DuckDuckGo 순).

    Args:
        query: 검색어
        max_results: 결과 개수 (1~20, CSE는 요청당 최대 10)
    """
    q = (query or "").strip()
    if not q:
        return "오류: 검색어가 비어 있습니다."

    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(20, n))

    source = ""
    rows: list[dict[str, str]] = []

    cse_rows = _rows_from_google_cse(q, n)
    if cse_rows:
        rows = cse_rows
        source = "google_cse"
    else:
        gs_rows = _rows_from_googlesearch_pkg(q, n)
        if gs_rows:
            rows = gs_rows
            source = "google_unofficial"
        else:
            rows = _rows_from_duckduckgo(q, n)
            source = "duckduckgo"

    if not rows:
        return _format_rows([], q)

    body = _format_rows(rows, q)
    if source == "duckduckgo":
        body = (
            "[검색 출처] Google 연동(CSE 또는 googlesearch-python)에서 결과를 가져오지 못해 "
            "DuckDuckGo 보조 검색 결과입니다.\n\n"
        ) + body
    return body


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
