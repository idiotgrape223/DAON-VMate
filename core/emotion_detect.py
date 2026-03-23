"""답변·대화 텍스트에서 감정 라벨 추론 (규칙 기반). model_dict emotionMap 키와 동일한 소문자 라벨."""

from __future__ import annotations

import re
from typing import Final

# model_dict.json emotionMap 과 맞출 것
_CANONICAL: Final[tuple[str, ...]] = (
    "neutral",
    "joy",
    "sadness",
    "anger",
    "fear",
    "surprise",
    "disgust",
    "smirk",
)

# (라벨, 키워드…) — 한·영, 대소문자 무시 매칭
_KEYWORD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "joy",
        (
            "ㅎㅎ",
            "ㅋㅋ",
            "ㅋㅎ",
            "하하",
            "히히",
            "좋아",
            "좋네",
            "좋습",
            "좋지",
            "반가",
            "환영",
            "그래",
            "맞아",
            "기뻐",
            "기쁘",
            "행복",
            "고마워",
            "감사",
            "최고",
            "멋져",
            "웃",
            "즐거",
            "재밌",
            "재미",
            "happy",
            "great",
            "thanks",
            "lol",
            "nice",
        ),
    ),
    (
        "sadness",
        (
            "슬프",
            "슬퍼",
            "우울",
            "속상",
            "아쉽",
            "힘들",
            "눈물",
            "울",
            "괴로",
            "미안해",
            "미안",
            "sad",
            "sorry",
            "unhappy",
        ),
    ),
    (
        "anger",
        (
            "화나",
            "짜증",
            "열받",
            "분해",
            "빡",
            "시러",
            "싫어",
            "angry",
            "mad",
            "hate",
        ),
    ),
    (
        "fear",
        (
            "무서",
            "두려",
            "불안",
            "걱정",
            "떨려",
            "fear",
            "scared",
            "afraid",
        ),
    ),
    (
        "surprise",
        (
            "놀랐",
            "놀라",
            "헉",
            "어머",
            "대박",
            "진짜",
            "설마",
            "wow",
            "what",
            "surpris",
        ),
    ),
    (
        "disgust",
        (
            "역겨",
            "싫다",
            "더러",
            "으으",
            "disgust",
            "gross",
        ),
    ),
    (
        "smirk",
        (
            "흐흐",
            "흥",
            "치사",
            "뻔뻔",
            "의심",
            "씨익",
            "smirk",
        ),
    ),
)


def _normalize_for_match(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", " ", t)
    return t


def detect_emotion_label(text: str) -> str:
    """
    텍스트에서 감정 1개 선택. 키워드가 없으면 neutral.
    여러 감정이 겹치면 언급 횟수(가중)가 가장 큰 것.
    """
    t = _normalize_for_match(text)
    if not t.strip():
        return "neutral"

    scores: dict[str, int] = {k: 0 for k in _CANONICAL}
    for label, words in _KEYWORD_GROUPS:
        for w in words:
            wn = w.lower()
            if len(wn) >= 2 and wn in t:
                scores[label] += t.count(wn)

    best = max(scores, key=lambda k: scores[k])
    if scores[best] <= 0:
        return "neutral"
    return best
