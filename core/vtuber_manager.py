from __future__ import annotations

from typing import Any, Callable, Optional

from core.live2d_emotion_tags import (
    assistant_history_plain,
    strip_thinking_mode_answer_only,
)
from core.llm_attachments import LLMMediaAttachment, format_user_text_for_history
from core.llm_engine import LLMEngine
from core.tts_engine import TTSEngine

# user/assistant 쌍 개수 상한 (너무 길면 컨텍스트 비대)
_MAX_HISTORY_PAIRS = 16


def _assistant_content_for_history(assistant_full: str, fc: dict[str, Any]) -> str:
    """LLM 컨텍스트용 assistant 문자열. 사고 모드일 때는 ### 사고/답변 구조를 유지해 형식이 무너지지 않게 함."""
    thinking = bool((fc.get("llm") or {}).get("thinking_mode", False))
    if thinking:
        return assistant_history_plain(assistant_full, fc).strip()
    return assistant_history_plain(
        strip_thinking_mode_answer_only(assistant_full, fc), fc
    ).strip()


class VTuberManager:
    """
    LLM, TTS 등 코어 컴포넌트 관리. Ollama chat 히스토리 유지.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.llm_engine = LLMEngine(config)
        self.tts_engine = TTSEngine(config)
        self._chat_history: list[dict[str, str]] = []
        self._history_listeners: list[Callable[[], None]] = []

    def add_history_listener(self, cb: Callable[[], None]) -> None:
        if cb not in self._history_listeners:
            self._history_listeners.append(cb)

    def remove_history_listener(self, cb: Callable[[], None]) -> None:
        try:
            self._history_listeners.remove(cb)
        except ValueError:
            pass

    def _notify_history_changed(self) -> None:
        for cb in list(self._history_listeners):
            try:
                cb()
            except Exception:
                pass

    def reload_from_config(self, config: dict) -> None:
        self.llm_engine.set_full_config(config)
        self.llm_engine.apply_config(config.get("llm", {}) or {})
        self.tts_engine.apply_config(config.get("tts", {}) or {})

    def set_mcp_client(self, client) -> None:
        self.llm_engine.set_mcp_client(client)

    def clear_chat_history(self) -> None:
        self._chat_history.clear()

    def set_chat_history(self, messages: list[dict[str, str]]) -> None:
        self._chat_history.clear()
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip()
            content = str(m.get("content") or "")
            if role in ("user", "assistant") and content.strip():
                self._chat_history.append({"role": role, "content": content})
        self._trim_history()

    def history_snapshot(self) -> list[dict[str, str]]:
        return list(self._chat_history)

    def commit_user_exchange_if_ok(self, user_text: str, assistant_full: str) -> None:
        """스트리밍 종료 후, 오류 응답이 아니면 히스토리에 user/assistant 쌍을 추가합니다."""
        user_text = (user_text or "").strip()
        assistant_full = (assistant_full or "").strip()
        if not user_text or not assistant_full:
            return
        if assistant_full.startswith("[LLM]") or assistant_full.startswith("[오류]"):
            return
        fc = getattr(self.llm_engine, "_full_config", {}) or {}
        assistant_plain = _assistant_content_for_history(assistant_full, fc)
        self._chat_history.append({"role": "user", "content": user_text})
        self._chat_history.append(
            {"role": "assistant", "content": assistant_plain}
        )
        self._trim_history()
        self._notify_history_changed()

    def _trim_history(self) -> None:
        max_msgs = _MAX_HISTORY_PAIRS * 2
        while len(self._chat_history) > max_msgs:
            del self._chat_history[0:2]

    def process_user_input(
        self,
        text: str,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ):
        text = (text or "").strip()
        atts = list(attachments or [])
        history_snapshot = list(self._chat_history)
        hist_user = format_user_text_for_history(text, atts)

        response_text = self.llm_engine.generate_response(
            text,
            history=history_snapshot,
            attachments=atts or None,
        )

        fc = getattr(self.llm_engine, "_full_config", {}) or {}
        for_tts = strip_thinking_mode_answer_only(response_text, fc)
        tts_text = assistant_history_plain(for_tts, fc).strip()
        if not response_text.startswith("[LLM]"):
            self._chat_history.append({"role": "user", "content": hist_user})
            self._chat_history.append(
                {
                    "role": "assistant",
                    "content": _assistant_content_for_history(response_text, fc),
                }
            )
            self._trim_history()
            self._notify_history_changed()

        audio_data = self.tts_engine.generate_audio(tts_text)
        return response_text, audio_data
