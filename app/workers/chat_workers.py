from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QMetaObject, Qt, QThread, Signal, Q_ARG

from core.audio_playback import play_wav_bytes_blocking, stop_playback
from core.llm_attachments import LLMMediaAttachment, format_user_text_for_history
from core.live2d_emotion_tags import (
    assistant_history_plain,
    strip_assistant_tags_for_pipeline,
    thinking_mode_answer_body_if_marked,
)
from core.text_stream_batch import TextBatchAccumulator
from core.vmate_manager import VMateManager

class _StreamSyncState:
    __slots__ = ("cv", "results", "llm_done", "batches_total")

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.results: dict[int, bytes] = {}
        self.llm_done = False
        self.batches_total: int | None = None


class TypingSyncState:
    """타이핑으로 실제로 보인 글자 수(코드포인트). TTS 재생 스레드가 이 값을 기다립니다."""

    __slots__ = ("lock", "displayed")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.displayed = 0


class _LLMChatWorkerThread(QThread):
    """메인 스레드 블로킹 방지: 백그라운드에서 LLM+TTS 처리."""

    response_ready = Signal(str, bytes)
    response_failed = Signal(str)

    def __init__(
        self,
        vmate_manager: VMateManager,
        text: str,
        stop_event: threading.Event,
        attachments: Optional[list[LLMMediaAttachment]] = None,
        history_user_content: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vmate_manager
        self._text = text
        self._attachments = list(attachments or [])
        self._stop_event = stop_event
        self._history_user_content = history_user_content

    def run(self) -> None:
        try:
            response_text, audio = self._vm.process_user_input(
                self._text,
                attachments=self._attachments or None,
                history_user_content=self._history_user_content,
            )
            if self.isInterruptionRequested() or self._stop_event.is_set():
                return
            if not isinstance(audio, (bytes, bytearray)):
                audio = b""
            self.response_ready.emit(response_text, bytes(audio))
        except Exception as e:
            if not (self.isInterruptionRequested() or self._stop_event.is_set()):
                self.response_failed.emit(str(e))


class _StreamChatWorkerThread(QThread):
    """
    스트림 델타 -> 배치 -> (텍스트는 즉시 UI 큐, TTS는 백그라운드) -> 타이핑 후 구간별 순차 재생.
    텍스트를 TTS 완료 뒤에만 넣으면 두 번째 배치 TTS가 지연·멈출 때 긴 답이 첫 문장에서 끊긴 것처럼 보임.
    """

    text_batch = Signal(int, int, str)
    # assistant_raw_progress: 태그 포함 스트림 누적 원문 → 표정 즉시 반영
    assistant_raw_progress = Signal(int, str)
    stream_finished = Signal(str)
    stream_failed = Signal(str)
    pipeline_interrupted = Signal()

    def __init__(
        self,
        vmate_manager: VMateManager,
        user_text: str,
        chat_widget: "ChatWidget",
        stop_event: threading.Event,
        attachments: Optional[list[LLMMediaAttachment]] = None,
        invoke_gen: int = 0,
        history_user_line_override: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vm = vmate_manager
        self._user = (user_text or "").strip()
        self._attachments = list(attachments or [])
        if history_user_line_override is not None:
            self._history_user_line = (history_user_line_override or "").strip()
            if not self._history_user_line:
                self._history_user_line = format_user_text_for_history(
                    self._user, self._attachments
                )
        else:
            self._history_user_line = format_user_text_for_history(
                self._user, self._attachments
            )
        self._cw = chat_widget
        self._stop_event = stop_event
        self._invoke_gen = int(invoke_gen)

    def run(self) -> None:
        shared = _StreamSyncState()
        tts_in_q: queue.Queue = queue.Queue()
        t1: threading.Thread | None = None
        t2: threading.Thread | None = None
        batch_texts: dict[int, str] = {}
        segment_ui_ready: dict[int, threading.Event] = {}
        stop_ev = self._stop_event

        def _fail_cleanup(msg: str) -> None:
            with shared.cv:
                shared.llm_done = True
                shared.batches_total = 0
                shared.cv.notify_all()
            try:
                tts_in_q.put_nowait(None)
            except Exception:
                pass
            self.stream_failed.emit(msg)

        def _interrupt_cleanup(tts_batch_idx: int) -> None:
            stop_playback()
            for ev in segment_ui_ready.values():
                ev.set()
            with shared.cv:
                for i in range(tts_batch_idx):
                    if i not in shared.results:
                        shared.results[i] = b""
                shared.llm_done = True
                shared.batches_total = tts_batch_idx
                shared.cv.notify_all()
            try:
                tts_in_q.put_nowait(None)
            except Exception:
                pass

        try:
            llm = self._vm.llm_engine
            tts = self._vm.tts_engine
            history = self._vm.history_snapshot()
            acc = TextBatchAccumulator(
                llm.stream_batch_min_chars,
                llm.stream_batch_max_chars,
            )

            skip_tts = False
            tts_batch_idx = 0
            full_chunks: list[str] = []

            self._cw._stream_segment_ui_events = segment_ui_ready

            def tts_worker() -> None:
                while True:
                    if stop_ev.is_set():
                        try:
                            while True:
                                tts_in_q.get_nowait()
                        except queue.Empty:
                            pass
                        break
                    try:
                        item = tts_in_q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if item is None:
                        break
                    idx, tx = item
                    if stop_ev.is_set():
                        with shared.cv:
                            shared.results[idx] = b""
                            shared.cv.notify_all()
                        continue
                    if tts.provider == "edge-tts":
                        time.sleep(0.1)
                    try:
                        a = tts.generate_audio(tx)
                    except Exception:
                        a = b""
                    if stop_ev.is_set():
                        a = b""
                    if not isinstance(a, bytes):
                        a = b""
                    with shared.cv:
                        shared.results[idx] = a
                        shared.cv.notify_all()
                    if stop_ev.is_set():
                        continue
                    QMetaObject.invokeMethod(
                        self._cw,
                        "_on_stream_tts_segment_ready",
                        Qt.QueuedConnection,
                        Q_ARG(int, self._invoke_gen),
                        Q_ARG(int, idx),
                        Q_ARG(str, tx),
                        Q_ARG(QByteArray, QByteArray(a)),
                    )

            def play_worker() -> None:
                next_p = 0
                while True:
                    with shared.cv:
                        while next_p not in shared.results:
                            if stop_ev.is_set():
                                return
                            if (
                                shared.llm_done
                                and shared.batches_total is not None
                                and next_p >= shared.batches_total
                            ):
                                return
                            shared.cv.wait(timeout=0.25)
                        if stop_ev.is_set():
                            return
                        audio = shared.results.pop(next_p)
                        seg_txt = batch_texts.get(next_p, "")
                    evs = getattr(self._cw, "_stream_segment_ui_events", None)
                    if evs is not None:
                        ev = evs.get(next_p)
                        if ev is not None:
                            ev.wait(timeout=180.0)
                    if stop_ev.is_set():
                        stop_playback()
                        return
                    cur = next_p
                    next_p = cur + 1
                    ba = QByteArray(bytes(audio))
                    QMetaObject.invokeMethod(
                        self._cw,
                        "_prepare_playback_segment",
                        Qt.BlockingQueuedConnection,
                        Q_ARG(int, self._invoke_gen),
                        Q_ARG(str, seg_txt),
                        Q_ARG(QByteArray, ba),
                    )
                    if stop_ev.is_set():
                        stop_playback()
                        return
                    try:
                        play_wav_bytes_blocking(bytes(audio))
                    except Exception:
                        pass

            t1 = threading.Thread(target=tts_worker, daemon=True)
            t2 = threading.Thread(target=play_worker, daemon=True)
            t1.start()
            t2.start()

            fc = getattr(llm, "_full_config", {}) or {}
            thinking_mode = bool((fc.get("llm") or {}).get("thinking_mode", False))
            display_plain_len = 0
            rolled = ""
            interrupted = False
            for delta in llm.iter_chat_stream(
                self._user,
                history=history,
                attachments=self._attachments or None,
            ):
                if self.isInterruptionRequested() or stop_ev.is_set():
                    interrupted = True
                    break
                full_chunks.append(delta)
                rolled += delta
                if rolled.lstrip().startswith(("[LLM]", "[오류]")):
                    skip_tts = True
                for b in acc.feed(delta):
                    if not b.strip():
                        continue
                    b_out = strip_assistant_tags_for_pipeline(b, fc)
                    if skip_tts:
                        self.text_batch.emit(self._invoke_gen, -1, b_out)
                    elif thinking_mode:
                        display_plain_len += len(b_out or "")
                        self.text_batch.emit(self._invoke_gen, -1, b_out)
                    else:
                        segment_ui_ready[tts_batch_idx] = threading.Event()
                        batch_texts[tts_batch_idx] = b_out
                        self.text_batch.emit(self._invoke_gen, tts_batch_idx, b_out)
                        tts_in_q.put((tts_batch_idx, b_out))
                        tts_batch_idx += 1
                if not rolled.lstrip().startswith(("[LLM]", "[오류]")):
                    self.assistant_raw_progress.emit(self._invoke_gen, rolled)

            if not interrupted:
                for b in acc.flush():
                    if not b.strip():
                        continue
                    b_out = strip_assistant_tags_for_pipeline(b, fc)
                    if skip_tts:
                        self.text_batch.emit(self._invoke_gen, -1, b_out)
                    elif thinking_mode:
                        display_plain_len += len(b_out or "")
                        self.text_batch.emit(self._invoke_gen, -1, b_out)
                    else:
                        segment_ui_ready[tts_batch_idx] = threading.Event()
                        batch_texts[tts_batch_idx] = b_out
                        self.text_batch.emit(self._invoke_gen, tts_batch_idx, b_out)
                        tts_in_q.put((tts_batch_idx, b_out))
                        tts_batch_idx += 1
                if rolled and not rolled.lstrip().startswith(("[LLM]", "[오류]")):
                    self.assistant_raw_progress.emit(self._invoke_gen, rolled)

            if interrupted:
                _interrupt_cleanup(tts_batch_idx)
                if t1 is not None:
                    t1.join(timeout=self._vm.tts_engine.timeout_sec + 30)
                if t2 is not None:
                    t2.join(timeout=60.0)
                self.pipeline_interrupted.emit()
                return

            full = "".join(full_chunks).strip()

            if thinking_mode and not skip_tts:
                raw_ans = thinking_mode_answer_body_if_marked(full, fc)
                if raw_ans is not None:
                    tts_plain = assistant_history_plain(raw_ans, fc).strip()
                else:
                    # MCP 직후 등 `### 답변` 없이 평문만 올 때 무음 방지 (전체 답을 TTS)
                    tts_plain = assistant_history_plain(full, fc).strip()
                if tts_plain:
                    t_acc = TextBatchAccumulator(
                        llm.stream_batch_min_chars,
                        llm.stream_batch_max_chars,
                    )
                    t_parts: list[str] = []
                    t_parts.extend(t_acc.feed(tts_plain))
                    t_parts.extend(t_acc.flush())
                    for tb in t_parts:
                        if not tb.strip():
                            continue
                        tb_out = strip_assistant_tags_for_pipeline(tb, fc)
                        if not tb_out.strip():
                            continue
                        segment_ui_ready[tts_batch_idx] = threading.Event()
                        batch_texts[tts_batch_idx] = tb_out
                        QMetaObject.invokeMethod(
                            self._cw,
                            "_schedule_stream_tts_segment",
                            Qt.QueuedConnection,
                            Q_ARG(int, self._invoke_gen),
                            Q_ARG(int, tts_batch_idx),
                            Q_ARG(int, display_plain_len),
                        )
                        tts_in_q.put((tts_batch_idx, tb_out))
                        tts_batch_idx += 1
            with shared.cv:
                shared.batches_total = 0 if skip_tts else tts_batch_idx
                shared.llm_done = True
                shared.cv.notify_all()

            tts_in_q.put(None)
            # tts_worker는 배치마다 timeout_sec까지 걸릴 수 있음. 고정 120~150초 조인이면
            # 긴 답변에서 합성·재생이 남은 채 stream_finished가 나가 TTS가 끊긴 것처럼 보임.
            per = float(self._vm.tts_engine.timeout_sec)
            nseg = max(0, int(tts_batch_idx))
            if skip_tts or nseg <= 0:
                t1_budget = max(60.0, per + 30.0)
                t2_budget = max(120.0, per + 60.0)
            else:
                t1_budget = max(180.0, per * nseg + 120.0)
                t2_budget = max(
                    300.0,
                    per * nseg * 2 + 600.0 + 45.0 * nseg,
                )
            if t1 is not None:
                t1.join(timeout=t1_budget)
            if t2 is not None:
                t2.join(timeout=t2_budget)

            self._vm.commit_user_exchange_if_ok(self._history_user_line, full)
            self.stream_finished.emit(full)
        except Exception as e:
            _fail_cleanup(str(e))
        finally:
            if t1 is not None and t1.is_alive():
                try:
                    tts_in_q.put_nowait(None)
                except Exception:
                    pass
                t1.join(timeout=2.0)
            if t2 is not None and t2.is_alive():
                with shared.cv:
                    shared.llm_done = True
                    shared.batches_total = shared.batches_total or 0
                    shared.cv.notify_all()
                t2.join(timeout=2.0)
