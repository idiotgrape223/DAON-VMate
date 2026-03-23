"""
Open-LLM-VTuber 방식: LLM이 본문에 [joy], [neutral] 같은 태그를 넣고,
emotionMap 값은 Live2D 표정(Expression) 목록의 인덱스.

참고: Open-LLM-VTuber/src/open_llm_vtuber/live2d_model.py
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional


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
        f"사용 가능: {parts}\n"
        "예: [joy]오늘 날씨 좋네요!"
    )


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
    스트리밍 배치·TTS 구간용. 현재 live2d.model_folder 프로필의 emotionMap 키에 대응하는 [tag]만 제거.
    """
    if not text or not full_config:
        return text
    live = full_config.get("live2d") or {}
    folder = str(live.get("model_folder", "") or "").strip()
    if not folder:
        return text
    from core.model_profile import profile_for_folder

    prof = profile_for_folder(folder)
    em = build_emo_map_from_profile(prof)
    if not em:
        return text
    out = strip_emotion_tags_regex(text, em)
    keys = sorted(em.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(k) for k in keys)
    if text and re.match(rf"(?i)^\s*\[({alt})\]", text):
        out = out.lstrip()
    return out


def assistant_history_plain(text: str, full_config: Optional[dict[str, Any]]) -> str:
    """히스토리 저장용 전체 답변에서 [태그] 제거 (Open-LLM remove_emotion_keywords)."""
    if not text or not full_config:
        return text
    live = full_config.get("live2d") or {}
    folder = str(live.get("model_folder", "") or "").strip()
    if not folder:
        return text
    from core.model_profile import profile_for_folder

    prof = profile_for_folder(folder)
    em = build_emo_map_from_profile(prof)
    if not em:
        return text
    out = remove_emotion_tags(text, em)
    keys = sorted(em.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(k) for k in keys)
    if text and re.match(rf"(?i)^\s*\[({alt})\]", text):
        out = out.lstrip()
    return out
