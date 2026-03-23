"""
DuckDuckGo 하이브리드 웹 검색 MCP 서버.
실행: ``python -m mcp_extension.servers.web_search``
"""

from __future__ import annotations
import logging
from mcp.server.fastmcp import FastMCP

# 로그 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daon-web-search")

mcp = FastMCP("daon-web-search")

@mcp.tool()
def web_search(query: str, max_results: int = 10) -> str:
    """
    웹(뉴스, 블로그, 공식 문서 포함)에서 정보를 검색합니다.
    검색어와 가장 연관성이 높은 최신 정보를 제목, URL, 내용 요약과 함께 반환합니다.
    
    Args:
        query: 검색어 (정확한 결과를 위해 구체적으로 입력하세요)
        max_results: 결과 개수 (1~20개)
    """
    q = (query or "").strip()
    if not q:
        return "오류: 검색어가 비어 있습니다."

    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(20, n))

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return "오류: pip install duckduckgo-search 가 필요합니다."

    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                q, 
                region='kr-kr', 
                safesearch='moderate', 
                timelimit=None,
                max_results=n
            )
            rows = list(results)
    except Exception as e:
        return f"검색 중 오류 발생: {e}"

    if not rows:
        return f"'{q}'에 대한 검색 결과가 없습니다. 검색어를 다르게 입력해 보세요."

    parts: list[str] = []
    for i, r in enumerate(rows, 1):
        title = str(r.get("title") or "제목 없음").strip()
        href = str(r.get("href") or r.get("url") or "URL 없음").strip()
        body = str(r.get("body") or "내용 요약 없음").strip()

        entry = [
            f"결과 {i}: {title}",
            f"출처: {href}",
            f"요약: {body}"
        ]
        parts.append("\n".join(entry))

    return "\n\n" + "\n\n---\n\n".join(parts) + "\n\n"


def main() -> None:
    """MCP 서버 실행"""
    mcp.run()


if __name__ == "__main__":
    main()