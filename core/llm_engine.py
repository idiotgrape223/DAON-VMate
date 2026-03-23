from __future__ import annotations

import json
from typing import Any, Iterator, Optional, TYPE_CHECKING

import requests

from config.llm_defaults import default_llm_api_url_for_provider
from core.llm_attachments import (
    LLMMediaAttachment,
    MAX_LLM_ATTACHMENTS,
    build_openai_user_message,
    build_ollama_user_message,
)
from core.llm_mcp_tools import (
    build_mcp_tools_prompt_suffix,
    execute_mcp_calls,
    inject_system_suffix,
    parse_mcp_calls_from_text,
)

if TYPE_CHECKING:
    from core.mcp_client import MCPClientService

# settings.yaml의 system_prompt 앞에 API 전달 시 항상 붙는 고정 안내
CORE_SYSTEM_PROMPT_PREFIX = """## Core System Prompt
Do not use emoticons, emojis, or Markdown.
When responding, you must prefix every sentence with a tag in brackets if an emotion is felt, such as [joy], [sadness], or [neutral]. Use only the tag names listed in the following system instructions. Select one from: [neutral], [joy], [sadness], [anger], [fear], [disgust], [surprise], [smirk]. Example: [joy] The weather is great today!"""


class LLMEngine:
    """
    - ollama: POST /api/chat
    - openai_compatible / lm_studio / custom: OpenAI Chat Completions 호환 POST .../v1/chat/completions
    """

    _OPENAI_STYLE_PROVIDERS = frozenset(
        {"openai_compatible", "lm_studio", "custom"}
    )

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self._full_config: dict[str, Any] = dict(config) if config else {}
        llm = self._full_config.get("llm", {}) or {}
        self.provider = llm.get("provider", "ollama")
        self.model_name = llm.get("model", "llama3")
        if self.provider == "custom":
            self.api_url = (
                str(llm.get("api_url", "") or "").strip()
                or "http://127.0.0.1:8000/v1"
            )
        else:
            self.api_url = default_llm_api_url_for_provider(self.provider)
        self.api_key = str(llm.get("api_key", "") or "").strip()
        self.temperature = float(llm.get("temperature", 0.7))
        self.max_tokens = int(llm.get("max_tokens", 2048))
        self.system_prompt = str(llm.get("system_prompt", "")).strip()
        self.request_timeout_sec = float(llm.get("request_timeout_sec", 120))
        self.stream_enabled = bool(llm.get("stream_enabled", True))
        self.stream_batch_min_chars = int(llm.get("stream_batch_min_chars", 8))
        self.stream_batch_max_chars = int(llm.get("stream_batch_max_chars", 56))
        self.use_mcp_tools = bool(llm.get("use_mcp_tools", False))
        self.mcp_max_rounds = int(llm.get("mcp_max_rounds", 8))
        self._mcp_client: MCPClientService | None = None

    def set_mcp_client(self, client: Optional["MCPClientService"]) -> None:
        self._mcp_client = client

    def set_full_config(self, config: Optional[dict[str, Any]]) -> None:
        """live2d 등 비-llm 키를 시스템 프롬프트(감정 태그 안내)에 반영하기 위해 전체 설정을 보관합니다."""
        self._full_config = dict(config) if config else {}

    def _effective_system_prompt(self) -> str:
        base = (self.system_prompt or "").strip()
        fc = self._full_config or {}
        llm_sec = fc.get("llm") or {}
        live = fc.get("live2d") or {}
        if not bool(llm_sec.get("use_emotion_tags", True)):
            body = base
        elif not bool(live.get("auto_emotion_from_assistant", True)):
            body = base
        else:
            from core.live2d_emotion_tags import (
                build_emo_map_from_profile,
                emotion_tags_prompt_instruction,
            )
            from core.model_profile import profile_for_folder

            folder = str(live.get("model_folder", "") or "").strip()
            prof = profile_for_folder(folder)
            em = build_emo_map_from_profile(prof)
            extra = emotion_tags_prompt_instruction(em)
            if not extra:
                body = base
            elif base:
                body = f"{base}{extra}"
            else:
                body = extra.lstrip()

        core = CORE_SYSTEM_PROMPT_PREFIX.rstrip()
        rest = (body or "").strip()
        if rest:
            return f"{core}\n\n{rest}"
        return core

    def apply_config(self, llm: dict) -> None:
        if not llm:
            return
        self.provider = llm.get("provider", self.provider)
        self.model_name = llm.get("model", self.model_name)
        if self.provider == "custom":
            if "api_url" in llm:
                u = str(llm.get("api_url") or "").strip()
                if u:
                    self.api_url = u
        else:
            self.api_url = default_llm_api_url_for_provider(self.provider)
        if "api_key" in llm:
            self.api_key = str(llm.get("api_key") or "").strip()
        self.temperature = float(llm.get("temperature", self.temperature))
        self.max_tokens = int(llm.get("max_tokens", self.max_tokens))
        if "system_prompt" in llm:
            self.system_prompt = str(llm["system_prompt"] or "").strip()
        if "request_timeout_sec" in llm:
            self.request_timeout_sec = float(llm["request_timeout_sec"])
        if "stream_enabled" in llm:
            self.stream_enabled = bool(llm["stream_enabled"])
        if "stream_batch_min_chars" in llm:
            self.stream_batch_min_chars = int(llm["stream_batch_min_chars"])
        if "stream_batch_max_chars" in llm:
            self.stream_batch_max_chars = int(llm["stream_batch_max_chars"])
        if "use_mcp_tools" in llm:
            self.use_mcp_tools = bool(llm["use_mcp_tools"])
        if "mcp_max_rounds" in llm:
            self.mcp_max_rounds = max(1, min(32, int(llm["mcp_max_rounds"])))

    def _http_stream_timeout(self) -> tuple[float, float]:
        """
        스트리밍 POST용 (연결 타임아웃, 읽기 idle 타임아웃).
        단일 timeout=으로 쓰면 토큰·청크 사이 추론 지연이 길 때 중간에 ReadTimeout으로 끊길 수 있음.
        """
        conn = float(min(30.0, max(10.0, self.request_timeout_sec / 4.0)))
        read_idle = float(max(300.0, self.request_timeout_sec * 3.0))
        return (conn, read_idle)

    def _is_openai_style(self) -> bool:
        return self.provider in self._OPENAI_STYLE_PROVIDERS

    def _openai_api_url_looks_like_ollama(self) -> bool:
        u = self.api_url.strip().lower()
        return "/api/generate" in u or "/api/chat" in u or ":11434" in u

    def _openai_style_config_error(self) -> Optional[str]:
        """openai_compatible 등인데 URL이 Ollama용이면 잘못된 /v1/chat/completions 조합이 됨."""
        if not self._is_openai_style():
            return None
        if self._openai_api_url_looks_like_ollama():
            return (
                "[LLM] 설정 오류: 제공자는 OpenAI 호환인데 API URL이 Ollama 주소입니다. "
                "LLM 탭에서 API URL을 https://api.openai.com 또는 https://api.openai.com/v1 "
                "(또는 사용 중인 호환 서비스 베이스 URL)로 바꾸세요."
            )
        return None

    @staticmethod
    def _text_from_openai_content(content: Any) -> str:
        """message.content 또는 delta.content: str 또는 content-part 배열."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif "text" in block:
                    parts.append(str(block.get("text", "")))
            return "".join(parts)
        return str(content)

    def _is_reasoning_style_model(self) -> bool:
        """일부 모델은 temperature 미지원, max_completion_tokens 권장."""
        m = (self.model_name or "").strip().lower()
        return any(
            m.startswith(p)
            for p in (
                "o1",
                "o3",
                "o4-mini",
                "gpt-5",
                "o4",
            )
        )

    def _openai_chat_payload(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name.strip(),
            "messages": messages,
            "stream": stream,
        }
        if self._is_reasoning_style_model():
            payload["max_completion_tokens"] = self.max_tokens
        else:
            payload["temperature"] = self.temperature
            payload["max_tokens"] = self.max_tokens
        return payload

    def _openai_chat_url(self) -> str:
        raw = self.api_url.strip().split("?")[0].rstrip("/")
        if raw.endswith("/chat/completions"):
            return raw
        if raw.endswith("/v1"):
            return f"{raw}/chat/completions"
        return f"{raw}/v1/chat/completions"

    def _openai_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _ollama_chat_url(self) -> str:
        raw = self.api_url.strip().split("?")[0].rstrip("/")
        if raw.endswith("/api/chat"):
            return raw
        if "/api/generate" in raw:
            return raw.replace("/api/generate", "/api/chat", 1)
        return raw + "/api/chat"

    def _normalize_attachments(
        self, attachments: Optional[list[LLMMediaAttachment]]
    ) -> list[LLMMediaAttachment]:
        if not attachments:
            return []
        return list(attachments[:MAX_LLM_ATTACHMENTS])

    def _messages_for_chat(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]],
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        sys_text = self._effective_system_prompt()
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        if history:
            for m in history:
                role = m.get("role", "")
                content = (m.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        atts = self._normalize_attachments(attachments)
        if self.provider == "ollama":
            messages.append(build_ollama_user_message(user_text, atts))
        elif self._is_openai_style():
            messages.append(build_openai_user_message(user_text, atts))
        else:
            messages.append({"role": "user", "content": (user_text or "").strip()})
        return messages

    def _call_ollama_chat(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> str:
        if not self.model_name.strip():
            return "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."

        url = self._ollama_chat_url()
        messages = self._messages_for_chat(user_text, history, attachments)

        payload: dict[str, Any] = {
            "model": self.model_name.strip(),
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.request_timeout_sec,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.ConnectionError:
            return (
                "[LLM] Ollama 서버에 연결할 수 없습니다. "
                "Ollama가 실행 중인지, 주소가 맞는지 확인하세요."
            )
        except requests.exceptions.Timeout:
            return "[LLM] 요청 시간이 초과되었습니다. 타임아웃을 늘리거나 모델을 확인하세요."
        except requests.exceptions.RequestException as e:
            return f"[LLM] 네트워크 오류: {e}"

        if resp.status_code != 200:
            try:
                err = resp.json()
                detail = err.get("error", resp.text)
            except Exception:
                detail = resp.text or str(resp.status_code)
            return f"[LLM] HTTP {resp.status_code}: {detail}"

        try:
            data = resp.json()
        except ValueError:
            return "[LLM] 응답 JSON을 해석할 수 없습니다."

        msg = data.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            text = "".join(parts).strip()
            if text:
                return text
        return "[LLM] 응답에 텍스트가 없습니다."

    def _stream_ollama_chat(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> Iterator[str]:
        """Ollama 스트림에서 message.content 델타를 순서대로보냅니다."""
        if not self.model_name.strip():
            yield "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."
            return

        url = self._ollama_chat_url()
        messages = self._messages_for_chat(user_text, history, attachments)

        payload: dict[str, Any] = {
            "model": self.model_name.strip(),
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        stream_to = self._http_stream_timeout()
        try:
            with requests.post(
                url,
                json=payload,
                timeout=stream_to,
                headers={"Content-Type": "application/json"},
                stream=True,
            ) as resp:
                if resp.status_code != 200:
                    try:
                        err = resp.json()
                        detail = err.get("error", resp.text)
                    except Exception:
                        detail = resp.text or str(resp.status_code)
                    yield f"[LLM] HTTP {resp.status_code}: {detail}"
                    return

                saw_done = False
                got_json_line = False
                try:
                    for line in resp.iter_lines(decode_unicode=False):
                        if not line:
                            continue
                        try:
                            if isinstance(line, bytes):
                                line = line.decode("utf-8", errors="replace")
                            data = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        got_json_line = True
                        msg = data.get("message") or {}
                        content = msg.get("content")
                        if isinstance(content, str) and content:
                            yield content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    t = block.get("text", "")
                                    if isinstance(t, str) and t:
                                        yield t
                        if data.get("done"):
                            saw_done = True
                            break
                except requests.exceptions.Timeout:
                    yield (
                        "\n[LLM] 스트림 수신 타임아웃: 토큰 사이 간격이 길면 이렇게 끊길 수 있습니다. "
                        "설정의 요청 타임아웃을 늘리거나 GPU/모델 부하를 확인하세요."
                    )
                    return
                except requests.exceptions.ChunkedEncodingError as e:
                    yield f"\n[LLM] 스트림 데이터가 중간에 끊겼습니다(청크 인코딩): {e}"
                    return
                except requests.exceptions.ConnectionError as e:
                    yield f"\n[LLM] 스트림 연결이 끊겼습니다: {e}"
                    return

                if got_json_line and not saw_done:
                    yield (
                        "\n[LLM] 서버가 완료(done) 신호 없이 스트림을 종료했습니다. "
                        "프록시·방화벽·Ollama 로그를 확인해 보세요."
                    )
        except requests.exceptions.ConnectionError:
            yield (
                "[LLM] Ollama 서버에 연결할 수 없습니다. "
                "Ollama가 실행 중인지, 주소가 맞는지 확인하세요."
            )
            return
        except requests.exceptions.Timeout:
            yield "[LLM] 연결 또는 첫 응답 대기 시간이 초과되었습니다."
            return
        except requests.exceptions.RequestException as e:
            yield f"[LLM] 네트워크 오류: {e}"
            return

    def _call_openai_chat(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> str:
        if not self.model_name.strip():
            return "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."

        cfg_err = self._openai_style_config_error()
        if cfg_err:
            return cfg_err

        url = self._openai_chat_url()
        if "api.openai.com" in url.lower() and not self.api_key.strip():
            return (
                "[LLM] api.openai.com 사용 시 API 키가 필요합니다. "
                "설정 LLM 탭의 API 키를 입력하세요."
            )

        messages = self._messages_for_chat(user_text, history, attachments)
        payload = self._openai_chat_payload(messages, stream=False)

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.request_timeout_sec,
                headers=self._openai_headers(),
            )
        except requests.exceptions.ConnectionError:
            return "[LLM] API 서버에 연결할 수 없습니다. URL과 서버 상태를 확인하세요."
        except requests.exceptions.Timeout:
            return "[LLM] 요청 시간이 초과되었습니다."
        except requests.exceptions.RequestException as e:
            return f"[LLM] 네트워크 오류: {e}"

        if resp.status_code != 200:
            try:
                err = resp.json()
                detail = err.get("error", {})
                if isinstance(detail, dict):
                    detail = detail.get("message", err)
                elif not detail:
                    detail = resp.text
            except Exception:
                detail = resp.text or str(resp.status_code)
            return f"[LLM] HTTP {resp.status_code}: {detail}"

        try:
            data = resp.json()
        except ValueError:
            return "[LLM] 응답 JSON을 해석할 수 없습니다."

        choices = data.get("choices") or []
        if not choices:
            return "[LLM] 응답에 choices가 없습니다."
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        text = self._text_from_openai_content(content).strip()
        if text:
            return text
        refusal = msg.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            return refusal.strip()
        return "[LLM] 응답에 텍스트가 없습니다."

    def _mcp_tools_active(self) -> bool:
        if not self.use_mcp_tools:
            return False
        fc = self._full_config.get("mcp") if isinstance(self._full_config.get("mcp"), dict) else {}
        if not bool(fc.get("enabled", False)):
            return False
        mc = self._mcp_client
        if mc is None or not mc.is_running():
            return False
        return True

    def _generate_from_messages(self, messages: list[dict[str, Any]]) -> str:
        """대화 메시지 배열로 비스트리밍 완성 응답(ollama / OpenAI 호환)."""
        if self.provider == "ollama":
            if not self.model_name.strip():
                return "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."
            url = self._ollama_chat_url()
            payload: dict[str, Any] = {
                "model": self.model_name.strip(),
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                },
            }
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    timeout=self.request_timeout_sec,
                    headers={"Content-Type": "application/json"},
                )
            except requests.exceptions.ConnectionError:
                return (
                    "[LLM] Ollama 서버에 연결할 수 없습니다. "
                    "Ollama가 실행 중인지, 주소가 맞는지 확인하세요."
                )
            except requests.exceptions.Timeout:
                return "[LLM] 요청 시간이 초과되었습니다."
            except requests.exceptions.RequestException as e:
                return f"[LLM] 네트워크 오류: {e}"
            if resp.status_code != 200:
                try:
                    err = resp.json()
                    detail = err.get("error", resp.text)
                except Exception:
                    detail = resp.text or str(resp.status_code)
                return f"[LLM] HTTP {resp.status_code}: {detail}"
            try:
                data = resp.json()
            except ValueError:
                return "[LLM] 응답 JSON을 해석할 수 없습니다."
            msg = data.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                text = "".join(parts).strip()
                if text:
                    return text
            return "[LLM] 응답에 텍스트가 없습니다."

        if self._is_openai_style():
            if not self.model_name.strip():
                return "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."
            cfg_err = self._openai_style_config_error()
            if cfg_err:
                return cfg_err
            url = self._openai_chat_url()
            if "api.openai.com" in url.lower() and not self.api_key.strip():
                return (
                    "[LLM] api.openai.com 사용 시 API 키가 필요합니다. "
                    "설정 LLM 탭의 API 키를 입력하세요."
                )
            payload = self._openai_chat_payload(messages, stream=False)
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    timeout=self.request_timeout_sec,
                    headers=self._openai_headers(),
                )
            except requests.exceptions.ConnectionError:
                return "[LLM] API 서버에 연결할 수 없습니다. URL과 서버 상태를 확인하세요."
            except requests.exceptions.Timeout:
                return "[LLM] 요청 시간이 초과되었습니다."
            except requests.exceptions.RequestException as e:
                return f"[LLM] 네트워크 오류: {e}"
            if resp.status_code != 200:
                try:
                    err = resp.json()
                    detail = err.get("error", {})
                    if isinstance(detail, dict):
                        detail = detail.get("message", err)
                    elif not detail:
                        detail = resp.text
                except Exception:
                    detail = resp.text or str(resp.status_code)
                return f"[LLM] HTTP {resp.status_code}: {detail}"
            try:
                data = resp.json()
            except ValueError:
                return "[LLM] 응답 JSON을 해석할 수 없습니다."
            choices = data.get("choices") or []
            if not choices:
                return "[LLM] 응답에 choices가 없습니다."
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            text = self._text_from_openai_content(content).strip()
            if text:
                return text
            refusal = msg.get("refusal")
            if isinstance(refusal, str) and refusal.strip():
                return refusal.strip()
            return "[LLM] 응답에 텍스트가 없습니다."

        return (
            f"[LLM] provider '{self.provider}' 는 MCP 도구 루프를 지원하지 않습니다. "
            "ollama 또는 OpenAI 호환 제공자를 선택하세요."
        )

    def _chat_with_mcp_tool_loop(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> str:
        assert self._mcp_client is not None
        messages = self._messages_for_chat(user_text, history, attachments)
        tools = self._mcp_client.list_all_tools_sync()
        suffix = build_mcp_tools_prompt_suffix(tools)
        messages = inject_system_suffix(messages, suffix)
        max_r = max(1, min(32, int(self.mcp_max_rounds)))

        for _ in range(max_r):
            reply = self._generate_from_messages(messages)
            if reply.startswith("[LLM]"):
                return reply
            _clean, calls = parse_mcp_calls_from_text(reply)
            if not calls:
                return _clean if _clean.strip() else reply.strip()
            messages.append({"role": "assistant", "content": reply.strip()})
            feedback = execute_mcp_calls(self._mcp_client, calls)
            messages.append({"role": "user", "content": feedback})

        return "[LLM] MCP 도구 호출 라운드 상한에 도달했습니다. 요청을 단순화해 보세요."

    @staticmethod
    def _pseudo_stream_chunks(text: str, size: int = 72) -> Iterator[str]:
        if not text:
            return
        s = max(8, min(256, int(size)))
        for i in range(0, len(text), s):
            yield text[i : i + s]

    def _stream_openai_chat(
        self,
        user_text: str,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> Iterator[str]:
        if not self.model_name.strip():
            yield "[LLM] 모델 이름이 비어 있습니다. 설정에서 모델을 지정하세요."
            return

        cfg_err = self._openai_style_config_error()
        if cfg_err:
            yield cfg_err
            return

        url = self._openai_chat_url()
        if "api.openai.com" in url.lower() and not self.api_key.strip():
            yield (
                "[LLM] api.openai.com 사용 시 API 키가 필요합니다. "
                "설정 LLM 탭의 API 키를 입력하세요."
            )
            return

        messages = self._messages_for_chat(user_text, history, attachments)
        payload = self._openai_chat_payload(messages, stream=True)

        stream_to = self._http_stream_timeout()
        try:
            with requests.post(
                url,
                json=payload,
                timeout=stream_to,
                headers=self._openai_headers(),
                stream=True,
            ) as resp:
                if resp.status_code != 200:
                    try:
                        err = resp.json()
                        detail = err.get("error", {})
                        if isinstance(detail, dict):
                            detail = detail.get("message", err)
                        elif not detail:
                            detail = resp.text
                    except Exception:
                        detail = resp.text or str(resp.status_code)
                    yield f"[LLM] HTTP {resp.status_code}: {detail}"
                    return

                saw_done = False
                got_event_line = False
                try:
                    for line in resp.iter_lines(decode_unicode=False):
                        if not line:
                            continue
                        try:
                            if isinstance(line, bytes):
                                line = line.decode("utf-8", errors="replace")
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if not line.lower().startswith("data:"):
                                continue
                            chunk = line[5:].lstrip()
                            if chunk == "[DONE]":
                                saw_done = True
                                break
                            data = json.loads(chunk)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        got_event_line = True
                        for choice in data.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            piece = self._text_from_openai_content(content)
                            if piece:
                                yield piece
                except requests.exceptions.Timeout:
                    yield (
                        "\n[LLM] 스트림 수신 타임아웃(SSE). 토큰 간격이 길거나 중간 장비가 연결을 끊었을 수 있습니다."
                    )
                    return
                except requests.exceptions.ChunkedEncodingError as e:
                    yield f"\n[LLM] 스트림이 비정상 종료되었습니다: {e}"
                    return
                except requests.exceptions.ConnectionError as e:
                    yield f"\n[LLM] 스트림 연결이 끊겼습니다: {e}"
                    return

                if got_event_line and not saw_done:
                    yield (
                        "\n[LLM] SSE가 [DONE] 없이 끝났습니다. "
                        "게이트웨이 타임아웃·프록시 버퍼 한도를 의심해 보세요."
                    )
        except requests.exceptions.ConnectionError:
            yield "[LLM] API 서버에 연결할 수 없습니다. URL과 서버 상태를 확인하세요."
            return
        except requests.exceptions.Timeout:
            yield "[LLM] 연결 또는 첫 응답 대기 시간이 초과되었습니다."
            return
        except requests.exceptions.RequestException as e:
            yield f"[LLM] 네트워크 오류: {e}"
            return

    def iter_chat_stream(
        self,
        user_text: str,
        *,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> Iterator[str]:
        user_text = (user_text or "").strip()
        atts = self._normalize_attachments(attachments)
        if not user_text and not atts:
            yield "[LLM] 입력이 비어 있습니다."
            return
        if self._mcp_tools_active():
            final = self._chat_with_mcp_tool_loop(
                user_text, history=history, attachments=atts or None
            )
            if final.startswith("[LLM]"):
                yield final
                return
            yield from self._pseudo_stream_chunks(final)
            return
        if self.provider == "ollama":
            yield from self._stream_ollama_chat(
                user_text, history=history, attachments=atts or None
            )
            return
        if self._is_openai_style():
            yield from self._stream_openai_chat(
                user_text, history=history, attachments=atts or None
            )
            return
        yield (
            f"[LLM] provider '{self.provider}' 스트리밍은 아직 지원하지 않습니다. "
            "ollama 또는 OpenAI 호환 제공자를 사용하세요."
        )

    def generate_response(
        self,
        prompt: str,
        *,
        history: Optional[list[dict[str, str]]] = None,
        attachments: Optional[list[LLMMediaAttachment]] = None,
    ) -> str:
        prompt = (prompt or "").strip()
        atts = self._normalize_attachments(attachments)
        if not prompt and not atts:
            return "[LLM] 입력이 비어 있습니다."

        if self._mcp_tools_active():
            return self._chat_with_mcp_tool_loop(
                prompt, history=history, attachments=atts or None
            )

        if self.provider == "ollama":
            return self._call_ollama_chat(
                prompt, history=history, attachments=atts or None
            )
        if self._is_openai_style():
            return self._call_openai_chat(
                prompt, history=history, attachments=atts or None
            )

        return (
            f"[LLM] provider '{self.provider}' 는 아직 지원하지 않습니다. "
            "ollama 또는 OpenAI 호환 제공자를 선택하세요."
        )
