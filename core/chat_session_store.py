"""모델(Live2D 폴더)별 채팅 세션 JSON 저장."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any

SESSION_SUBDIR = "daon_chat_sessions"
LAST_ACTIVE_FILENAME = "_last_session.txt"
_SESSION_ID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.I)


def sessions_dir(repo_root: str, model_folder: str) -> str:
    return os.path.normpath(
        os.path.join(
            repo_root,
            "assets",
            "live2d-models",
            model_folder.strip(),
            SESSION_SUBDIR,
        )
    )


def default_session_title() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def ensure_sessions_dir(repo_root: str, model_folder: str) -> str:
    d = sessions_dir(repo_root, model_folder)
    os.makedirs(d, exist_ok=True)
    return d


def _session_path(repo_root: str, model_folder: str, session_id: str) -> str:
    sid = (session_id or "").strip()
    if not _SESSION_ID_RE.match(sid):
        raise ValueError("invalid session id")
    return os.path.join(sessions_dir(repo_root, model_folder), f"{sid}.json")


def read_last_active_session_id(repo_root: str, model_folder: str) -> str | None:
    folder = (model_folder or "").strip()
    if not folder:
        return None
    p = os.path.join(sessions_dir(repo_root, folder), LAST_ACTIVE_FILENAME)
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if raw and _SESSION_ID_RE.match(raw):
        sp = _session_path(repo_root, folder, raw)
        if os.path.isfile(sp):
            return raw
    return None


def write_last_active_session_id(
    repo_root: str, model_folder: str, session_id: str
) -> None:
    folder = (model_folder or "").strip()
    if not folder or not _SESSION_ID_RE.match((session_id or "").strip()):
        return
    base = ensure_sessions_dir(repo_root, folder)
    p = os.path.join(base, LAST_ACTIVE_FILENAME)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(session_id.strip())
    except OSError:
        pass


def list_sessions(repo_root: str, model_folder: str) -> list[dict[str, Any]]:
    folder = (model_folder or "").strip()
    out: list[dict[str, Any]] = []
    if not folder:
        return out
    d = sessions_dir(repo_root, folder)
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        sid = name[:-5]
        if not _SESSION_ID_RE.match(sid):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "id": sid,
                "title": str(data.get("title") or default_session_title()),
                "updated_at": str(data.get("updated_at") or ""),
                "path": path,
            }
        )

    def _sort_key(x: dict[str, Any]) -> str:
        return x.get("updated_at") or ""

    out.sort(key=_sort_key, reverse=True)
    return out


def load_session_messages(
    repo_root: str, model_folder: str, session_id: str
) -> tuple[list[dict[str, str]], str] | None:
    folder = (model_folder or "").strip()
    if not folder or not _SESSION_ID_RE.match((session_id or "").strip()):
        return None
    path = _session_path(repo_root, folder, session_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_msgs = data.get("messages")
    if not isinstance(raw_msgs, list):
        raw_msgs = []
    messages: list[dict[str, str]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip()
        content = str(m.get("content") or "")
        if role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": content})
    title = str(data.get("title") or default_session_title())
    return messages, title


def save_session(
    repo_root: str,
    model_folder: str,
    session_id: str,
    title: str,
    messages: list[dict[str, str]],
    *,
    created_at: str | None = None,
) -> bool:
    folder = (model_folder or "").strip()
    if not folder or not _SESSION_ID_RE.match((session_id or "").strip()):
        return False
    ensure_sessions_dir(repo_root, folder)
    path = _session_path(repo_root, folder, session_id)
    now = datetime.now().isoformat(timespec="seconds")
    prev_created = created_at
    if prev_created is None and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict) and isinstance(old.get("created_at"), str):
                prev_created = old["created_at"]
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    if not prev_created:
        prev_created = now
    payload = {
        "version": 1,
        "id": session_id.strip(),
        "title": (title or "").strip() or default_session_title(),
        "created_at": prev_created,
        "updated_at": now,
        "messages": messages,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True


def create_empty_session(
    repo_root: str, model_folder: str, title: str | None = None
) -> tuple[str, str]:
    folder = (model_folder or "").strip()
    if not folder:
        raise ValueError("model_folder empty")
    sid = str(uuid.uuid4())
    ensure_sessions_dir(repo_root, folder)
    t = (title or "").strip() or default_session_title()
    save_session(
        repo_root,
        folder,
        sid,
        t,
        [],
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    write_last_active_session_id(repo_root, folder, sid)
    return sid, t


def delete_session(repo_root: str, model_folder: str, session_id: str) -> bool:
    folder = (model_folder or "").strip()
    if not folder or not _SESSION_ID_RE.match((session_id or "").strip()):
        return False
    path = _session_path(repo_root, folder, session_id)
    try:
        os.remove(path)
    except OSError:
        return False
    last = read_last_active_session_id(repo_root, folder)
    if last == session_id.strip():
        lp = os.path.join(sessions_dir(repo_root, folder), LAST_ACTIVE_FILENAME)
        try:
            os.remove(lp)
        except OSError:
            pass
    return True


def rename_session(
    repo_root: str, model_folder: str, session_id: str, new_title: str
) -> bool:
    folder = (model_folder or "").strip()
    if not folder or not _SESSION_ID_RE.match((session_id or "").strip()):
        return False
    path = _session_path(repo_root, folder, session_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    raw_msgs = data.get("messages")
    if not isinstance(raw_msgs, list):
        raw_msgs = []
    messages: list[dict[str, str]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip()
        content = str(m.get("content") or "")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": content})
    nt = (new_title or "").strip() or default_session_title()
    created = str(data.get("created_at") or "")
    return save_session(
        repo_root, folder, session_id, nt, messages, created_at=created or None
    )
