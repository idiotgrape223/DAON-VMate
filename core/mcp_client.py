"""
Model Context Protocol(MCP) stdio 클라이언트.

서버 목록은 ``mcp_extension/loader.load_merged_mcp_servers``로 로드합니다
(기본 JSON + ``mcp_extension/servers/*/mcp_servers.fragment.json`` 병합).
Qt 메인 스레드에서는 call_tool / list_all_tools 만 동기 호출하면 됩니다.
"""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp import types as mcp_types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcp_extension import DEFAULT_MCP_SERVERS_CONFIG_FILE
from mcp_extension.loader import load_merged_mcp_servers


@dataclass
class MCPToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class MCPClientService:
    """백그라운드 asyncio 루프에서 MCP stdio 서버들과 통신합니다."""

    def __init__(self, repo_root: str) -> None:
        self._repo_root = repo_root
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._server_errors: dict[str, str] = {}
        self._lock = threading.Lock()
        self._config_enabled = False
        self._config_path = ""

    def apply_config(self, config: dict[str, Any]) -> None:
        mcp = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
        enabled = bool(mcp.get("enabled", False))
        rel = str(mcp.get("config_file") or DEFAULT_MCP_SERVERS_CONFIG_FILE).strip()
        path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(self._repo_root, rel))
        with self._lock:
            need_restart = (
                enabled != self._config_enabled
                or path != self._config_path
            )
            self._config_enabled = enabled
            self._config_path = path
        if not enabled:
            self.stop()
            return
        if need_restart or self._thread is None or not self._thread.is_alive():
            self.stop()
            self._start_thread(path)

    def stop(self) -> None:
        loop = self._loop
        stop_ev = self._stop_event
        th = self._thread
        if loop is not None and stop_ev is not None and not stop_ev.is_set():
            fut = asyncio.run_coroutine_threadsafe(self._set_stop(stop_ev), loop)
            try:
                fut.result(timeout=5.0)
            except Exception:
                pass
        if th is not None and th.is_alive():
            th.join(timeout=8.0)
        self._thread = None
        self._loop = None
        self._stop_event = None
        with self._lock:
            self._sessions.clear()
            self._server_errors.clear()

    @staticmethod
    async def _set_stop(ev: asyncio.Event) -> None:
        ev.set()

    def _start_thread(self, mcp_json_path: str) -> None:
        servers = load_merged_mcp_servers(self._repo_root, mcp_json_path)
        if not servers:
            return

        def thread_main() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._run_servers(servers))
            finally:
                loop.close()
                self._loop = None

        self._thread = threading.Thread(target=thread_main, name="mcp-client", daemon=True)
        self._thread.start()

    async def _run_servers(self, servers: dict[str, Any]) -> None:
        self._stop_event = asyncio.Event()
        stop = self._stop_event
        self._sessions.clear()
        self._server_errors.clear()

        tasks: list[asyncio.Task[None]] = []
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            cmd = str(spec.get("command") or "").strip()
            if not cmd:
                self._server_errors[str(name)] = "missing command"
                continue
            args = spec.get("args")
            if args is None:
                arg_list: list[str] = []
            elif isinstance(args, list):
                arg_list = [str(a) for a in args]
            else:
                arg_list = [str(args)]
            env = spec.get("env")
            env_dict = dict(env) if isinstance(env, dict) else None
            cwd = spec.get("cwd")
            cwd_path = str(cwd).strip() if cwd else None
            if cwd_path and not os.path.isabs(cwd_path):
                cwd_path = os.path.normpath(os.path.join(self._repo_root, cwd_path))
            try:
                params = StdioServerParameters(
                    command=cmd,
                    args=arg_list,
                    env=env_dict,
                    cwd=cwd_path,
                )
            except Exception as e:
                self._server_errors[str(name)] = str(e)
                continue
            tasks.append(
                asyncio.create_task(
                    self._one_server(str(name), params),
                    name=f"mcp:{name}",
                )
            )

        if not tasks:
            return

        await stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _one_server(self, name: str, params: StdioServerParameters) -> None:
        try:
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    with self._lock:
                        self._sessions[name] = session
                    assert self._stop_event is not None
                    await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            with self._lock:
                self._server_errors[name] = str(e)
        finally:
            with self._lock:
                self._sessions.pop(name, None)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status_summary(self) -> str:
        with self._lock:
            n_sess = len(self._sessions)
            errs = dict(self._server_errors)
        if not self._config_enabled:
            return "MCP: 꺼짐 (설정에서 연동 사용 켜기)"
        if not self.is_running():
            if errs:
                return "MCP: 시작 실패 — " + "; ".join(f"{k}: {v}" for k, v in errs.items())
            return "MCP: 대기 중 (서버 정의 없음 또는 종료됨)"
        parts = [f"연결 서버 {n_sess}개"]
        if errs:
            parts.append("오류: " + "; ".join(f"{k}: {v}" for k, v in errs.items()))
        return "MCP: " + ", ".join(parts)

    def list_all_tools_sync(self, timeout: float = 60.0) -> list[MCPToolInfo]:
        loop = self._loop
        if loop is None:
            return []
        fut = asyncio.run_coroutine_threadsafe(self._list_all_tools_async(), loop)
        try:
            return fut.result(timeout=timeout)
        except Exception:
            return []

    async def _list_all_tools_async(self) -> list[MCPToolInfo]:
        out: list[MCPToolInfo] = []
        with self._lock:
            snap = list(self._sessions.items())
        for server_name, session in snap:
            try:
                res = await session.list_tools()
            except Exception:
                continue
            for t in res.tools:
                desc = (t.description or "").strip()
                schema = t.inputSchema if isinstance(t.inputSchema, dict) else {}
                out.append(
                    MCPToolInfo(
                        server=server_name,
                        name=t.name,
                        description=desc,
                        input_schema=schema,
                    )
                )
        return out

    def call_tool_sync(
        self,
        server: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 120.0,
    ) -> mcp_types.CallToolResult:
        loop = self._loop
        if loop is None:
            raise RuntimeError("MCP 클라이언트가 실행 중이 아닙니다.")
        with self._lock:
            if server not in self._sessions:
                raise KeyError(f"서버 없음: {server}")
        fut = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(server, tool_name, arguments or {}),
            loop,
        )
        return fut.result(timeout=timeout)

    async def _call_tool_async(
        self, server: str, tool_name: str, arguments: dict[str, Any]
    ) -> mcp_types.CallToolResult:
        with self._lock:
            session = self._sessions.get(server)
        if session is None:
            raise KeyError(server)
        return await session.call_tool(tool_name, arguments)

    def call_tool_result_to_text(self, result: mcp_types.CallToolResult) -> str:
        parts: list[str] = []
        for c in result.content or []:
            if isinstance(c, mcp_types.TextContent):
                parts.append(c.text)
            elif isinstance(c, mcp_types.ImageContent):
                parts.append(f"[image {c.mimeType}]")
            elif isinstance(c, mcp_types.EmbeddedResource):
                parts.append("[embedded resource]")
            else:
                parts.append(str(c))
        return "\n".join(parts).strip() or "(빈 도구 결과)"
