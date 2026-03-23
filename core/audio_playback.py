from __future__ import annotations

import io
import threading
import wave
from typing import Optional

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

# 스트림 TTS 배치 + 비동기 재생이 겹치면 sd.play가 서로 끊거나 무음이 될 수 있음
_sd_play_lock = threading.Lock()


def stop_playback() -> None:
    """진행 중인 sounddevice 출력을 즉시 멈춥니다(다른 스레드에서 호출 가능)."""
    try:
        sd.stop()
    except Exception:
        pass


def _is_riff_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def wav_duration_seconds(data: bytes) -> Optional[float]:
    """WAV 또는 MP3 바이트에서 재생 길이(초). 실패 시 None."""
    if not data or len(data) < 12:
        return None
    if _is_riff_wav(data):
        try:
            with io.BytesIO(data) as buf:
                with wave.open(buf, "rb") as wf:
                    fr = wf.getframerate()
                    if fr <= 0:
                        return None
                    return wf.getnframes() / float(fr)
        except Exception:
            return None
    try:
        import miniaudio

        dec = miniaudio.decode(data, miniaudio.SampleFormat.SIGNED16)
        nch = max(1, int(dec.nchannels))
        sr = int(dec.sample_rate)
        if sr <= 0:
            return None
        ns = len(dec.samples)
        frames = ns // nch if nch else 0
        return frames / float(sr)
    except Exception:
        return None


def _play_mp3_bytes_blocking(data: bytes) -> None:
    """edge-tts 등에서 오는 MP3 바이트 재생 (miniaudio 디코딩)."""
    import miniaudio

    dec = miniaudio.decode(data, miniaudio.SampleFormat.SIGNED16)
    nch = max(1, int(dec.nchannels))
    sr = int(dec.sample_rate)
    if sr <= 0:
        return
    arr = np.array(dec.samples, dtype=np.int16).reshape(-1, nch)
    if arr.size == 0:
        return
    audio_f = arr.astype(np.float32) / 32768.0
    sd.play(audio_f, sr)
    sd.wait()


def play_wav_bytes_blocking(data: bytes) -> None:
    """WAV 또는 MP3(edge-tts) 바이트를 동기 재생. 호출 스레드에서 sd.wait()까지 대기."""
    if not data:
        return
    if _is_riff_wav(data):
        buf = io.BytesIO(data)
        sr, audio = wavfile.read(buf)
        if audio.size == 0:
            return
        if audio.dtype == np.int16:
            audio_f = audio.astype(np.float32) / 32768.0
        elif audio.dtype == np.int32:
            audio_f = audio.astype(np.float32) / float(np.iinfo(np.int32).max)
        elif np.issubdtype(audio.dtype, np.floating):
            audio_f = audio.astype(np.float32)
        else:
            audio_f = audio.astype(np.float32) / float(np.iinfo(audio.dtype).max)
        with _sd_play_lock:
            sd.play(audio_f, int(sr))
            sd.wait()
        return
    try:
        _play_mp3_bytes_blocking(data)
    except Exception:
        return


def play_wav_bytes_async(data: bytes) -> None:
    """GUI를 막지 않도록 데몬 스레드에서 재생합니다."""

    def _run() -> None:
        try:
            play_wav_bytes_blocking(data)
        except Exception:
            pass

    if not data:
        return
    threading.Thread(target=_run, daemon=True).start()
