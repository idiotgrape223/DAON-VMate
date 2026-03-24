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


def mcp_emotion_allowlist_ko_sentence(full_config: Optional[dict[str, Any]]) -> str:
    """
    MCP 라운드 직후 사용자에게 말할 때 Live2D/Core와 동일한 **영문** 태그만 쓰게 하는 한 줄 안내.
    (모델이 [당황] 등 한글 괄호 태그를 임의로 쓰는 것을 줄이기 위함.)
    """
    if not full_config:
        return (
            "[답변 형식] 감정 태그는 Core System Prompt 의 영문 키만 사용하세요. "
            "[당황]·[기쁨] 같은 한글·임의 대괄호 태그는 금지입니다."
        )
    llm_sec = full_config.get("llm") or {}
    live = full_config.get("live2d") or {}
    if not (
        bool(llm_sec.get("use_emotion_tags", True))
        and bool(live.get("auto_emotion_from_assistant", True))
    ):
        return ""
    folder = str(live.get("model_folder", "") or "").strip()
    if not folder:
        return (
            "[답변 형식] 감정 태그는 Core 에 명시된 영문 키(예: [neutral], [joy])만 사용하세요. "
            "[당황] 등 한글 괄호 태그는 금지입니다."
        )
    from core.live2d_emotion_tags import build_emo_map_from_profile
    from core.model_profile import profile_for_folder

    em = build_emo_map_from_profile(profile_for_folder(folder))
    if not em:
        return (
            "[답변 형식] 감정 태그는 Core 에 명시된 영문 키만 사용하세요. "
            "[당황] 등 한글 괄호 태그는 금지입니다."
        )
    keys = " ".join(f"[{k}]" for k in sorted(em.keys()))
    return (
        f"[답변 형식] 감정 태그는 **오직** 다음 영문 키만 사용하세요: {keys}. "
        "[당황]·[불안] 등 한글·임의 대괄호 태그는 **절대** 쓰지 마세요."
    )


def build_mcp_tools_prompt_suffix(
    tools: list[MCPToolInfo],
    full_config: Optional[dict[str, Any]] = None,
) -> str:
    """시스템 프롬프트에 붙일 MCP 안내(도구 목록 + 호출 형식)."""
    blocks: list[str] = [
        "",
        "## MCP 도구 (OpenAI/올라마/제미나이 등 API 종류와 무관)",
        "**중요(감정 태그와의 충돌 방지)**: MCP 도구를 쓰는 그 **한 번의 assistant 응답**에서는 "
        "[joy] 등 감정 태그를 **절대 쓰지 마세요**. 그 응답 전체는 **오직** 아래 마커와 그 사이 JSON만이어야 합니다(앞뒤 공백·한 줄 설명도 금지).",
        "**여러 도구가 모두 필요할 때만** 같은 마커 블록 안 JSON 배열에 호출을 여러 개 넣으세요(실행 순서는 배열 순서). "
        "**사용자가 요청한 도구만** 호출하세요. 웹검색만 부탁받았다면 `web_search` 만 사용하고 `file_agent` 등은 **넣지 마세요**. "
        "파일 저장·workspace를 **명시적으로** 요청받은 경우에만 `workspace_write` 등을 쓰세요.",
        "도구가 필요하면 **평문·감정 태그 없이** 아래 형식**만** 출력하세요. "
        "**금지**: ` ```json ` 코드블록·마크다운으로 감싸지 마세요. 마커 밖에 설명 글도 넣지 마세요.",
        "도구가 필요 없으면 이 마커를 쓰지 말고 평소처럼 답하세요(이때는 감정 태그 규칙을 따르세요).",
        "",
        MCP_MARK_BEGIN,
        '[{"server":"서버A","tool":"도구1","arguments":{}},{"server":"서버B","tool":"도구2","arguments":{}}]',
        MCP_MARK_END,
        "",
        "배열 원소마다 server·tool·arguments 를 넣습니다. (여러 도구가 필요할 때만 한 배열에 섞습니다.)",
        "**file_agent workspace_write** (저장 요청이 있을 때만): .xlsx 는 UTF-8 텍스트만으로도 저장 가능(OOXML, 줄→A열·쉼표면 CSV 열). "
        "실제 xlsx 바이너리는 content_base64=true. .pdf/이미지 등은 content_base64=true+base64.",
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
    em_note = mcp_emotion_allowlist_ko_sentence(full_config)
    if em_note:
        blocks.extend(["", "### 도구 실행 후 사용자 답변", em_note])
    return "\n".join(blocks)


_WS_RE = re.compile(r"\s+")
_MD_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _calls_from_parsed_json(data: Any) -> list[dict[str, Any]]:
    """json.loads 결과에서 server+tool 호출 목록 추출."""
    if isinstance(data, dict):
        if "calls" in data and isinstance(data["calls"], list):
            data = data["calls"]
        else:
            data = [data]
    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        server = str(item.get("server") or "").strip()
        tool = str(item.get("tool") or "").strip()
        args = item.get("arguments")
        if not isinstance(args, dict):
            args = {}
        if server and tool:
            out.append({"server": server, "tool": tool, "arguments": args})
    return out


def parse_mcp_calls_from_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """
    응답에서 MCP 마커 블록을 찾아 파싱합니다.
    마커가 없고 모델이 ```json … ``` 또는 단일 JSON 객체만 낸 경우도 호환합니다.
    반환: (마커·코드블록 제거 후 표시용 텍스트, 호출 목록)
    """
    raw = text or ""
    start = raw.find(MCP_MARK_BEGIN)
    end = raw.find(MCP_MARK_END)
    if start >= 0 and end > start:
        inner = raw[start + len(MCP_MARK_BEGIN) : end].strip()
        before = raw[:start].strip()
        after = raw[end + len(MCP_MARK_END) :].strip()
        clean = _WS_RE.sub(" ", f"{before} {after}".strip()).strip()
        try:
            data = json.loads(inner)
        except json.JSONDecodeError:
            return raw.strip(), []
        calls = _calls_from_parsed_json(data)
        # 호출만 있고 앞뒤 문장이 없으면 빈 문자열(원문 마커·JSON 블록 재노출 방지)
        return (clean if clean.strip() else ""), calls

    calls: list[dict[str, Any]] = []
    clean = raw
    for m in _MD_JSON_FENCE.finditer(raw):
        inner = m.group(1).strip()
        try:
            data = json.loads(inner)
        except json.JSONDecodeError:
            continue
        got = _calls_from_parsed_json(data)
        if got:
            calls.extend(got)
            clean = clean.replace(m.group(0), " ")

    if not calls:
        st = raw.strip()
        if len(st) >= 2 and st.startswith("{") and st.endswith("}"):
            try:
                data = json.loads(st)
                calls = _calls_from_parsed_json(data)
                if calls:
                    clean = ""
            except json.JSONDecodeError:
                pass

    if not calls:
        return raw.strip(), []

    clean = _WS_RE.sub(" ", clean.strip()).strip()
    return (clean if clean.strip() else ""), calls


def execute_mcp_calls(
    client: Optional[MCPClientService],
    calls: list[dict[str, Any]],
    full_config: Optional[dict[str, Any]] = None,
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

    body = (
        "The following are the results of the MCP tool execution. Please respond in the language used by the user.\n\n"
        + "\n\n".join(parts)
    )
    em = mcp_emotion_allowlist_ko_sentence(full_config)
    if em:
        body = f"{body}\n\n{em}"
    if full_config and bool((full_config.get("llm") or {}).get("thinking_mode")):
        body = (
            f"{body}\n\n"
            "[사고 모드] 위 결과를 반영한 **최종** 사용자 답변은 반드시 "
            "`### 사고`(내부 추론) 다음에 `### 답변`(실제 대화 문장) 형식으로 작성하세요. "
            "도구 결과 요약만 평문 한 덩어리로 보내지 마세요."
        )
    return body


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
