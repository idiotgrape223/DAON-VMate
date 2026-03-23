"""감정 표정 적용 진단. ui.debug_emotion_log 또는 환경변수 DAON_VMATE_DEBUG_EMOTION=1 일 때 debug_emotion.log에 NDJSON."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

_LOG_BASENAME = "debug_emotion.log"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_last_apply_ts: float = 0.0
_APPLY_THROTTLE_SEC = 0.25


def emotion_debug_enabled(config: dict[str, Any] | None) -> bool:
    env = os.environ.get("DAON_VMATE_DEBUG_EMOTION", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if not isinstance(config, dict):
        return False
    return bool((config.get("ui") or {}).get("debug_emotion_log", False))


def _hypothesis_for_event(event: str) -> str:
    if event == "no_emo_map":
        return "H1_profile_map"
    if event == "no_chosen":
        return "H2_tags_neutral"
    if event in ("no_expressions", "oob_no_neutral", "oob_neutral_invalid"):
        return "H3_model_indices"
    return "H4_apply_setexpr"


def emotion_apply_debug_log(
    folder_key: str,
    payload: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> None:
    global _last_apply_ts
    if not emotion_debug_enabled(config):
        return
    ev = str(payload.get("event") or "")
    if ev == "apply":
        now = time.time()
        if now - _last_apply_ts < _APPLY_THROTTLE_SEC:
            return
        _last_apply_ts = now
    path = os.path.join(_REPO_ROOT, _LOG_BASENAME)
    hid = _hypothesis_for_event(ev)
    row = {
        "id": f"log_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
        "timestamp": int(time.time() * 1000),
        "location": "core/emotion_apply_debug_log.py:emotion_apply_debug_log",
        "message": ev or "emotion_debug",
        "hypothesisId": hid,
        "data": {"folder_key": folder_key, **payload},
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_emotion_apply_step(
    config: dict[str, Any] | None,
    folder_key: str,
    event: str,
    **data: Any,
) -> None:
    """apply_emotion_for_assistant_text 등에서 한 줄로 호출."""
    emotion_apply_debug_log(folder_key, {"event": event, **data}, config=config)
