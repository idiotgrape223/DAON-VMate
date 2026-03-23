"""예시 MCP 서버: echo 도구. 실행: 프로젝트 루트에서 ``python -m mcp_extension.servers.echo_example``."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("daon-echo-example")


@mcp.tool()
def echo(message: str) -> str:
    """입력 문자열을 그대로 돌려줍니다."""
    return message


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
