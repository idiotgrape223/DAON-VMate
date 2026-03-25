"""Live2D 감정 태그(emotionMap)·모션 그룹 오버레이 (model_dict 대신 폴더별 JSON)."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Optional

_SETTINGS_PREFIX = "daon_"
_SETTINGS_SUFFIX = "_expression_settings.json"

_overlay_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def expression_settings_path(repo_root: str, folder_name: str) -> str:
    fn = f"{_SETTINGS_PREFIX}{folder_name}{_SETTINGS_SUFFIX}"
    return os.path.normpath(
        os.path.join(repo_root, "assets", "live2d-models", folder_name, fn)
    )


def clear_expression_settings_cache(folder_name: Optional[str] = None) -> None:
    if folder_name is None:
        _overlay_cache.clear()
        return
    key = (folder_name or "").strip().lower()
    drop = [k for k in _overlay_cache if k.lower() == key]
    for k in drop:
        del _overlay_cache[k]


def _coerce_index(v: Any) -> Optional[int]:
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


def normalize_emotion_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        key = k.strip().lower()
        if not key:
            continue
        iv = _coerce_index(v)
        if iv is not None and iv >= 0:
            out[key] = iv
    return out


def load_expression_overlay(repo_root: str, folder_name: str) -> dict[str, Any]:
    folder = (folder_name or "").strip()
    if not folder:
        return {}
    path = expression_settings_path(repo_root, folder)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cache_key = path
    hit = _overlay_cache.get(cache_key)
    if hit is not None and hit[0] == mtime:
        return dict(hit[1])
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        _overlay_cache[cache_key] = (mtime, {})
        return {}
    if not isinstance(raw, dict):
        return {}
    _overlay_cache[cache_key] = (mtime, raw)
    return dict(raw)


def save_expression_overlay(
    repo_root: str,
    folder_name: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    folder = (folder_name or "").strip()
    if not folder:
        return False, "모델 폴더 이름이 비어 있습니다."
    base = os.path.join(repo_root, "assets", "live2d-models", folder)
    if not os.path.isdir(base):
        return False, f"모델 폴더가 없습니다: {base}"
    path = expression_settings_path(repo_root, folder)
    if not payload:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            return False, str(e)
        clear_expression_settings_cache(folder)
        return True, f"오버레이 제거됨: {path}"
    out = {"version": int(payload.get("version", 1))}
    if "emotionMap" in payload:
        em = normalize_emotion_map(payload.get("emotionMap"))
        out["emotionMap"] = em
    if "emotionMotionGroup" in payload:
        g = payload.get("emotionMotionGroup")
        if isinstance(g, str):
            out["emotionMotionGroup"] = g
    if "idleMotionGroupName" in payload:
        g = payload.get("idleMotionGroupName")
        if isinstance(g, str):
            out["idleMotionGroupName"] = g
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return False, str(e)
    clear_expression_settings_cache(folder)
    return True, path
