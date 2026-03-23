"""MCP 서버 JSON 로드 및 mcp_extension/servers/*/fragment 병합 (순수 stdlib)."""

from __future__ import annotations

import json
import os
from typing import Any

from . import FRAGMENT_FILENAME, SERVERS_SUBDIR


def _mcp_servers_dict(doc: dict[str, Any]) -> dict[str, Any]:
    if "mcpServers" in doc and isinstance(doc["mcpServers"], dict):
        return doc["mcpServers"]
    if "mcp_servers" in doc and isinstance(doc["mcp_servers"], dict):
        return doc["mcp_servers"]
    return {}


def load_mcp_servers_file(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return _mcp_servers_dict(doc)


def _fragment_paths(repo_root: str) -> list[str]:
    base = os.path.normpath(os.path.join(repo_root, "mcp_extension", SERVERS_SUBDIR))
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for name in sorted(os.listdir(base)):
        sub = os.path.join(base, name)
        if not os.path.isdir(sub):
            continue
        frag = os.path.join(sub, FRAGMENT_FILENAME)
        if os.path.isfile(frag):
            out.append(frag)
    return out


def load_merged_mcp_servers(repo_root: str, primary_config_path: str) -> dict[str, Any]:
    """
    1) primary_config_path의 서버 정의
    2) mcp_extension/servers/<각 폴더>/mcp_servers.fragment.json 을 알파벳 순으로 병합
    동일 서버 이름이면 뒤(프래그먼트)가 덮어씁니다.
    """
    merged: dict[str, Any] = dict(load_mcp_servers_file(primary_config_path))
    for frag in _fragment_paths(repo_root):
        extra = load_mcp_servers_file(frag)
        merged.update(extra)
    return merged
