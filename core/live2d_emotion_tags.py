"""
Open-LLM-VTuber 방식: LLM이 본문에 [joy], [neutral] 같은 태그를 넣고,
emotionMap 값은 Live2D 표정(Expression) 목록의 인덱스.

참고: Open-LLM-VTuber/src/open_llm_vtuber/live2d_model.py
"""

from __future__ import annotations

import html
import math
import re
from typing import Any, Optional

# Core 시스템 프롬프트와 동일한 기본 허용 태그 (프로필 emotionMap 이 비어 있을 때도 한글 [웃음] 등 제거용)
_DEFAULT_CORE_EMOTION_TAG_KEYS = frozenset(
    {"neutral", "joy", "sadness", "anger", "fear", "disgust", "surprise", "smirk"}
)

_ORPHAN_DOUBLE_ASTERISK = re.compile(r"\*\*\s*\*\*")


def _coerce_emotion_index(v: Any) -> Optional[int]:
    """emotionMap 값이 int / 정수 float / 숫자 문자열일 때만 인덱스로 인정."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v):
        iv = int(v)
        if abs(v - float(iv)) < 1e-9:
            return iv
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("-") and s[1:].isdigit():
            return int(s)
        if s.isdigit():
            return int(s)
    return None


def build_emo_map_from_profile(profile: Optional[dict[str, Any]]) -> dict[str, int]:
    """emotionMap → {소문자 키: 정수 인덱스}."""
    if not profile:
        return {}
    em = profile.get("emotionMap")
    if not isinstance(em, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in em.items():
        if not isinstance(k, str):
            continue
        iv = _coerce_emotion_index(v)
        if iv is not None and iv >= 0:
            out[k.strip().lower()] = iv
    return out


def emotion_tags_prompt_instruction(emo_map: dict[str, int]) -> str:
    """시스템 프롬프트에 붙일 안내 (Open-LLM-VTuber 의 emo_str 개념)."""
    if not emo_map:
        return ""
    parts = " ".join(f"[{k}]" for k in sorted(emo_map.keys()))
    return (
        "\n\n[Live2D 감정 태그]\n"
        "답변에는 기본적으로 감정이 드러날 때마다 아래 태그 중 하나를 대괄호 그대로, "
        "가능하면 문장 맨 앞에 붙입니다. 중립일 때는 [neutral]을 쓸 수 있습니다. "
        "한 번에 하나의 태그만 쓰는 것을 권장합니다.\n"
        f"사용 가능(오직 아래 영문 키만): {parts}\n"
        "**금지**: 대괄호 안에 한글·한자·그 밖의 임의 단어(예: [웃음], [기쁨], [당황])를 넣지 마세요. "
        "오직 위 목록의 영문 키만 허용됩니다.\n"
        "예: [joy]오늘 날씨 좋네요!"
    )


def _allowed_emotion_tag_keys(emo_map: dict[str, int]) -> set[str]:
    if emo_map:
        return {k.strip().lower() for k in emo_map.keys() if isinstance(k, str)}
    return set(_DEFAULT_CORE_EMOTION_TAG_KEYS)


def strip_invalid_emotion_bracket_tokens(text: str, emo_map: dict[str, int]) -> str:
    """
    emotionMap 에 없는 [대괄호] 토큰 제거. [웃음]·[laugh] 등 영문 허용 목록 밖 태그를 막음.
    허용 키는 프로필 emotionMap(없으면 Core 기본 8종).
    """
    if not text:
        return text
    allowed = _allowed_emotion_tag_keys(emo_map)

    def _repl(m: re.Match[str]) -> str:
        inner = m.group(1).strip().lower()
        if inner in allowed:
            return m.group(0)
        return ""

    out = re.sub(r"\[([^\]\[\n]{1,64})\]", _repl, text)
    out = _ORPHAN_DOUBLE_ASTERISK.sub("", out)
    return out


def extract_emotion_indices(text: str, emo_map: dict[str, int]) -> list[int]:
    """
    문자열에서 [감정키] 패턴을 찾아 emotionMap 의 정수 값(표정 인덱스) 목록을 순서대로 반환.
    Open-LLM-VTuber Live2dModel.extract_emotion 와 동일 로직.
    """
    if not text or not emo_map:
        return []
    expression_list: list[int] = []
    s = text.lower()
    i = 0
    keys_by_len = sorted(emo_map.keys(), key=len, reverse=True)
    while i < len(s):
        if s[i] != "[":
            i += 1
            continue
        matched = False
        for key in keys_by_len:
            emo_tag = f"[{key}]"
            if s[i : i + len(emo_tag)] == emo_tag:
                expression_list.append(emo_map[key])
                i += len(emo_tag) - 1
                matched = True
                break
        i += 1
    return expression_list


def remove_emotion_tags(text: str, emo_map: dict[str, int]) -> str:
    """
    화면·히스토리용으로 [태그] 제거. Open-LLM-VTuber Live2dModel.remove_emotion_keywords 와 동등.
    """
    if not text or not emo_map:
        return text
    result = text
    lower_result = result.lower()
    for key in emo_map.keys():
        lower_key = f"[{key}]".lower()
        while lower_key in lower_result:
            start_index = lower_result.find(lower_key)
            end_index = start_index + len(lower_key)
            result = result[:start_index] + result[end_index:]
            lower_result = lower_result[:start_index] + lower_result[end_index:]
    return result


def strip_emotion_tags_regex(text: str, emo_map: dict[str, int]) -> str:
    """한 번에 제거 (배치 스트리밍용). 키 길이 내림차순으로 alternation."""
    if not text or not emo_map:
        return text
    keys = sorted(emo_map.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(k) for k in keys)
    return re.sub(rf"\[({alt})\]", "", text, flags=re.IGNORECASE)


def strip_assistant_tags_for_pipeline(
    text: str, full_config: Optional[dict[str, Any]]
) -> str:
    """
    스트리밍 배치·TTS 구간용. 허용 emotionMap [tag] 제거 후, 허용 목록 밖 [한글] 등도 제거.
    """
    if not text or not full_config:
        return text
    live = full_config.get("live2d") or {}
    folder = str(live.get("model_folder", "") or "").strip()
    em: dict[str, int] = {}
    if folder:
        from core.model_profile import effective_profile_for_folder

        prof = effective_profile_for_folder(folder)
        em = build_emo_map_from_profile(prof)
    out = text
    if em:
        out = strip_emotion_tags_regex(out, em)
        keys = sorted(em.keys(), key=len, reverse=True)
        alt = "|".join(re.escape(k) for k in keys)
        if text and re.match(rf"(?i)^\s*\[({alt})\]", text):
            out = out.lstrip()
    out = strip_invalid_emotion_bracket_tokens(out, em)
    return out


# 줄 맨 앞만이 아니라, 한 줄 끝에 `... ### 답변 실제답` 처럼 붙은 경우도 인식해야 함
_THINKING_ANSWER_HEADER = re.compile(r"###\s*답변\s*")


def strip_thinking_mode_answer_only(text: str, full_config: Optional[dict[str, Any]]) -> str:
    """
    사고 모드 응답에서 `### 답변` 이후만 남깁니다(TTS·히스토리용).
    설정이 꺼져 있거나 구분자가 없으면 원문을 그대로 둡니다.
    """
    if not text or not full_config:
        return text
    if not bool((full_config.get("llm") or {}).get("thinking_mode", False)):
        return text
    m = _THINKING_ANSWER_HEADER.search(text)
    if not m:
        return text
    return text[m.end() :].lstrip()


_THINKING_THINK_HEADER = re.compile(r"###\s*사고\s*")


def assistant_thinking_display_body_html(
    text: str,
    full_config: Optional[dict[str, Any]],
    *,
    think_color: str,
    body_color: str,
    name_span_before_answer: Optional[str] = None,
) -> Optional[str]:
    """
    사고 모드이고 `### 사고` / `### 답변` 구조가 있으면 헤더는 숨기고,
    사고 본문은 기울임+think_color, 답변 본문은 body_color 로 RichText 조각을 반환.
    `name_span_before_answer`가 있으면 답변 본문 직전에만 붙임(사고 블록 옆에는 이름 없음).
    해당 없으면 None (호출측에서 일반 단일 스팬 처리).
    """
    if not text or not full_config:
        return None
    if not bool((full_config.get("llm") or {}).get("thinking_mode", False)):
        return None

    plain = assistant_history_plain(text, full_config)
    s = plain if isinstance(plain, str) else text

    def esc_br(sub: str) -> str:
        return html.escape(sub).replace("\n", "<br/>")

    mt = _THINKING_THINK_HEADER.search(s)
    ma_only = _THINKING_ANSWER_HEADER.search(s)

    if not mt and not ma_only:
        return None

    chunks: list[str] = []

    if mt:
        prefix = s[: mt.start()].rstrip()
        after_think_hdr = s[mt.end() :]
        ma_rel = _THINKING_ANSWER_HEADER.search(after_think_hdr)
        if ma_rel:
            think_body = after_think_hdr[: ma_rel.start()].strip()
            answer_body = after_think_hdr[ma_rel.end() :].lstrip()
        else:
            think_body = after_think_hdr.strip()
            answer_body = ""

        if prefix:
            chunks.append(
                f'<span style="color:{body_color};">{esc_br(prefix)}</span>'
            )
        show_think = bool(think_body) or (
            not ma_rel and bool(after_think_hdr.strip())
        )
        if show_think:
            chunks.append(
                f'<span style="color:{think_color};font-style:italic;">'
                f"{esc_br(think_body)}</span>"
            )
        if answer_body:
            if show_think:
                chunks.append("<br/>")
            if name_span_before_answer is not None:
                chunks.append(name_span_before_answer)
            chunks.append(
                f'<span style="color:{body_color};">{esc_br(answer_body)}</span>'
            )
        return "".join(chunks) if chunks else None

    if ma_only:
        prefix = s[: ma_only.start()].rstrip()
        answer_body = s[ma_only.end() :].lstrip()
        if prefix:
            chunks.append(
                f'<span style="color:{body_color};">{esc_br(prefix)}</span>'
            )
        if answer_body:
            if prefix:
                chunks.append("<br/>")
            if name_span_before_answer is not None:
                chunks.append(name_span_before_answer)
            chunks.append(
                f'<span style="color:{body_color};">{esc_br(answer_body)}</span>'
            )
        return "".join(chunks) if chunks else None

    return None


def thinking_mode_answer_body_if_marked(
    text: str, full_config: Optional[dict[str, Any]]
) -> Optional[str]:
    """
    사고 모드이고 `### 답변` 헤더가 있을 때만 그 아래 본문을 반환.
    마커가 없으면 None (스트리밍 TTS에서 사고 전체를 읽지 않도록 구분).
    """
    if not text or not full_config:
        return None
    if not bool((full_config.get("llm") or {}).get("thinking_mode", False)):
        return None
    m = _THINKING_ANSWER_HEADER.search(text)
    if not m:
        return None
    return text[m.end() :].lstrip()


def assistant_history_plain(text: str, full_config: Optional[dict[str, Any]]) -> str:
    """화면 표시·[태그] 제거용. 사고 블록 제거는 TTS/히스토리용으로 strip_thinking_mode_answer_only 를 별도 호출."""
    if not text or not full_config:
        return text
    live = full_config.get("live2d") or {}
    folder = str(live.get("model_folder", "") or "").strip()
    em: dict[str, int] = {}
    if folder:
        from core.model_profile import effective_profile_for_folder

        prof = effective_profile_for_folder(folder)
        em = build_emo_map_from_profile(prof)
    out = text
    if em:
        out = remove_emotion_tags(text, em)
        keys = sorted(em.keys(), key=len, reverse=True)
        alt = "|".join(re.escape(k) for k in keys)
        if text and re.match(rf"(?i)^\s*\[({alt})\]", text):
            out = out.lstrip()
    out = strip_invalid_emotion_bracket_tokens(out, em)
    return out
