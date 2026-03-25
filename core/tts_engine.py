from __future__ import annotations

import re
import sys
import time
from typing import Any, Optional, cast

import requests


class TTSEngine:
    """
    설정 기반 TTS.
    - gpt-sovits: GET/POST (기존)
    - edge-tts: Microsoft Edge 음성 (edge-tts 패키지)
    - openai_tts: OpenAI Speech API (또는 호환 엔드포인트)
    - elevenlabs: ElevenLabs 공식 Python SDK(우선) 또는 REST 폴백
    - custom: GET ?text= 또는 POST JSON {"text":...} 로 오디오 바이너리 수신
    """

    @staticmethod
    def edge_tts_dependency_status() -> tuple[bool, str]:
        """edge-tts 제공자 사용 시 필요한 패키지. (성공, 빈 문자열) 또는 (실패, 안내 문구)."""
        exe = sys.executable or "python"
        frozen = bool(getattr(sys, "frozen", False))

        def _pip_hint(extra: str) -> str:
            if frozen:
                return (
                    f"{extra}\n\n"
                    "현재는 패키징된 실행 파일로 동작 중이라, 일반적인 pip 설치 경로와 다를 수 있습니다."
                )
            q = '"' + exe.replace('"', r"\"") + '"'
            return (
                f"{extra}\n\n"
                "지금 이 앱이 사용 중인 Python:\n"
                f"  {exe}\n\n"
                "터미널에서 다른 `python`/`pip`로 설치하면 이 환경에는 반영되지 않습니다.\n"
                "위와 동일한 인터프리터로 설치하려면:\n"
                f"  {q} -m pip install edge-tts miniaudio"
            )

        try:
            import edge_tts  # noqa: F401
        except ImportError as e:
            return (
                False,
                _pip_hint(
                    "edge_tts 모듈을 불러올 수 없습니다 (pip 패키지 이름: edge-tts).\n"
                    f"원인: {e!s}"
                ),
            )
        except Exception as e:
            return (
                False,
                _pip_hint(
                    "edge_tts 로드 중 오류가 났습니다 (설치는 되었으나 실행 불가할 수 있음).\n"
                    f"원인: {type(e).__name__}: {e!s}"
                ),
            )
        try:
            import miniaudio  # noqa: F401
        except ImportError as e:
            return (
                False,
                _pip_hint(
                    "miniaudio 모듈이 없어 Edge TTS(MP3) 재생이 불가합니다.\n"
                    f"원인: {e!s}"
                ),
            )
        except Exception as e:
            return (
                False,
                _pip_hint(
                    "miniaudio 로드 중 오류가 났습니다.\n"
                    f"원인: {type(e).__name__}: {e!s}"
                ),
            )
        return True, ""

    @staticmethod
    def elevenlabs_dependency_status() -> tuple[bool, str]:
        """elevenlabs 제공자 사용 시 공식 SDK 패키지."""
        exe = sys.executable or "python"
        frozen = bool(getattr(sys, "frozen", False))

        def _pip_hint(extra: str) -> str:
            if frozen:
                return (
                    f"{extra}\n\n"
                    "현재는 패키징된 실행 파일로 동작 중이라, 일반적인 pip 설치 경로와 다를 수 있습니다."
                )
            q = '"' + exe.replace('"', r"\"") + '"'
            return (
                f"{extra}\n\n"
                "지금 이 앱이 사용 중인 Python:\n"
                f"  {exe}\n\n"
                "위와 동일한 인터프리터로 설치하려면:\n"
                f"  {q} -m pip install elevenlabs"
            )

        try:
            from elevenlabs.client import ElevenLabs  # noqa: F401
        except ImportError as e:
            return (
                False,
                _pip_hint(
                    "elevenlabs 모듈이 없습니다 (pip 패키지 이름: elevenlabs).\n"
                    f"원인: {e!s}"
                ),
            )
        except Exception as e:
            return (
                False,
                _pip_hint(
                    "elevenlabs 로드 중 오류가 났습니다.\n"
                    f"원인: {type(e).__name__}: {e!s}"
                ),
            )
        return True, ""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        tts = (config or {}).get("tts", {})
        self.provider = tts.get("provider", "gpt-sovits")
        self.api_url = tts.get("api_url", "http://127.0.0.1:9880/tts")
        okey = str(tts.get("openai_tts_api_key", "") or "").strip()
        if not okey:
            okey = str(tts.get("api_key", "") or "").strip()
        self.openai_tts_api_key = okey
        self.elevenlabs_api_key = str(tts.get("elevenlabs_api_key", "") or "").strip()
        self.character_name = tts.get("character_name", "daon")
        self.timeout_sec = int(tts.get("timeout_sec", 120))

        self.edge_voice = str(tts.get("edge_voice", "ko-KR-SunHiNeural"))
        self.openai_tts_model = str(tts.get("openai_tts_model", "tts-1"))
        self.openai_tts_voice = str(tts.get("openai_tts_voice", "nova"))
        self.elevenlabs_model = str(tts.get("elevenlabs_model", "eleven_multilingual_v2"))
        self.elevenlabs_voice_id = str(tts.get("elevenlabs_voice_id", ""))
        self.elevenlabs_api_base = str(tts.get("elevenlabs_api_base", "") or "")
        self.elevenlabs_output_format = str(
            tts.get("elevenlabs_output_format", "mp3_44100_128") or "mp3_44100_128"
        )

        # GPT-SoVITS
        self.text_lang = str(tts.get("text_lang", "ko"))
        self.ref_audio_path = str(tts.get("ref_audio_path", ""))
        self.prompt_lang = str(tts.get("prompt_lang", "ko"))
        self.prompt_text = str(tts.get("prompt_text", ""))
        self.text_split_method = str(tts.get("text_split_method", "cut5"))
        self.batch_size = str(tts.get("batch_size", "1"))
        self.media_type = str(tts.get("media_type", "wav"))
        self.streaming_mode = str(tts.get("streaming_mode", "false")).lower()

    def apply_config(self, tts: dict) -> None:
        if not tts:
            return
        self.provider = tts.get("provider", self.provider)
        self.api_url = tts.get("api_url", self.api_url)
        if "openai_tts_api_key" in tts:
            self.openai_tts_api_key = str(tts.get("openai_tts_api_key") or "").strip()
        elif "api_key" in tts:
            self.openai_tts_api_key = str(tts.get("api_key") or "").strip()
        if "elevenlabs_api_key" in tts:
            self.elevenlabs_api_key = str(tts.get("elevenlabs_api_key") or "").strip()
        self.character_name = tts.get("character_name", self.character_name)
        self.timeout_sec = int(tts.get("timeout_sec", self.timeout_sec))
        if "edge_voice" in tts:
            self.edge_voice = str(tts["edge_voice"])
        if "openai_tts_model" in tts:
            self.openai_tts_model = str(tts["openai_tts_model"])
        if "openai_tts_voice" in tts:
            self.openai_tts_voice = str(tts["openai_tts_voice"])
        if "elevenlabs_model" in tts:
            self.elevenlabs_model = str(tts["elevenlabs_model"])
        if "elevenlabs_voice_id" in tts:
            self.elevenlabs_voice_id = str(tts["elevenlabs_voice_id"])
        if "elevenlabs_api_base" in tts:
            self.elevenlabs_api_base = str(tts.get("elevenlabs_api_base") or "")
        if "elevenlabs_output_format" in tts:
            self.elevenlabs_output_format = str(
                tts.get("elevenlabs_output_format") or "mp3_44100_128"
            )
        if "text_lang" in tts:
            self.text_lang = str(tts["text_lang"])
        if "ref_audio_path" in tts:
            self.ref_audio_path = str(tts["ref_audio_path"])
        if "prompt_lang" in tts:
            self.prompt_lang = str(tts["prompt_lang"])
        if "prompt_text" in tts:
            self.prompt_text = str(tts["prompt_text"])
        if "text_split_method" in tts:
            self.text_split_method = str(tts["text_split_method"])
        if "batch_size" in tts:
            self.batch_size = str(tts["batch_size"])
        if "media_type" in tts:
            self.media_type = str(tts["media_type"])
        if "streaming_mode" in tts:
            self.streaming_mode = str(tts["streaming_mode"]).lower()

    def _should_skip_tts(self, text: str) -> bool:
        raw = (text or "").strip()
        return raw.startswith("[LLM]") or raw.startswith("[오류]")

    def _clean_text_for_tts(self, text: str) -> str:
        """대괄호 태그 제거. 전부 지워져 빈 문자열이면 원문으로 TTS(무음 스킵 방지)."""
        raw = (text or "").strip()
        if not raw:
            return ""
        cleaned = re.sub(r"\[.*?\]", "", raw).strip()
        return cleaned if cleaned else raw

    def _gpt_sovits_request(self, text: str) -> bytes:
        params = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio_path,
            "prompt_lang": self.prompt_lang,
            "prompt_text": self.prompt_text,
            "text_split_method": self.text_split_method,
            "batch_size": self.batch_size,
            "media_type": self.media_type,
            "streaming_mode": self.streaming_mode,
        }
        try:
            r = requests.get(
                self.api_url,
                params=params,
                timeout=self.timeout_sec,
            )
            if r.status_code == 405:
                r = requests.post(
                    self.api_url,
                    json=params,
                    timeout=self.timeout_sec,
                )
            if r.status_code != 200:
                return b""
            ct = (r.headers.get("content-type") or "").lower()
            if "application/json" in ct or "text/html" in ct:
                return b""
            return r.content if r.content else b""
        except requests.RequestException:
            return b""

    def _edge_tts_request_once(self, text: str) -> bytes:
        try:
            import edge_tts
        except ImportError:
            return b""

        voice = (self.edge_voice or "ko-KR-SunHiNeural").strip()
        recv_to = max(90, int(self.timeout_sec))
        communicate = edge_tts.Communicate(
            text,
            voice,
            receive_timeout=recv_to,
        )
        out = bytearray()
        try:
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if isinstance(data, bytes):
                        out.extend(data)
        except Exception:
            return b""
        return bytes(out)

    def _edge_tts_request(self, text: str) -> bytes:
        """스트리밍 배치가 연속으로 몰리면 Edge 쪽 일시 실패·빈 응답이 나올 수 있어 짧게 재시도."""
        t = (text or "").strip()
        if not t:
            return b""
        last = b""
        for attempt in range(3):
            if attempt:
                time.sleep(0.25 * attempt)
            last = self._edge_tts_request_once(t)
            if last:
                return last
        return last

    def _openai_speech_url(self) -> str:
        raw = self.api_url.strip().split("?")[0].rstrip("/")
        if raw.endswith("/speech") or "/audio/speech" in raw:
            return raw
        if raw.endswith("/v1"):
            return f"{raw}/audio/speech"
        return f"{raw}/v1/audio/speech"

    def _openai_tts_request(self, text: str) -> bytes:
        url = self._openai_speech_url()
        headers = {"Content-Type": "application/json"}
        key = (self.openai_tts_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.openai_tts_model or "tts-1",
            "input": text,
            "voice": self.openai_tts_voice or "alloy",
        }
        try:
            r = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_sec,
            )
            if r.status_code != 200:
                return b""
            ct = (r.headers.get("content-type") or "").lower()
            if "json" in ct:
                return b""
            return r.content if r.content else b""
        except requests.RequestException:
            return b""

    def _elevenlabs_tts_request_http(self, text: str) -> bytes:
        key = (self.elevenlabs_api_key or "").strip()
        voice = (self.elevenlabs_voice_id or "").strip()
        if not key or not voice:
            return b""
        model = (self.elevenlabs_model or "eleven_multilingual_v2").strip()
        base = (self.elevenlabs_api_base or "").strip().rstrip("/")
        if not base:
            base = "https://api.elevenlabs.io"
        url = f"{base}/v1/text-to-speech/{voice}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": key,
        }
        payload = {"text": text, "model_id": model}
        try:
            r = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_sec,
            )
            if r.status_code != 200:
                return b""
            ct = (r.headers.get("content-type") or "").lower()
            if "json" in ct and "audio" not in ct:
                return b""
            return r.content if r.content else b""
        except requests.RequestException:
            return b""

    def _elevenlabs_tts_request(self, text: str) -> bytes:
        key = (self.elevenlabs_api_key or "").strip()
        voice = (self.elevenlabs_voice_id or "").strip()
        if not key or not voice:
            return b""
        model = (self.elevenlabs_model or "eleven_multilingual_v2").strip()
        out_fmt = (self.elevenlabs_output_format or "mp3_44100_128").strip() or "mp3_44100_128"

        try:
            from elevenlabs.client import ElevenLabs
        except ImportError:
            return self._elevenlabs_tts_request_http(text)

        base = (self.elevenlabs_api_base or "").strip().rstrip("/")
        client_kw: dict[str, Any] = {
            "api_key": key,
            "timeout": float(max(1, int(self.timeout_sec))),
        }
        if base:
            client_kw["base_url"] = base
        try:
            client = ElevenLabs(**client_kw)
            chunks = client.text_to_speech.convert(
                voice_id=voice,
                text=text,
                model_id=model,
                output_format=cast(Any, out_fmt),
            )
            return b"".join(chunks)
        except Exception:
            return self._elevenlabs_tts_request_http(text)

    def _custom_tts_request(self, text: str) -> bytes:
        try:
            r = requests.get(
                self.api_url,
                params={"text": text},
                timeout=self.timeout_sec,
            )
            if r.status_code == 405 or r.status_code == 404:
                r = requests.post(
                    self.api_url,
                    json={"text": text},
                    timeout=self.timeout_sec,
                )
            if r.status_code != 200:
                return b""
            ct = (r.headers.get("content-type") or "").lower()
            if "application/json" in ct and not ct.startswith("audio/"):
                try:
                    data = r.json()
                    if isinstance(data, dict) and "audio" in data:
                        import base64

                        return base64.b64decode(data["audio"])
                except Exception:
                    pass
                return b""
            return r.content if r.content else b""
        except requests.RequestException:
            return b""

    def generate_audio(self, text: str) -> bytes:
        skip = self._should_skip_tts(text)
        cleaned = self._clean_text_for_tts(text) if not skip else ""
        if skip:
            return b""
        if not cleaned:
            return b""

        if self.provider == "gpt-sovits":
            return self._gpt_sovits_request(cleaned)
        if self.provider == "edge-tts":
            return self._edge_tts_request(cleaned)
        if self.provider == "openai_tts":
            return self._openai_tts_request(cleaned)
        if self.provider == "elevenlabs":
            return self._elevenlabs_tts_request(cleaned)
        if self.provider == "custom":
            return self._custom_tts_request(cleaned)
        return b""
