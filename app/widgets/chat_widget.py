from __future__ import annotations

import html
import os
import threading
from typing import Optional

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QMimeData,
    Qt,
    QThread,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.widgets.icons import make_paperclip_icon
from ui.hover_button import HoverAnimPushButton
from app.workers.chat_workers import (
    TypingSyncState,
    _LLMChatWorkerThread,
    _StreamChatWorkerThread,
)
from app.windows.identity import is_app_main_window
from core.audio_playback import (
    play_wav_bytes_async,
    stop_playback,
    wav_duration_seconds,
)
from core.llm_attachments import (
    LLMMediaAttachment,
    MAX_LLM_ATTACHMENTS,
    format_user_text_for_history,
    load_attachment_from_path,
)

# 유휴 시 LLM에만 보내는 유도 문구(히스토리에는 짧은 줄만 남김).
_IDLE_PROACTIVE_LLM_TEXT = (
    "[시스템·유도] 사용자가 설정한 시간 동안 이 앱에서 입력이나 조작이 없었습니다. "
    "당신(캐릭터)이 먼저 말을 걸어 대화를 이어가 주세요. "
    "말투는 시스템 프롬프트대로, 1~3문장으로 가볍게. 깊고 전문적으로 이야기 할 필요 없이 쉽고 자연스럽게 말해주세요. "
    "이미지를 첨부 하고 있다면 그 이미지에서 어떤 것들이 보이는지 간단하게 설명해도 괜찮습니다. 다만, 이미지가 없는경우에는 그런 뉘앙스를 넣지 마세요."
)
from core.live2d_character_settings import get_assistant_display_name
from core.live2d_emotion_tags import (
    assistant_history_plain,
    assistant_thinking_display_body_html,
)
from core.model_profile import repo_root

class ChatWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self._dark_mode = True
        self._c_body = "#cdd6f4"
        self._c_user = "#89b4fa"
        self._c_assist = "#f5c2e7"
        self._c_muted = "#7f849c"
        self._c_err = "#f38ba8"
        self.setObjectName("vmateChatFrame")
        self.setFixedHeight(200)

        self._chat_thread: Optional[QThread] = None
        self._pending_reply_label: Optional[QLabel] = None
        self._wait_phase = 0
        self._stream_motion_once = False
        self._typing_sync: Optional[TypingSyncState] = None
        self._type_buffer = ""
        self._pipeline_done = False
        self._post_typewriter_audio: Optional[bytes] = None
        self._post_typewriter_text: Optional[str] = None
        self._assistant_finalize_done = False
        self._typing_interval_ms = 42
        self._typing_chars_per_tick = 1
        self._stream_segment_ui_events: Optional[dict[int, threading.Event]] = None
        self._stream_segment_release_at: dict[int, int] = {}
        self._stream_typing_cumulative: int = 0
        self._stop_pipeline = threading.Event()
        self._stream_invoke_gen = 0
        self._pending_attachments: list[LLMMediaAttachment] = []
        self._pet_compact_mode = False

        self._type_timer = QTimer(self)
        self._type_timer.setSingleShot(False)
        self._type_timer.timeout.connect(self._on_typewriter_tick)

        layout = QVBoxLayout()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout()
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.history_widget.setLayout(self.history_layout)
        self.scroll.setWidget(self.history_widget)

        layout.addWidget(self.scroll)

        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self._apply_input_placeholder_for_assistant()
        self.input_field.returnPressed.connect(self.send_message)

        self.btn_attach = QToolButton(self)
        self.btn_attach.setIcon(make_paperclip_icon())
        self.btn_attach.setToolTip(
            "이미지·텍스트·PDF 첨부. 채팅창으로 파일을 끌어다 놓아도 추가됩니다. "
            "OpenAI 호환 API는 PDF를 파일 파트로 보내고, Ollama는 추출 텍스트를 넣습니다."
        )
        self.btn_attach.setFixedSize(34, 34)
        self.btn_attach.clicked.connect(self._pick_attachments)
        self.btn_attach.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_attach.customContextMenuRequested.connect(
            self._on_attach_context_menu
        )

        self._attach_badge = QLabel("")
        self._attach_badge.setMaximumWidth(56)

        self.btn_send = HoverAnimPushButton("전송", hover_blur=22)
        self.btn_send.clicked.connect(self.send_message)

        self.btn_interrupt = HoverAnimPushButton("중단", hover_blur=22)
        self.btn_interrupt.setToolTip(
            "응답 생성·타이핑·음성 재생을 즉시 멈춥니다."
        )
        self.btn_interrupt.clicked.connect(self.request_interrupt)
        self.btn_interrupt.setEnabled(False)

        input_layout.addWidget(self.btn_attach)
        input_layout.addWidget(self._attach_badge)
        input_layout.addWidget(self.input_field, stretch=1)
        input_layout.addWidget(self.btn_send)
        input_layout.addWidget(self.btn_interrupt)

        layout.addLayout(input_layout)
        self.setLayout(layout)

        self._wait_timer = QTimer(self)
        self._wait_timer.timeout.connect(self._animate_waiting)

        self.setAcceptDrops(True)
        self._drop_watch_targets: list[QWidget] = []
        for w in (
            self.scroll,
            self.scroll.viewport(),
            self.history_widget,
            self.input_field,
        ):
            w.setAcceptDrops(True)
            w.installEventFilter(self)
            self._drop_watch_targets.append(w)

        self.apply_dark_mode_from_parent_config()

    def apply_dark_mode_from_parent_config(self) -> None:
        dark = True
        if self.parent is not None and hasattr(self.parent, "config"):
            dark = bool(self.parent.config.get("ui", {}).get("dark_mode", True))
        self.apply_dark_mode(dark)

    def _set_push_shadow(self, btn: HoverAnimPushButton, color: QColor) -> None:
        eff = btn.graphicsEffect()
        if isinstance(eff, QGraphicsDropShadowEffect):
            eff.setColor(color)

    def apply_dark_mode(self, dark: bool) -> None:
        self._dark_mode = bool(dark)
        if self._dark_mode:
            self._c_body = "#cdd6f4"
            self._c_user = "#89b4fa"
            self._c_assist = "#f5c2e7"
            self._c_muted = "#7f849c"
            self._c_err = "#f38ba8"
            self.setStyleSheet(
                """
                QFrame#vmateChatFrame {
                    background-color: rgba(24, 24, 37, 0.94);
                    border: 1px solid rgba(69, 71, 90, 0.98);
                    border-radius: 14px;
                }
                QScrollArea { background: transparent; border: none; }
                QScrollBar:vertical {
                    background: #181825;
                    width: 8px;
                    margin: 2px;
                    border-radius: 4px;
                }
                QScrollBar::handle:vertical {
                    background: #45475a;
                    min-height: 28px;
                    border-radius: 4px;
                }
                """
            )
            self.input_field.setStyleSheet(
                "padding: 8px 12px; border-radius: 8px; border: 1px solid #45475a; "
                "background: #1e1e2e; color: #cdd6f4; selection-background-color: #45475a;"
            )
            self.btn_attach.setStyleSheet(
                "QToolButton { border: 1px solid #45475a; border-radius: 8px; background: #313244; }"
                "QToolButton:hover { background: #45475a; border-color: #585b70; }"
            )
            self.btn_send.setStyleSheet(
                "QPushButton { padding: 8px 16px; background: #89b4fa; color: #11111b; "
                "border-radius: 8px; font-weight: 600; border: 1px solid #89b4fa; }"
                "QPushButton:hover { background: #b4befe; border-color: #b4befe; }"
                "QPushButton:pressed { background: #7287fd; border-color: #7287fd; color: #11111b; }"
                "QPushButton:disabled { background: #45475a; color: #7f849c; border-color: #45475a; }"
            )
            self.btn_interrupt.setStyleSheet(
                "QPushButton { padding: 8px 12px; background: #313244; color: #f38ba8; "
                "border-radius: 8px; font-weight: 600; border: 1px solid #45475a; }"
                "QPushButton:hover { background: #45475a; color: #eba0ac; }"
                "QPushButton:pressed { background: #1e1e2e; }"
                "QPushButton:disabled { background: #181825; color: #585b70; border-color: #313244; }"
            )
            self._set_push_shadow(self.btn_send, QColor(137, 180, 250, 90))
            self._set_push_shadow(self.btn_interrupt, QColor(243, 139, 168, 95))
        else:
            self._c_body = "#222222"
            self._c_user = "#0056b3"
            self._c_assist = "#d63384"
            self._c_muted = "#666666"
            self._c_err = "#cc0000"
            self.setStyleSheet(
                """
                QFrame#vmateChatFrame {
                    background-color: rgba(255, 255, 255, 220);
                    border-radius: 10px;
                }
                QScrollArea { background: transparent; border: none; }
                QScrollBar:vertical {
                    background: #e8e8e8;
                    width: 8px;
                    margin: 2px;
                    border-radius: 4px;
                }
                QScrollBar::handle:vertical {
                    background: #b0b0b0;
                    min-height: 28px;
                    border-radius: 4px;
                }
                """
            )
            self.input_field.setStyleSheet(
                "padding: 8px; border-radius: 5px; border: 1px solid #ccc; "
                "background: white; color: #222; selection-background-color: #b3d7ff;"
            )
            self.btn_attach.setStyleSheet(
                "QToolButton { border: 1px solid #ccc; border-radius: 5px; background: #fff; }"
                "QToolButton:hover { background: #f0f4ff; border-color: #99b; }"
            )
            self.btn_send.setStyleSheet(
                "QPushButton { padding: 8px 15px; background: #007bff; color: white; "
                "border-radius: 5px; font-weight: bold; border: none; }"
                "QPushButton:hover { background: #1a8cff; }"
                "QPushButton:pressed { background: #0069d9; }"
                "QPushButton:disabled { background: #6c757d; color: #e9ecef; }"
            )
            self.btn_interrupt.setStyleSheet(
                "QPushButton { padding: 8px 12px; background: #dc3545; color: white; "
                "border-radius: 5px; font-weight: bold; border: none; }"
                "QPushButton:hover { background: #e4606d; }"
                "QPushButton:pressed { background: #bd2130; }"
                "QPushButton:disabled { background: #adb5bd; color: #f8f9fa; }"
            )
            self._set_push_shadow(self.btn_send, QColor(0, 123, 255, 95))
            self._set_push_shadow(self.btn_interrupt, QColor(220, 53, 69, 95))
        self._attach_badge.setStyleSheet(
            f"color:{self._c_muted};font-size:11px;"
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self._drop_watch_targets:
            if isinstance(event, QDragEnterEvent):
                if self._attachment_drop_busy():
                    return False
                if self._mime_has_attachable_files(event.mimeData()):
                    event.acceptProposedAction()
                    return True
                return False
            if isinstance(event, QDragMoveEvent):
                if self._attachment_drop_busy():
                    return False
                if self._mime_has_attachable_files(event.mimeData()):
                    event.acceptProposedAction()
                    return True
                return False
            if isinstance(event, QDropEvent):
                if self._attachment_drop_busy():
                    return False
                paths = self._local_file_paths_from_mime(event.mimeData())
                if paths:
                    self._ingest_attachment_paths(paths)
                    event.acceptProposedAction()
                    return True
                return False
        return super().eventFilter(watched, event)

    def set_pet_compact_mode(self, on: bool) -> None:
        """캐릭터 모드 모드: 대화 기록은 쌓되 스크롤 영역만 숨기고 입력 줄만 표시."""
        self._pet_compact_mode = bool(on)
        if on:
            self.scroll.hide()
            self.setFixedHeight(56)
        else:
            self.scroll.show()
            self.setFixedHeight(200)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """캐릭터 모드 컴팩트 바: 입력·버튼이 아닌 빈 영역을 다시 누르면 닫기."""
        if self._pet_compact_mode and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            for w in (
                self.input_field,
                self.btn_attach,
                self.btn_send,
                self.btn_interrupt,
                self._attach_badge,
            ):
                if w.isVisible() and w.geometry().contains(pos):
                    super().mousePressEvent(event)
                    return
            mw = self.window()
            if is_app_main_window(mw) and getattr(mw, "_desktop_pet_mode", False):
                mw.hide_pet_floating_chat()
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if (
            self._pet_compact_mode
            and event.key() == Qt.Key.Key_Escape
        ):
            mw = self.window()
            if is_app_main_window(mw) and getattr(mw, "_desktop_pet_mode", False):
                mw.hide_pet_floating_chat()
                mw.live2d_view.setFocus()
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._attachment_drop_busy():
            event.ignore()
            return
        if self._mime_has_attachable_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._attachment_drop_busy():
            event.ignore()
            return
        if self._mime_has_attachable_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if self._attachment_drop_busy():
            event.ignore()
            return
        paths = self._local_file_paths_from_mime(event.mimeData())
        if paths:
            self._ingest_attachment_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _attachment_drop_busy(self) -> bool:
        return (
            self._chat_thread is not None
            and self._chat_thread.isRunning()
            and not self._stop_pipeline.is_set()
        )

    @staticmethod
    def _local_file_paths_from_mime(mime: QMimeData | None) -> list[str]:
        if mime is None or not mime.hasUrls():
            return []
        out: list[str] = []
        for u in mime.urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if p and os.path.isfile(p):
                    out.append(p)
        seen: set[str] = set()
        uniq: list[str] = []
        for p in out:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                uniq.append(ap)
        return uniq

    def _mime_has_attachable_files(self, mime: QMimeData | None) -> bool:
        return bool(self._local_file_paths_from_mime(mime))

    def _ingest_attachment_paths(self, paths: list[str]) -> None:
        for path in paths:
            if len(self._pending_attachments) >= MAX_LLM_ATTACHMENTS:
                QMessageBox.information(
                    self,
                    "첨부",
                    f"첨부는 최대 {MAX_LLM_ATTACHMENTS}개까지 가능합니다.",
                )
                break
            att, err = load_attachment_from_path(path)
            if err:
                QMessageBox.warning(self, "첨부", err)
                continue
            self._pending_attachments.append(att)
        self._refresh_attach_badge()

    def _assistant_display_name(self) -> str:
        mw = self.window()
        if is_app_main_window(mw) and hasattr(mw, "current_live2d_model_folder"):
            folder = mw.current_live2d_model_folder()
            legacy = str(
                getattr(mw, "config", {}).get("ui", {}).get("chat_assistant_name", "")
                or ""
            ).strip()
        elif self.parent and hasattr(self.parent, "config"):
            cfg = self.parent.config
            folder = str(cfg.get("live2d", {}).get("model_folder", "") or "").strip()
            legacy = str(cfg.get("ui", {}).get("chat_assistant_name", "") or "").strip()
        else:
            return "DAON"
        return get_assistant_display_name(
            repo_root(),
            folder,
            legacy_chat_assistant_name=legacy or None,
        )

    def _assistant_name_span_html(self) -> str:
        n = html.escape(self._assistant_display_name())
        return f'<span style="color:{self._c_assist};font-weight:bold;">{n}:</span> '

    def _apply_input_placeholder_for_assistant(self) -> None:
        n = self._assistant_display_name()
        self.input_field.setPlaceholderText(f"{n}에게 메시지 보내기...")

    def _refresh_attach_badge(self) -> None:
        n = len(self._pending_attachments)
        if n <= 0:
            self._attach_badge.setText("")
            return
        self._attach_badge.setText(f"({n})")

    def _on_attach_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_clear = menu.addAction("첨부 모두 제거")
        act_clear.setEnabled(bool(self._pending_attachments))
        chosen = menu.exec(self.btn_attach.mapToGlobal(pos))
        if chosen == act_clear:
            self._pending_attachments.clear()
            self._refresh_attach_badge()

    def _pick_attachments(self) -> None:
        if (
            self._chat_thread is not None
            and self._chat_thread.isRunning()
            and not self._stop_pipeline.is_set()
        ):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "파일 첨부",
            "",
            "이미지·텍스트·PDF (*.png *.jpg *.jpeg *.gif *.webp *.pdf *.txt *.md *.csv *.json *.yaml *.yml *.log);;모든 파일 (*.*)",
        )
        if not paths:
            return
        self._ingest_attachment_paths(list(paths))

    def apply_assistant_display_settings(self) -> None:
        """설정 저장 후 채팅 표시 이름·입력 힌트 반영."""
        self._apply_input_placeholder_for_assistant()
        if self._pending_reply_label:
            if self._wait_timer.isActive():
                self._animate_waiting()
            elif hasattr(self, "_streaming_plain"):
                self._update_pending_label_html()

    def _reload_typing_config(self) -> None:
        ui = self.parent.config.get("ui", {}) if self.parent else {}
        # 짧은 간격 + 1글자/틱에 가까울수록 타다닥 타자기 느낌
        self._typing_interval_ms = max(4, int(ui.get("typing_interval_ms", 26)))
        self._typing_chars_per_tick = max(1, min(4, int(ui.get("typing_chars_per_tick", 1))))

    def _ensure_type_timer(self) -> None:
        self._type_timer.setInterval(self._typing_interval_ms)
        if not self._type_timer.isActive():
            self._type_timer.start()

    def _assistant_bubble_label_html(self, raw_text: str) -> str:
        """사고 모드면 이름은 `### 답변` 본문 앞에만 붙이고, 그 외에는 말풍선 맨 앞에 붙입니다."""
        cfg = getattr(self.parent, "config", None) or {}
        name_sp = self._assistant_name_span_html()
        styled = assistant_thinking_display_body_html(
            raw_text,
            cfg,
            think_color=self._c_muted,
            body_color=self._c_body,
            name_span_before_answer=name_sp,
        )
        if styled is not None:
            return styled
        show = assistant_history_plain(raw_text, cfg)
        esc = html.escape(show).replace("\n", "<br/>")
        return name_sp + f'<span style="color:{self._c_body};">{esc}</span>'

    def _update_pending_label_html(self) -> None:
        if not self._pending_reply_label:
            return
        plain = getattr(self, "_streaming_plain", "") or ""
        self._pending_reply_label.setText(self._assistant_bubble_label_html(plain))

    def _release_stream_segments_if_caught_up(self) -> None:
        """스트리밍 TTS: 화면에 해당 구간 글자가 다 나온 뒤에만 재생 스레드가 진행하도록."""
        evs = self._stream_segment_ui_events
        if not evs or not self._stream_segment_release_at:
            return
        shown = len(getattr(self, "_streaming_plain", "") or "")
        for idx, need_len in list(self._stream_segment_release_at.items()):
            if shown < need_len:
                continue
            ev = evs.get(idx)
            if ev is not None and not ev.is_set():
                ev.set()

    def _on_typewriter_tick(self) -> None:
        if self._typing_sync is None:
            self._type_timer.stop()
            return

        if self._type_buffer:
            n = self._typing_chars_per_tick
            chunk = self._type_buffer[:n]
            self._type_buffer = self._type_buffer[n:]
            if not hasattr(self, "_streaming_plain"):
                self._streaming_plain = ""
            self._streaming_plain += chunk
            with self._typing_sync.lock:
                self._typing_sync.displayed = len(self._streaming_plain)
            self._update_pending_label_html()
            self._release_stream_segments_if_caught_up()
            self._scroll_to_bottom()
            if not self._stream_motion_once:
                self._stream_motion_once = True
                self.parent.live2d_view.play_tap_interaction()
            if not self._type_buffer:
                self._type_timer.stop()
                self._after_type_buffer_drained()
            return

        self._type_timer.stop()
        self._after_type_buffer_drained()

    def _after_type_buffer_drained(self) -> None:
        if self._post_typewriter_text is not None:
            txt = self._post_typewriter_text
            audio = self._post_typewriter_audio
            self._post_typewriter_text = None
            self._post_typewriter_audio = None
            dur = wav_duration_seconds(audio) if audio else None
            self.parent.live2d_view.begin_lip_sync_for_text(txt, duration_sec=dur)
            if audio:
                play_wav_bytes_async(audio)
            if not self._stream_motion_once:
                self._stream_motion_once = True
                self.parent.live2d_view.play_tap_interaction()
        if self._pipeline_done and not self._type_buffer:
            self._finalize_assistant_turn()

    def _finalize_assistant_turn(self) -> None:
        if self._assistant_finalize_done:
            return
        self._assistant_finalize_done = True
        plain_emo = (getattr(self, "_emotion_plain_source", "") or "").strip()
        if not plain_emo:
            plain_emo = (getattr(self, "_streaming_plain", "") or "").strip()
        self._type_timer.stop()
        self._set_input_busy(False)
        th = self._chat_thread
        self._chat_thread = None
        self._wait_timer.stop()
        self._pending_reply_label = None
        self.parent.live2d_view.apply_emotion_for_assistant_text(plain_emo)
        self._emotion_plain_source = ""
        for name in ("_streaming_plain", "_type_buffer"):
            if hasattr(self, name):
                delattr(self, name)
        self._stream_motion_once = False
        self._pipeline_done = False
        self._post_typewriter_audio = None
        self._post_typewriter_text = None
        self._typing_sync = None
        self._stream_segment_ui_events = None
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        self._scroll_to_bottom()
        if th is not None:
            th.deleteLater()
        self._sync_interrupt_button_state()

    @Slot()
    def _on_pipeline_interrupted(self) -> None:
        """스트림 워커가 사용자 중단으로 조기 종료했을 때."""
        sender = self.sender()
        if sender is not self._chat_thread:
            self._discard_thread_later(sender)
            return
        self._type_timer.stop()
        stop_playback()
        self.parent.live2d_view.stop_lip_sync()
        self._wait_timer.stop()
        self._set_input_busy(False)
        th = self._chat_thread
        self._chat_thread = None
        plain = getattr(self, "_streaming_plain", "") or ""
        if plain.strip():
            interrupt_plain = plain.rstrip() + "\n[중단됨]"
        else:
            interrupt_plain = "[중단됨]"
        if self._pending_reply_label:
            self._pending_reply_label.setText(
                self._assistant_bubble_label_html(interrupt_plain)
            )
        self._pending_reply_label = None
        self._stream_motion_once = False
        self._pipeline_done = False
        self._typing_sync = None
        self._stream_segment_ui_events = None
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        self._post_typewriter_audio = None
        self._post_typewriter_text = None
        for name in ("_streaming_plain", "_type_buffer"):
            if hasattr(self, name):
                delattr(self, name)
        self._assistant_finalize_done = True
        self._scroll_to_bottom()
        if th is not None:
            th.deleteLater()
        self._sync_interrupt_button_state()

    def _touch_main_window_user_activity(self) -> None:
        mw = self.window()
        if is_app_main_window(mw) and hasattr(mw, "touch_user_activity"):
            mw.touch_user_activity()

    def is_pipeline_busy(self) -> bool:
        if not self.input_field.isEnabled():
            return True
        if self._type_timer.isActive():
            return True
        if self._chat_thread is not None and self._chat_thread.isRunning():
            return True
        return False

    def clear_conversation_ui(self) -> None:
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def load_history_messages(self, messages: list[dict[str, str]]) -> None:
        self.clear_conversation_ui()
        aname = self._assistant_display_name()
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip()
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                body = html.escape(content).replace("\n", "<br/>")
                self.add_message("User", body, is_user=True)
            elif role == "assistant":
                cfg = getattr(self.parent, "config", None) or {}
                name_sp = self._assistant_name_span_html()
                styled = assistant_thinking_display_body_html(
                    content,
                    cfg,
                    think_color=self._c_muted,
                    body_color=self._c_body,
                    name_span_before_answer=name_sp,
                )
                if styled is not None:
                    self.add_message(
                        "",
                        styled,
                        is_user=False,
                        assistant_row_html_complete=True,
                    )
                else:
                    body = html.escape(content).replace("\n", "<br/>")
                    self.add_message(aname, body, is_user=False)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def add_message(
        self,
        sender,
        text,
        is_user=True,
        *,
        assistant_row_html_complete: bool = False,
    ):
        if assistant_row_html_complete:
            msg_label = QLabel(text)
            msg_label.setTextFormat(Qt.TextFormat.RichText)
            msg_label.setWordWrap(True)
            msg_label.setStyleSheet("background: transparent;")
            self.history_layout.addWidget(msg_label)
            self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            )
            return
        esc = html.escape(sender)
        name_c = self._c_user if is_user else self._c_assist
        msg_label = QLabel(
            f'<span style="color:{name_c};font-weight:bold;">{esc}:</span> '
            f'<span style="color:{self._c_body};">{text}</span>'
        )
        msg_label.setTextFormat(Qt.TextFormat.RichText)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("background: transparent;")
        self.history_layout.addWidget(msg_label)

        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _scroll_to_bottom(self) -> None:
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _detach_chat_worker_signals(self, th: QThread | None) -> None:
        """이전 스트림 워커의 시그널을 끊어 새 전송과 겹치지 않게 합니다."""
        if th is None:
            return
        try:
            th.disconnect(self)
        except (RuntimeError, TypeError):
            pass

    @staticmethod
    def _discard_thread_later(th) -> None:
        if not isinstance(th, QThread):
            return
        if not th.isRunning():
            th.deleteLater()
        else:
            th.finished.connect(th.deleteLater)

    def _set_input_busy(self, busy: bool) -> None:
        self.input_field.setEnabled(not busy)
        self.btn_send.setEnabled(not busy)
        self.btn_attach.setEnabled(not busy)
        self._sync_interrupt_button_state()

    def _sync_interrupt_button_state(self) -> None:
        """처리 중이거나 타자 효과 재생 중이면 중단 버튼을 켭니다."""
        active = (
            not self.input_field.isEnabled()
            or self._type_timer.isActive()
            or (
                self._chat_thread is not None
                and self._chat_thread.isRunning()
            )
        )
        self.btn_interrupt.setEnabled(active)

    def request_interrupt(self) -> None:
        """LLM 스트림·TTS·오디오·타이핑·립싱크를 끊습니다."""
        self._stop_pipeline.set()
        stop_playback()
        self.parent.live2d_view.stop_lip_sync()
        evs = self._stream_segment_ui_events
        if evs is not None:
            for ev in evs.values():
                ev.set()

        had_typing_timer = self._type_timer.isActive()
        buf = getattr(self, "_type_buffer", "") or ""
        self._type_timer.stop()

        thread_running = (
            self._chat_thread is not None and self._chat_thread.isRunning()
        )
        if thread_running:
            self._chat_thread.requestInterruption()
        elif self._pending_reply_label and not self._assistant_finalize_done:
            if had_typing_timer or buf or (getattr(self, "_streaming_plain", "") or "").strip():
                self._post_typewriter_audio = None
                self._post_typewriter_text = None
                plain = getattr(self, "_streaming_plain", "") or ""
                if plain.strip():
                    self._finish_pending_assistant(plain.rstrip() + "\n[중단됨]")
                else:
                    self._finish_pending_assistant("[중단됨]")
                self._finalize_assistant_turn()
        self._set_input_busy(False)
        self._sync_interrupt_button_state()

    def _add_pending_assistant(self) -> None:
        self._pending_reply_label = QLabel()
        self._pending_reply_label.setWordWrap(True)
        self._pending_reply_label.setTextFormat(Qt.TextFormat.RichText)
        self._pending_reply_label.setStyleSheet("background: transparent;")
        self._wait_phase = 0
        self._animate_waiting()
        self._wait_timer.start(450)
        self.history_layout.addWidget(self._pending_reply_label)
        self._scroll_to_bottom()

    def _animate_waiting(self) -> None:
        if not self._pending_reply_label:
            return
        self._wait_phase = (self._wait_phase + 1) % 3
        dots = "." * (self._wait_phase + 1)
        self._pending_reply_label.setText(
            self._assistant_name_span_html()
            + f'<span style="color:{self._c_muted};font-style:italic;">답변 생성 중{dots}</span>'
        )
        self._scroll_to_bottom()

    def _finish_pending_assistant(self, text: str) -> None:
        self._wait_timer.stop()
        if self._pending_reply_label:
            self._pending_reply_label.setText(
                self._assistant_bubble_label_html(text or "")
            )
            self._pending_reply_label = None
        self._scroll_to_bottom()

    def _on_response_ready(self, response_text: str, audio: bytes) -> None:
        self._wait_timer.stop()
        th = self._chat_thread
        self._chat_thread = None
        if self._stop_pipeline.is_set():
            self._set_input_busy(False)
            if self._pending_reply_label:
                self._finish_pending_assistant("[중단됨]")
            if th is not None:
                th.deleteLater()
            self._sync_interrupt_button_state()
            return
        raw = (response_text or "").strip()
        if not raw:
            self._set_input_busy(False)
            if th is not None:
                th.deleteLater()
            return
        cfg = getattr(self.parent, "config", None) or {}
        clean = assistant_history_plain(raw, cfg).strip()
        self._emotion_plain_source = raw
        self._streaming_plain = ""
        self._type_buffer = clean
        self._pipeline_done = True
        self._post_typewriter_text = clean
        self.parent.live2d_view.apply_emotion_for_assistant_text(raw)
        if isinstance(audio, (bytes, bytearray)) and len(audio) > 0:
            self._post_typewriter_audio = bytes(audio)
        else:
            self._post_typewriter_audio = None
        self._ensure_type_timer()
        if th is not None:
            th.deleteLater()

    def _on_response_failed(self, err: str) -> None:
        self._type_timer.stop()
        self._set_input_busy(False)
        th = self._chat_thread
        self._chat_thread = None
        self._assistant_finalize_done = True
        self._typing_sync = None
        self._type_buffer = ""
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        msg = f"[오류] {err}"
        self._finish_pending_assistant(msg)
        self.parent.live2d_view.begin_lip_sync_for_text(msg)
        if th is not None:
            th.deleteLater()

    @Slot(int, str, QByteArray)
    def _prepare_playback_segment(
        self, invoke_gen: int, text: str, audio_qba: QByteArray
    ) -> None:
        """재생 스레드에서 BlockingQueuedConnection으로 호출: 립싱크만 메인에서 맞춤."""
        if int(invoke_gen) != self._stream_invoke_gen:
            return
        raw = bytes(audio_qba) if audio_qba is not None else b""
        dur = wav_duration_seconds(raw) if raw else None
        self.parent.live2d_view.begin_lip_sync_for_text(text, duration_sec=dur)

    @Slot(int, int, str, QByteArray)
    def _on_stream_tts_segment_ready(
        self, invoke_gen: int, idx: int, text: str, audio_qba: QByteArray
    ) -> None:
        """TTS 완료 시: 텍스트는 text_batch로 이미 큐에 있음. 빈 구간만 재생 잠금 해제."""
        if int(invoke_gen) != self._stream_invoke_gen:
            return
        self._wait_timer.stop()
        if not (text or "").strip():
            evs = self._stream_segment_ui_events
            if evs is not None and idx in evs:
                evs[idx].set()
            return

    @Slot(int, str)
    def _on_assistant_raw_progress(self, invoke_gen: int, rolled: str) -> None:
        if int(invoke_gen) != self._stream_invoke_gen:
            return
        self.parent.live2d_view.apply_emotion_for_assistant_text(rolled)

    @Slot(int, int, int)
    def _schedule_stream_tts_segment(
        self, invoke_gen: int, segment_idx: int, release_after_len: int
    ) -> None:
        """사고 모드 스트리밍: 전체 답이 타이핑된 뒤에만 TTS 구간 재생이 풀리도록 잠금 길이 등록."""
        if int(invoke_gen) != self._stream_invoke_gen:
            return
        self._stream_segment_release_at[int(segment_idx)] = int(release_after_len)
        self._release_stream_segments_if_caught_up()

    @Slot(int, int, str)
    def _on_text_batch(self, invoke_gen: int, segment_idx: int, batch: str) -> None:
        if int(invoke_gen) != self._stream_invoke_gen:
            return
        self._wait_timer.stop()
        if segment_idx >= 0:
            self._stream_typing_cumulative += len(batch or "")
            self._stream_segment_release_at[segment_idx] = self._stream_typing_cumulative
            if (batch or "").strip() and not self._stream_motion_once:
                self._stream_motion_once = True
                self.parent.live2d_view.play_tap_interaction()
        self._type_buffer += batch or ""
        self._ensure_type_timer()

    def _on_stream_finished(self, full: str) -> None:
        if self.sender() is not self._chat_thread:
            self._discard_thread_later(self.sender())
            return
        self._emotion_plain_source = (full or "").strip()
        self._pipeline_done = True
        self._ensure_type_timer()
        if not self._type_buffer:
            self._after_type_buffer_drained()

    def _on_stream_failed(self, err: str) -> None:
        if self.sender() is not self._chat_thread:
            self._discard_thread_later(self.sender())
            return
        self._type_timer.stop()
        self._set_input_busy(False)
        th = self._chat_thread
        self._chat_thread = None
        self._wait_timer.stop()
        esc = html.escape(err)
        if self._pending_reply_label:
            self._pending_reply_label.setText(
                self._assistant_name_span_html()
                + f'<span style="color:{self._c_err};">[오류] {esc}</span>'
            )
        self._pending_reply_label = None
        if hasattr(self, "_streaming_plain"):
            delattr(self, "_streaming_plain")
        self._type_buffer = ""
        self._stream_motion_once = False
        self._pipeline_done = False
        self._typing_sync = None
        self._stream_segment_ui_events = None
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        self._assistant_finalize_done = True
        self._scroll_to_bottom()
        self.parent.live2d_view.begin_lip_sync_for_text(f"[오류] {err}")
        if th is not None:
            th.deleteLater()

    def send_message(self) -> None:
        if self._chat_thread is not None and self._chat_thread.isRunning():
            if self._stop_pipeline.is_set():
                self._detach_chat_worker_signals(self._chat_thread)
                self._chat_thread = None
            else:
                return

        text = self.input_field.text().strip()
        pending = list(self._pending_attachments)
        mw = self.window()
        if is_app_main_window(mw):
            extra = mw.current_screen_share_attachment_for_llm()
            if extra is not None:
                while len(pending) >= MAX_LLM_ATTACHMENTS:
                    pending.pop(0)
                pending.append(extra)
        if not text and not pending:
            return

        self._touch_main_window_user_activity()

        if text:
            display = html.escape(text)
        else:
            display = '<span style="color:#666;">(첨부만 전송)</span>'
        if pending:
            esc_names = ", ".join(html.escape(a.original_name) for a in pending)
            display += (
                f"<br/><span style=\"color:{self._c_muted};font-size:11px;\">첨부: {esc_names}</span>"
            )

        self.add_message("User", display, is_user=True)
        self.input_field.clear()
        self._pending_attachments.clear()
        self._refresh_attach_badge()
        self._stop_pipeline.clear()
        self._add_pending_assistant()
        self._set_input_busy(True)
        self._stream_motion_once = False
        self._reload_typing_config()
        self._typing_sync = TypingSyncState()
        self._type_buffer = ""
        self._streaming_plain = ""
        self._emotion_plain_source = ""
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        self._pipeline_done = False
        self._post_typewriter_audio = None
        self._post_typewriter_text = None
        self._assistant_finalize_done = False
        self.parent.live2d_view.clear_emotion_dedup()

        llm_cfg = self.parent.config.get("llm", {})
        use_stream = bool(llm_cfg.get("stream_enabled", True))

        if use_stream:
            self._stream_invoke_gen += 1
            stream_gen = self._stream_invoke_gen
            self._chat_thread = _StreamChatWorkerThread(
                self.parent.vmate_manager,
                text,
                self,
                self._stop_pipeline,
                pending,
                stream_gen,
                None,
                self,
            )
            self._chat_thread.text_batch.connect(self._on_text_batch)
            self._chat_thread.assistant_raw_progress.connect(
                self._on_assistant_raw_progress
            )
            self._chat_thread.stream_finished.connect(self._on_stream_finished)
            self._chat_thread.stream_failed.connect(self._on_stream_failed)
            self._chat_thread.pipeline_interrupted.connect(
                self._on_pipeline_interrupted
            )
            self._chat_thread.start()
        else:
            self._chat_thread = _LLMChatWorkerThread(
                self.parent.vmate_manager,
                text,
                self._stop_pipeline,
                pending,
                None,
                self,
            )
            self._chat_thread.response_ready.connect(self._on_response_ready)
            self._chat_thread.response_failed.connect(self._on_response_failed)
            self._chat_thread.start()

    def send_idle_proactive_message(self) -> bool:
        """설정된 유휴 시간 경과 시 메인 창 타이머에서 호출. 일반 전송과 동일한 첨부 규칙."""
        if self._chat_thread is not None and self._chat_thread.isRunning():
            if self._stop_pipeline.is_set():
                self._detach_chat_worker_signals(self._chat_thread)
                self._chat_thread = None
            else:
                return False

        llm_text = _IDLE_PROACTIVE_LLM_TEXT
        pending: list[LLMMediaAttachment] = []
        mw = self.window()
        if is_app_main_window(mw):
            extra = mw.current_screen_share_attachment_for_llm()
            if extra is not None:
                while len(pending) >= MAX_LLM_ATTACHMENTS:
                    pending.pop(0)
                pending.append(extra)

        hist_line = format_user_text_for_history("(유휴) 캐릭터가 먼저 말함", pending)
        display = f'<span style="color:{self._c_muted};">(유휴 · 캐릭터가 먼저 말함)</span>'
        if pending:
            esc_names = ", ".join(html.escape(a.original_name) for a in pending)
            display += (
                f"<br/><span style=\"color:{self._c_muted};font-size:11px;\">첨부: {esc_names}</span>"
            )

        self.add_message("User", display, is_user=True)
        self._stop_pipeline.clear()
        self._add_pending_assistant()
        self._set_input_busy(True)
        self._stream_motion_once = False
        self._reload_typing_config()
        self._typing_sync = TypingSyncState()
        self._type_buffer = ""
        self._streaming_plain = ""
        self._emotion_plain_source = ""
        self._stream_segment_release_at.clear()
        self._stream_typing_cumulative = 0
        self._pipeline_done = False
        self._post_typewriter_audio = None
        self._post_typewriter_text = None
        self._assistant_finalize_done = False
        self.parent.live2d_view.clear_emotion_dedup()

        llm_cfg = self.parent.config.get("llm", {})
        use_stream = bool(llm_cfg.get("stream_enabled", True))

        if use_stream:
            self._stream_invoke_gen += 1
            stream_gen = self._stream_invoke_gen
            self._chat_thread = _StreamChatWorkerThread(
                self.parent.vmate_manager,
                llm_text,
                self,
                self._stop_pipeline,
                pending,
                stream_gen,
                hist_line,
                self,
            )
            self._chat_thread.text_batch.connect(self._on_text_batch)
            self._chat_thread.assistant_raw_progress.connect(
                self._on_assistant_raw_progress
            )
            self._chat_thread.stream_finished.connect(self._on_stream_finished)
            self._chat_thread.stream_failed.connect(self._on_stream_failed)
            self._chat_thread.pipeline_interrupted.connect(
                self._on_pipeline_interrupted
            )
            self._chat_thread.start()
        else:
            self._chat_thread = _LLMChatWorkerThread(
                self.parent.vmate_manager,
                llm_text,
                self._stop_pipeline,
                pending,
                hist_line,
                self,
            )
            self._chat_thread.response_ready.connect(self._on_response_ready)
            self._chat_thread.response_failed.connect(self._on_response_failed)
            self._chat_thread.start()
        return True

