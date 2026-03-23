"""
모델·제공자와 무관하게 MCP 도구를 쓰기 위한 프롬프트 보강 및 응답 파싱.

모델은 반드시 아래 마커 사이에 JSON 배열만 넣어 도구를 요청합니다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.mcp_client import MCPClientService, MCPToolInfo

MCP_MARK_BEGIN = "<<<DAON_MCP_CALLS>>>"
MCP_MARK_END = "<<<END_DAON_MCP_CALLS>>>"


def build_mcp_tools_prompt_suffix(tools: list[MCPToolInfo]) -> str:
    """시스템 프롬프트에 붙일 MCP 안내(도구 목록 + 호출 형식)."""
    blocks: list[str] = [
        "",
        "## MCP 도구 (OpenAI/올라마/제미나이 등 API 종류와 무관)",
        "도구가 필요하면 **평문 답변 없이** 아래 형식만 한 번 출력하세요.",
        "도구가 필요 없으면 이 마커를 쓰지 말고 평소처럼 답하세요.",
        "",
        MCP_MARK_BEGIN,
        '[{"server":"서버이름","tool":"도구이름","arguments":{"키":"값"}}]',
        MCP_MARK_END,
        "",
        "여러 도구는 JSON 배열에 객체를 나열합니다. arguments는 도구 스키마에 맞는 객체입니다.",
        "",
        "### 등록된 도구",
    ]
    if not tools:
        blocks.append("(현재 연결된 MCP 도구가 없습니다.)")
    else:
        catalog: list[dict[str, Any]] = []
        for t in tools:
            catalog.append(
                {
                    "server": t.server,
                    "tool": t.name,
                    "description": (t.description or "")[:400],
                    "arguments_json_schema": t.input_schema or {},
                }
            )
        blocks.append(json.dumps(catalog, ensure_ascii=False, indent=2))
    return "\n".join(blocks)


_WS_RE = re.compile(r"\s+")


def parse_mcp_calls_from_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """
    응답에서 MCP 마커 블록을 찾아 파싱합니다.
    반환: (마커 제거·정리된 표시용 텍스트, 호출 목록)
    """
    raw = text or ""
    start = raw.find(MCP_MARK_BEGIN)
    end = raw.find(MCP_MARK_END)
    if start < 0 or end < 0 or end <= start:
        return raw.strip(), []

    inner = raw[start + len(MCP_MARK_BEGIN) : end].strip()
    before = raw[:start].strip()
    after = raw[end + len(MCP_MARK_END) :].strip()
    clean = _WS_RE.sub(" ", f"{before} {after}".strip()).strip()

    calls: list[dict[str, Any]] = []
    try:
        data = json.loads(inner)
    except json.JSONDecodeError:
        return raw.strip(), []

    if isinstance(data, dict):
        if "calls" in data and isinstance(data["calls"], list):
            data = data["calls"]
        else:
            data = [data]
    if not isinstance(data, list):
        return clean or raw.strip(), []

    for item in data:
        if not isinstance(item, dict):
            continue
        server = str(item.get("server") or "").strip()
        tool = str(item.get("tool") or "").strip()
        args = item.get("arguments")
        if not isinstance(args, dict):
            args = {}
        if server and tool:
            calls.append({"server": server, "tool": tool, "arguments": args})
    return (clean if clean else raw.strip()), calls


def execute_mcp_calls(
    client: Optional[MCPClientService],
    calls: list[dict[str, Any]],
) -> str:
    """도구 실행 결과를 다음 user 메시지에 넣을 한 덩어리 텍스트로 만듭니다."""
    if not calls:
        return "(호출된 도구 없음)"
    if client is None:
        return "[MCP] 클라이언트가 없습니다."

    parts: list[str] = []
    for c in calls:
        server = c.get("server")
        tool = c.get("tool")
        args = c.get("arguments") or {}
        label = f"{server}::{tool}"
        try:
            result = client.call_tool_sync(
                str(server),
                str(tool),
                args if isinstance(args, dict) else {},
            )
            text = client.call_tool_result_to_text(result)
            parts.append(f"### {label}\n{text}")
        except Exception as e:
            parts.append(f"### {label}\n[오류] {e}")
    return (
        "The following are the results of the MCP tool execution. Please respond in the language used by the user.\n\n"
        + "\n\n".join(parts)
    )


def inject_system_suffix(
    messages: list[dict[str, Any]], suffix: str
) -> list[dict[str, Any]]:
    """첫 system 메시지에 suffix를 덧붙이거나, 없으면 system 메시지를 추가합니다."""
    if not suffix.strip():
        return messages
    out = [dict(m) for m in messages]
    for i, m in enumerate(out):
        if m.get("role") == "system":
            c = m.get("content")
            base = c if isinstance(c, str) else str(c or "")
            out[i] = {**m, "content": f"{base.rstrip()}{suffix}"}
            return out
    return [{"role": "system", "content": suffix.lstrip()}] + out
