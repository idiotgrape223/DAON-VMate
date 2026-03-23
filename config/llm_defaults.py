"""LLM 제공자별 기본 API 베이스 URL (설정 UI에 노출하지 않고 저장 시 자동 적용)."""

from __future__ import annotations

# 엔진이 ollama는 /api/chat, OpenAI 호환은 /v1/chat/completions 를 붙입니다.
LLM_DEFAULT_API_URL: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "openai_compatible": "https://api.openai.com/v1",
    "lm_studio": "http://127.0.0.1:1234/v1",
}


def default_llm_api_url_for_provider(provider: str) -> str:
    return LLM_DEFAULT_API_URL.get(provider, "http://127.0.0.1:8000/v1")
