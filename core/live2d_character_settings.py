"""Live2D 모델 폴더별 캐릭터 프롬프트 (daon_{folder}_settings.json)."""

from __future__ import annotations

import json
import os
from typing import Any

SETTINGS_FILENAME_PREFIX = "daon_"
SETTINGS_FILENAME_SUFFIX = "_settings.json"

# 채팅 말풍선 표시명 + LLM 에 전달할 호칭 (JSON 최상위)
CHARACTER_NAME_KEY = "character_name"

# JSON 키 → 시스템 프롬프트에 넣을 때 섹션 제목(한글)
SECTION_KEYS: tuple[tuple[str, str], ...] = (
    ("personality", "성격·특성"),
    ("speech_style", "말투"),
    ("traits_or_habits", "습관·행동"),
    ("speech_examples", "말투 예시·대사"),
    ("restrictions", "금지·주의사항"),
    ("extra_instructions", "추가 지시"),
)


def default_character_settings() -> dict[str, Any]:
    out: dict[str, Any] = {CHARACTER_NAME_KEY: ""}
    for key, _ in SECTION_KEYS:
        out[key] = ""
    return out


def character_settings_path(repo_root: str, folder_name: str) -> str:
    fn = f"{SETTINGS_FILENAME_PREFIX}{folder_name}{SETTINGS_FILENAME_SUFFIX}"
    return os.path.normpath(
        os.path.join(repo_root, "assets", "live2d-models", folder_name, fn)
    )


def load_character_settings(repo_root: str, folder_name: str) -> dict[str, Any]:
    out = default_character_settings()
    folder = (folder_name or "").strip()
    if not folder:
        return out
    path = character_settings_path(repo_root, folder)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return out
    if not isinstance(raw, dict):
        return out
    cn = raw.get(CHARACTER_NAME_KEY)
    if isinstance(cn, str):
        out[CHARACTER_NAME_KEY] = cn
    elif cn is not None:
        out[CHARACTER_NAME_KEY] = str(cn)
    for key, _ in SECTION_KEYS:
        v = raw.get(key)
        if isinstance(v, str):
            out[key] = v
        elif v is not None:
            out[key] = str(v)
    return out


def save_character_settings(
    repo_root: str, folder_name: str, data: dict[str, Any]
) -> tuple[bool, str]:
    folder = (folder_name or "").strip()
    if not folder:
        return False, "모델 폴더 이름이 비어 있습니다."
    base = os.path.join(repo_root, "assets", "live2d-models", folder)
    if not os.path.isdir(base):
        return False, f"모델 폴더가 없습니다: {base}"
    path = character_settings_path(repo_root, folder)
    payload: dict[str, Any] = {"version": 1}
    cn = data.get(CHARACTER_NAME_KEY)
    payload[CHARACTER_NAME_KEY] = (
        (cn or "").strip() if isinstance(cn, str) else str(cn or "")
    )
    for key, _ in SECTION_KEYS:
        v = data.get(key)
        payload[key] = (v or "").strip() if isinstance(v, str) else str(v or "")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return False, str(e)
    return True, path


def get_assistant_display_name(
    repo_root: str,
    folder_name: str,
    *,
    legacy_chat_assistant_name: str | None = None,
) -> str:
    """채팅 UI 에 보이는 이름. 캐릭터 JSON 우선, 없으면 구 설정(ui.chat_assistant_name) 폴백."""
    data = load_character_settings(repo_root, folder_name)
    n = str(data.get(CHARACTER_NAME_KEY, "") or "").strip()
    if n:
        return n
    leg = (legacy_chat_assistant_name or "").strip()
    if leg:
        return leg
    return "DAON"


def compose_character_prompt_block(folder_name: str, data: dict[str, Any]) -> str:
    """비어 있지 않은 필드만 모아 LLM system 에 붙일 블록(강제 준수 문구 포함)."""
    folder = (folder_name or "").strip()
    chunks: list[str] = []
    name = str(data.get(CHARACTER_NAME_KEY, "") or "").strip()
    if name:
        chunks.append(
            f"### 이름 (채팅 표시·자기 호칭)\n"
            f"{name}\n"
            "You MUST present as this character. In dialogue, stay consistent with this name/identity. "
            "The user sees this name as the speaker label in the chat UI."
        )
    for key, title in SECTION_KEYS:
        val = str(data.get(key, "") or "").strip()
        if val:
            chunks.append(f"### {title}\n{val}")
    if not chunks:
        return ""

    body = "\n\n".join(chunks)
    # 메타 지시는 영문으로 고정(모델이 규칙으로 인식하기 쉬움). 본문 섹션은 사용자 입력 그대로.
    preamble = f"""## MANDATORY CHARACTER CONTRACT (Live2D: {folder})

The text below is NOT suggestions. It is a HARD CONSTRAINT on how you must speak and act in every assistant message.

You MUST:
- Match personality, tone, habits, example lines, and restrictions below exactly. Generic or default assistant voice is FORBIDDEN when it conflicts with this contract.
- If the earlier generic system prompt disagrees with this contract on persona, speech style, behavior, or taboos, you MUST follow THIS contract for those aspects.
- Still obey non-conflicting technical rules from the Core System Prompt (for example required emotion tag format and bracket tags) while remaining fully in character.

You MUST NOT:
- Drift into a neutral narrator, textbook tone, or out-of-character voice.
- Ignore "금지·주의사항" or "말투" sections when they are filled in.

Before you answer the user, check: every sentence must sound like THIS character.

--- BEGIN CHARACTER SPECIFICATION ---

"""
    postamble = """

--- END CHARACTER SPECIFICATION ---

REMINDER: Your very next reply must fully satisfy the specification above. Treat violation as an error. 한국어 응답 시에도 위 성격·말투·금지사항을 반드시 지킬 것."""

    return preamble + body + postamble
