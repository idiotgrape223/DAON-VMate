"""model_dict.json + 각 모델의 model3.json(FileReferences.Motions) 연동."""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DICT_PATH = os.path.join(_REPO_ROOT, "model_dict.json")

_cache_mtime: float | None = None
_cache_profiles: list[dict[str, Any]] = []

# model3.json 경로별: mtime -> { 그룹명: 모션 개수 }
_catalog_cache: dict[str, tuple[float, dict[str, int]]] = {}


def model_dict_path() -> str:
    return _MODEL_DICT_PATH


def repo_root() -> str:
    """프로젝트 루트(model_dict.json, assets/live2d-models 기준)."""
    return _REPO_ROOT


def _load_profiles_uncached() -> list[dict[str, Any]]:
    try:
        with open(_MODEL_DICT_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def load_profiles() -> list[dict[str, Any]]:
    global _cache_mtime, _cache_profiles
    try:
        mtime = os.path.getmtime(_MODEL_DICT_PATH)
    except OSError:
        _cache_mtime = None
        _cache_profiles = []
        return []
    if _cache_mtime == mtime and _cache_profiles:
        return _cache_profiles
    _cache_profiles = _load_profiles_uncached()
    _cache_mtime = mtime
    return _cache_profiles


def profile_for_folder(folder_name: str) -> Optional[dict[str, Any]]:
    key = (folder_name or "").strip().lower()
    if not key:
        return None
    profiles = load_profiles()
    for p in profiles:
        n = p.get("name")
        if isinstance(n, str) and n.strip().lower() == key:
            return p
    # 폴더명과 model_dict 의 name 이 다를 때: url 경로에 .../live2d-models/{folder}/ 가 있으면 매칭
    needle = f"/live2d-models/{key}/"
    for p in profiles:
        url = str(p.get("url") or "").replace("\\", "/").lower()
        if needle in url:
            return p
    return None


def _resolve_model3_path(folder_name: str) -> Optional[str]:
    """assets/live2d-models/{folder}/… 아래 model3.json (우선 runtime/{folder}.model3.json)."""
    fn = (folder_name or "").strip()
    if not fn:
        return None
    base = os.path.join(_REPO_ROOT, "assets", "live2d-models", fn)
    if not os.path.isdir(base):
        return None
    preferred = os.path.join(base, "runtime", f"{fn}.model3.json")
    if os.path.isfile(preferred):
        return preferred
    matches = sorted(glob.glob(os.path.join(base, "**", "*.model3.json"), recursive=True))
    if not matches:
        return None
    for p in matches:
        if p.replace("\\", "/").endswith(f"/runtime/{fn}.model3.json"):
            return p
    for p in matches:
        if os.path.basename(p) == f"{fn}.model3.json":
            return p
    return matches[0]


def model3_json_path_for_folder(folder_name: str) -> Optional[str]:
    """미리보기·검증용: 모델 폴더에 대응하는 .model3.json 절대 경로."""
    return _resolve_model3_path(folder_name)


def load_motion_catalog_for_folder(folder_name: str) -> dict[str, int]:
    """
    model3.json 의 FileReferences.Motions 에서 그룹명 -> 해당 그룹 모션 개수.
    Cubism 에서 빈 문자열 키 \"\" 그룹(기본 풀)도 그대로 반영.
    """
    path = _resolve_model3_path(folder_name)
    if not path:
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cache_key = path
    hit = _catalog_cache.get(cache_key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        _catalog_cache[cache_key] = (mtime, {})
        return {}
    motions = (data.get("FileReferences") or {}).get("Motions")
    out: dict[str, int] = {}
    if isinstance(motions, dict):
        for k, v in motions.items():
            if isinstance(v, list):
                out[str(k)] = len(v)
    _catalog_cache[cache_key] = (mtime, out)
    return out


def load_expression_catalog_for_folder(folder_name: str) -> list[tuple[int, str]]:
    """model3.json FileReferences.Expressions → (인덱스, Name) 목록 (표정 선택 UI용)."""
    path = _resolve_model3_path(folder_name)
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    exprs = (data.get("FileReferences") or {}).get("Expressions")
    out: list[tuple[int, str]] = []
    if not isinstance(exprs, list):
        return out
    for i, item in enumerate(exprs):
        if not isinstance(item, dict):
            continue
        name = item.get("Name") or item.get("name")
        label = str(name).strip() if name is not None else f"expr{i}"
        out.append((i, label))
    return out


def effective_profile_for_folder(folder_name: str) -> Optional[dict[str, Any]]:
    """
    model_dict 프로필 + assets/.../daon_{folder}_expression_settings.json 오버레이.
    오버레이에 emotionMap 키가 있으면 해당 dict로 표정 매핑을 통째로 교체합니다.
    """
    from core.live2d_expression_settings import (
        load_expression_overlay,
        normalize_emotion_map,
    )

    fn = (folder_name or "").strip()
    if not fn:
        return None
    base = profile_for_folder(fn)
    ov = load_expression_overlay(_REPO_ROOT, fn)
    if not base:
        if not ov:
            return None
        em: dict[str, int] = {}
        if "emotionMap" in ov:
            em = normalize_emotion_map(ov.get("emotionMap"))
        eg = ov.get("emotionMotionGroup")
        ig = ov.get("idleMotionGroupName")
        if (
            not em
            and "emotionMotionGroup" not in ov
            and "idleMotionGroupName" not in ov
        ):
            return None
        return {
            "name": fn,
            "emotionMap": em,
            "emotionMotionGroup": eg if isinstance(eg, str) else "",
            "idleMotionGroupName": ig if isinstance(ig, str) else "Idle",
        }
    out = dict(base)
    if "emotionMap" in ov:
        out["emotionMap"] = normalize_emotion_map(ov.get("emotionMap"))
    if "emotionMotionGroup" in ov and isinstance(ov.get("emotionMotionGroup"), str):
        out["emotionMotionGroup"] = ov["emotionMotionGroup"]
    if "idleMotionGroupName" in ov and isinstance(ov.get("idleMotionGroupName"), str):
        out["idleMotionGroupName"] = ov["idleMotionGroupName"]
    return out


def _clamp_index(idx: int, count: int) -> int:
    if count <= 0:
        return 0
    return max(0, min(int(idx), count - 1))


def _extract_motion_index(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        if isinstance(value.get(""), int):
            return int(value[""])
        for v in value.values():
            if isinstance(v, int):
                return int(v)
    return 0


def _pick_auto_tap_from_catalog(catalog: dict[str, int]) -> Optional[tuple[str, int]]:
    """model3.json 에 실제로 있는 그룹만 사용."""
    if not catalog:
        return None
    for name in ("Tap", "TapBody", "FlickUp", "TapHead"):
        n = catalog.get(name, 0)
        if n > 0:
            return (name, 0)
    for name in ("HitAreaBody", "HitAreaHead"):
        n = catalog.get(name, 0)
        if n > 0:
            return (name, 0)
    if catalog.get("") and catalog[""] > 0:
        return ("", 0)
    n_idle = catalog.get("Idle", 0)
    if n_idle > 0:
        return ("Idle", 0)
    for k in sorted(catalog.keys()):
        if catalog[k] > 0:
            return (k, 0)
    return None


def _profile_tap_candidates(profile: dict[str, Any]) -> list[tuple[str, int]]:
    """model_dict 만 보고 후보 (그룹명이 Motions 키와 일치해야 유효)."""
    out: list[tuple[str, int]] = []
    gt = profile.get("genericTap")
    if isinstance(gt, dict):
        g = gt.get("group")
        if g is None:
            g = gt.get("motionGroup")
        if isinstance(g, str):
            idx = gt.get("index", 0)
            out.append((g, int(idx) if isinstance(idx, int) else 0))
    tm = profile.get("tapMotions")
    if isinstance(tm, dict) and tm:
        prio = (
            "HitAreaBody",
            "TapBody",
            "Body",
            "HitAreaHead",
            "TapHead",
            "Head",
        )
        seen: set[str] = set()
        for k in prio:
            if k in tm:
                out.append((k, _extract_motion_index(tm[k])))
                seen.add(k)
        for k, v in tm.items():
            if isinstance(k, str) and k not in seen:
                out.append((k, _extract_motion_index(v)))
                seen.add(k)
    return out


def tap_motion_for_profile(
    profile: Optional[dict[str, Any]],
    catalog: dict[str, int],
) -> tuple[str, int]:
    """
    1) model_dict 의 genericTap / tapMotions — 그룹이 catalog 에 있을 때만 채택 (인덱스 클램프)
    2) 없으면 catalog 기반 자동 (Tap → … → \"\" → Idle …)
    3) catalog 가 비어 있으면 TapBody, 0
    """
    default = ("TapBody", 0)

    if profile:
        for g, idx in _profile_tap_candidates(profile):
            n = catalog.get(g, 0)
            if n > 0:
                return (g, _clamp_index(idx, n))

    auto = _pick_auto_tap_from_catalog(catalog)
    if auto is not None:
        g, idx = auto
        n = catalog.get(g, 0)
        if n > 0:
            return (g, _clamp_index(idx, n))
        return auto

    return default


def tap_motion_for_folder(folder_name: str) -> tuple[str, int]:
    catalog = load_motion_catalog_for_folder(folder_name)
    profile = profile_for_folder(folder_name)
    return tap_motion_for_profile(profile, catalog)


def emotion_motion_index(profile: Optional[dict[str, Any]], label: str) -> Optional[int]:
    if not profile or not label:
        return None
    em = profile.get("emotionMap")
    if not isinstance(em, dict):
        return None
    v = em.get(label.strip().lower())
    if v is None:
        v = em.get(label.strip())
    if isinstance(v, int):
        return v
    return None


def emotion_motion_for_folder(folder_name: str, label: str) -> Optional[int]:
    return emotion_motion_index(effective_profile_for_folder(folder_name), label)


def idle_motion_group(profile: Optional[dict[str, Any]]) -> str:
    if not profile:
        return "Idle"
    idle = profile.get("idleMotionGroupName")
    if isinstance(idle, str) and idle.strip():
        return idle.strip()
    return "Idle"


def emotion_motion_group_name(profile: Optional[dict[str, Any]]) -> str:
    """감정 인덱스가 붙는 모션 그룹 (기본 Idle, mao/Alexia 등은 \"\" 풀)."""
    if not profile:
        return "Idle"
    g = profile.get("emotionMotionGroup")
    if isinstance(g, str):
        return g
    return idle_motion_group(profile)


def play_emotion_motion(
    start_motion_cb,
    folder_name: str,
    emotion_label: str,
) -> bool:
    """
    emotionMap + emotionMotionGroup 로 StartMotion.
    라벨이 맵에 없으면 neutral 인덱스로 폴백.
    """
    prof = effective_profile_for_folder(folder_name)
    key = (emotion_label or "neutral").strip().lower()
    idx = emotion_motion_index(prof, key)
    if idx is None:
        idx = emotion_motion_index(prof, "neutral")
    if idx is None:
        return False
    grp = emotion_motion_group_name(prof)
    catalog = load_motion_catalog_for_folder(folder_name)
    n = catalog.get(grp, 0)
    if n <= 0:
        return False
    start_motion_cb(grp, _clamp_index(idx, n))
    return True
