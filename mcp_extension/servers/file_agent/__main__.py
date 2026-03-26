"""
프로젝트 루트 기준 파일 에이전트 MCP 서버 (file_agent).

``workspace/`` 이하: 소스코드(.py, .cpp 등), 문서(.md, .txt), 설정(.json, .yaml),
오피스/바이너리(.xlsx, .pdf, 이미지 등) 생성·수정·삭제·목록.
바이너리는 읽기 시 base64 블록, 쓰기 시 ``content_base64=True``.

실행: 프로젝트 루트가 cwd인 상태에서
``python -m mcp_extension.servers.file_agent``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from core.workspace_file_ops import (
    WorkspacePathError,
    delete_path,
    list_workspace_entries,
    read_file,
    write_file,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daon-file-agent")

mcp = FastMCP("daon-file-agent")


def _repo_root() -> str:
    return str(Path.cwd().resolve())


def _coerce_bool(v: Any, default: bool = False) -> bool:
    """JSON에서 \"false\" 문자열이 오면 bool(v)가 True가 되는 실수를 막습니다."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("false", "0", "no", "off", ""):
            return False
        if s in ("true", "1", "yes", "on"):
            return True
    return default


@mcp.tool()
def workspace_read(path: str) -> str:
    """
    허용된 경로의 파일을 읽습니다.
    - 일반 텍스트(코드, 마크다운, JSON 등): UTF-8 문자열 그대로.
    - 엑셀/PDF/이미지 등: 첫 줄 ``[DAON_FILE_AGENT_BASE64]`` 다음 줄에 base64 한 덩어리.
    path: ``workspace/...`` 권장. 접두어 없이 ``kimchi_benefits.txt`` 만 주면 ``workspace/`` 아래로 처리됩니다.
    """
    try:
        return read_file(_repo_root(), path)
    except WorkspacePathError as e:
        return f"[오류] {e}"
    except OSError as e:
        return f"[오류] {e}"


@mcp.tool()
def workspace_write(path: str, content: str, content_base64: bool = False) -> str:
    """
    파일을 생성하거나 덮어씁니다. 중간 폴더는 자동 생성.
    - ``content_base64=False``(기본): UTF-8 텍스트(.txt, .md, .csv, .json 등).
    - ``.xlsx``: UTF-8 텍스트만 넣어도 됨(구분자 있으면 표, 없으면 줄마다 A열; 별도 패키지 불필요).
      실제 xlsx 바이너리는 ``content_base64=True`` + base64.
    - ``.pdf``/이미지 등 그 외 바이너리: ``content_base64=True`` + base64(또는 read 블록).
    path 규칙은 ``workspace_read``와 동일합니다.
    """
    try:
        return write_file(
            _repo_root(),
            path,
            content,
            content_base64=_coerce_bool(content_base64, False),
        )
    except WorkspacePathError as e:
        return f"[오류] {e}"
    except OSError as e:
        return f"[오류] {e}"


@mcp.tool()
def workspace_delete(path: str) -> str:
    """파일만 삭제합니다(폴더 삭제 불가). path 규칙은 workspace_read와 동일."""
    try:
        return delete_path(_repo_root(), path)
    except WorkspacePathError as e:
        return f"[오류] {e}"
    except OSError as e:
        return f"[오류] {e}"


@mcp.tool()
def workspace_list(relative_dir: str = "") -> str:
    """
    workspace 폴더 안의 항목을 나열합니다.
    relative_dir: workspace 기준 하위 경로(비우면 workspace 루트).
    예: 빈 문자열(루트), ``notes``, ``sub/project``
    """
    try:
        return list_workspace_entries(_repo_root(), relative_dir)
    except WorkspacePathError as e:
        return f"[오류] {e}"
    except OSError as e:
        return f"[오류] {e}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
