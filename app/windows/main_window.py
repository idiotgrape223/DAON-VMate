from __future__ import annotations

import glob
import logging
import os
import sys
from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QGuiApplication,
    QPalette,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.styles import (
    LIVE2D_TOP_BAR_HEIGHT_PX,
    LIVE2D_TOP_BAR_LIGHT_QSS,
    LIVE2D_TOP_BAR_QSS,
)
from app.widgets.chat_history_sidebar import ChatHistorySidebar
from app.widgets.chat_widget import ChatWidget
from app.widgets.live2d_widget import Live2DWidget
from config.config_loader import load_config, save_config
from core.chat_session_store import (
    create_empty_session,
    default_session_title,
    delete_session,
    list_sessions,
    load_session_messages,
    read_last_active_session_id,
    rename_session,
    save_session,
    write_last_active_session_id,
)
from core.llm_attachments import LLMMediaAttachment
from core.mcp_client import MCPClientService
from core.model_profile import repo_root
from core.tts_engine import TTSEngine
from core.vmate_manager import VMateManager
from ui.screen_share import (
    WindowPickerDialog,
    grab_full_virtual_desktop,
    grab_monitor,
    grab_native_window,
    list_visible_windows_win32,
    pixmap_to_llm_attachment,
)
from ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """히스토리 저장은 LLM 워커 스레드에서 트리거되므로 Signal로 메인 스레드에만 큐잉."""

    _persist_chat_requested = Signal()

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.mcp_client = MCPClientService(repo_root())
        self.mcp_client.apply_config(self.config)
        self.vmate_manager = VMateManager(self.config)
        self.vmate_manager.set_mcp_client(self.mcp_client)
        self._persist_chat_requested.connect(
            self._persist_active_chat_session,
            Qt.ConnectionType.QueuedConnection,
        )
        self.vmate_manager.add_history_listener(self._schedule_persist_chat)
        self._active_chat_session_id: str | None = None
        self._active_session_title: str = ""
        self._chat_sessions_bound_model: str | None = None
        if self.config.get("tts", {}).get("provider") == "edge-tts":
            _edge_ok, _edge_msg = TTSEngine.edge_tts_dependency_status()
            if not _edge_ok:
                _msg = _edge_msg

                def _show_edge_warn() -> None:
                    QMessageBox.warning(self, "TTS (edge-tts)", _msg)

                QTimer.singleShot(0, _show_edge_warn)
        ui_config = self.config.get('ui', {})
        self._desktop_pet_mode = False
        self._pet_floating_chat_placed = False
        self._saved_window_flags: Optional[int] = None
        self._normal_geometry = None

        self.setWindowTitle("DAON-VMate")
        self.resize(ui_config.get('window_width', 1280), ui_config.get('window_height', 720))

        if ui_config.get("always_on_top", False):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)
        root_layout = QHBoxLayout(self._central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._live2d_area = QWidget()
        self._live2d_area.setObjectName("live2dAreaShell")
        live_col = QVBoxLayout(self._live2d_area)
        live_col.setContentsMargins(0, 0, 0, 0)
        live_col.setSpacing(0)

        self._screen_share_active = False
        self._screen_share_mode = ""
        self._screen_share_screen = None
        self._screen_share_hwnd: int | None = None
        self._screen_share_attachment: LLMMediaAttachment | None = None
        self._screen_share_timer = QTimer(self)
        self._screen_share_timer.setInterval(800)
        self._screen_share_timer.timeout.connect(self._tick_screen_share_capture)

        self.live2d_view = Live2DWidget(self._live2d_area)
        self.chat_widget = ChatWidget(self)

        live_col.addWidget(self.live2d_view, 1)

        self._chat_history_sidebar = ChatHistorySidebar(self)
        self._chat_history_sidebar.setMinimumWidth(180)
        self._chat_history_sidebar.setMaximumWidth(560)

        self._main_splitter = QSplitter(
            Qt.Orientation.Horizontal, self._central_widget
        )
        self._main_splitter.setObjectName("mainChatSplitter")
        self._main_splitter.setHandleWidth(6)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.addWidget(self._chat_history_sidebar)
        self._main_splitter.addWidget(self._live2d_area)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(self._main_splitter)

        _sw = int(ui_config.get("chat_sidebar_width", 252))
        _sw = max(180, min(560, _sw))
        _rest = max(400, self.width() - _sw - self._main_splitter.handleWidth())
        self._main_splitter.setSizes([_sw, _rest])

        self._top_bar = QWidget(self._live2d_area)
        self._top_bar.setObjectName("live2dTopBar")
        self._top_bar.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._top_bar.setStyleSheet(LIVE2D_TOP_BAR_QSS)
        bar_layout = QHBoxLayout(self._top_bar)
        bar_layout.setContentsMargins(16, 8, 16, 8)
        bar_layout.setSpacing(10)

        self.btn_screen_share = QPushButton("화면 공유")
        self.btn_screen_share.setToolTip(
            "전체 화면·모니터·창(Windows)을 주기적으로 캡처합니다. "
            "메시지를 보낼 때마다 가장 최근 화면이 비전 LLM 첨부로 포함됩니다."
        )
        self._screen_share_menu = QMenu(self)
        self._screen_share_menu.aboutToShow.connect(self._populate_screen_share_menu)
        self.btn_screen_share.setMenu(self._screen_share_menu)

        self.btn_desktop_pet = QPushButton("캐릭터 모드")
        self.btn_desktop_pet.setToolTip(
            "데스크톱 캐릭터 모드: 무테 창과 Live2D 알파(배경 투명) 합성. "
            "왼쪽 짧은 클릭: 탭 모션 + 플로팅 채팅 토글. 드래그: 시점, Shift+드래그: 창 이동, 휠: 크기. "
            "우클릭: 표정·모션 메뉴(채팅 토글·채팅 모드 복귀 포함). Esc: 입력창만 닫기."
        )
        self.btn_desktop_pet.clicked.connect(self.enter_desktop_pet_mode)

        self.btn_chat_history = QPushButton("대화 기록")
        self.btn_chat_history.setObjectName("chatHistoryToggleBtn")
        self.btn_chat_history.setCheckable(True)
        self.btn_chat_history.setChecked(True)
        self.btn_chat_history.setToolTip(
            "왼쪽 대화 기록 패널을 표시하거나 숨깁니다. (너비는 패널 오른쪽 가장자리를 드래그해 조절)"
        )
        self.btn_chat_history.clicked.connect(self._on_chat_history_sidebar_toggled)

        self.btn_settings = QPushButton("설정")
        self.btn_settings.clicked.connect(self.open_settings)

        bar_layout.addWidget(self.btn_screen_share)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self.btn_desktop_pet)
        bar_layout.addWidget(self.btn_chat_history)
        bar_layout.addWidget(self.btn_settings)

        self._sidebar_saved_width: int | None = None
        self._chat_sidebar_visible_before_pet = True
        self._chat_sidebar_toggle_checked_before_pet = True

        _tb_font = QFont()
        _tb_font.setPointSize(10)
        _tb_font.setWeight(QFont.Weight.DemiBold)
        for _b in (
            self.btn_screen_share,
            self.btn_desktop_pet,
            self.btn_chat_history,
            self.btn_settings,
        ):
            _b.setFont(_tb_font)

        self.chat_widget.setParent(self._live2d_area)
        self.chat_widget.resize(600, 200)
        self._live2d_area.installEventFilter(self)
        self._layout_overlay_widgets()

        self._apply_normal_window_chrome_opaque()
        self._apply_live2d_alpha_overlay(False)

        self._apply_dark_mode_from_config(reload_chat=False)

        QTimer.singleShot(0, self.reload_live2d)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "_live2d_area", None) and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._sync_live2d_overlays)
            return False
        if event.type() == QEvent.Type.MouseMove:
            if not bool(self.config.get("ui", {}).get("mouse_tracking", True)):
                self.live2d_view.release_pointer_target()
                return False
            gp = event.globalPosition().toPoint()
            lp = self.live2d_view.mapFromGlobal(gp)
            if self.live2d_view.rect().contains(lp):
                self.live2d_view.set_pointer_target(float(lp.x()), float(lp.y()))
            else:
                self.live2d_view.release_pointer_target()
        return False

    def _layout_overlay_widgets(self):
        """오버레이는 QOpenGLWidget 밖(_live2d_area)에 두어 Windows에서 좌표·잔상 깨짐을 피합니다."""
        aw = max(1, self._live2d_area.width())
        ah = max(1, self._live2d_area.height())
        tb_h = LIVE2D_TOP_BAR_HEIGHT_PX
        self._top_bar.setFixedHeight(tb_h)
        self._top_bar.setGeometry(0, 0, aw, tb_h)
        self._top_bar.raise_()
        if getattr(self, "_desktop_pet_mode", False):
            if not getattr(self, "_pet_floating_chat_placed", False):
                self.chat_widget.hide()
            return
        self.chat_widget.resize(600, 200)
        cw = self.chat_widget.width()
        ch = self.chat_widget.height()
        x = max(0, (aw - cw) // 2)
        y = max(0, ah - ch - 16)
        self.chat_widget.move(x, y)
        self.chat_widget.raise_()

    def _sync_live2d_overlays(self) -> None:
        """스플리터·사이드바로 Live2D 영역 너비가 바뀔 때 상단 바·GL·채팅 오버레이를 다시 맞춤 (잔상 방지)."""
        if getattr(self, "_desktop_pet_mode", False):
            self._layout_overlay_widgets()
            return
        self._layout_overlay_widgets()
        if hasattr(self, "_top_bar"):
            self._top_bar.raise_()
            self._top_bar.repaint()
            for b in (
                getattr(self, "btn_screen_share", None),
                getattr(self, "btn_desktop_pet", None),
                getattr(self, "btn_chat_history", None),
                getattr(self, "btn_settings", None),
            ):
                if b is not None:
                    b.update()
        if hasattr(self, "chat_widget") and self.chat_widget.isVisible():
            self.chat_widget.raise_()
            self.chat_widget.update()
        if hasattr(self, "live2d_view"):
            if self.live2d_view.model and self.live2d_view._gl_ready:
                self.live2d_view.makeCurrent()
                try:
                    self.live2d_view.model.Resize(
                        max(self.live2d_view.width(), 1),
                        max(self.live2d_view.height(), 1),
                    )
                finally:
                    self.live2d_view.doneCurrent()
            self.live2d_view.update()
            self.live2d_view.repaint()
        if hasattr(self, "_central_widget"):
            self._central_widget.update()
        if hasattr(self, "_chat_history_sidebar"):
            self._chat_history_sidebar.update()

    def _on_chat_history_sidebar_toggled(self, checked: bool) -> None:
        """상단 '대화 기록' 버튼: 왼쪽 히스토리 패널 표시/숨김."""
        if not hasattr(self, "_chat_history_sidebar") or not hasattr(
            self, "_main_splitter"
        ):
            return
        side = self._chat_history_sidebar
        sp = self._main_splitter
        if checked:
            side.show()
            sw = self._sidebar_saved_width
            if sw is None or sw < 180:
                sw = int(self.config.get("ui", {}).get("chat_sidebar_width", 252))
            sw = max(180, min(560, int(sw)))
            total = max(400, sp.width())
            handle = sp.handleWidth()
            rest = max(200, total - sw - handle)
            sp.setSizes([sw, rest])
        else:
            sizes = sp.sizes()
            if sizes and sizes[0] >= 180:
                self._sidebar_saved_width = sizes[0]
            side.hide()
        QTimer.singleShot(0, self._sync_live2d_overlays)
        QTimer.singleShot(48, self._sync_live2d_overlays)

    def show_pet_floating_chat_at(self, lx: int, ly: int) -> None:
        """캐릭터 모드 모드: 클릭 지점 근처에 입력창만(히스토리 숨김) 표시."""
        if not getattr(self, "_desktop_pet_mode", False):
            return
        lv_w = max(1, self._live2d_area.width())
        lv_h = max(1, self._live2d_area.height())
        cw = min(560, max(300, lv_w - 16))
        ch = 56
        self.chat_widget.set_pet_compact_mode(True)
        self.chat_widget.resize(cw, ch)
        cx = int(lx) - cw // 2
        cy = int(ly) - 4
        cx = max(4, min(cx, lv_w - cw - 4))
        cy = max(4, min(cy, lv_h - ch - 4))
        self.chat_widget.move(cx, cy)
        self._pet_floating_chat_placed = True
        self.chat_widget.show()
        self.chat_widget.raise_()
        self.chat_widget.input_field.setFocus(Qt.FocusReason.MouseFocusReason)

    def hide_pet_floating_chat(self) -> None:
        if not getattr(self, "_desktop_pet_mode", False):
            return
        self._pet_floating_chat_placed = False
        self.chat_widget.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_overlay_widgets()
        if self.live2d_view.model and self.live2d_view._gl_ready:
            self.live2d_view.makeCurrent()
            try:
                self.live2d_view.model.Resize(
                    max(self.live2d_view.width(), 1),
                    max(self.live2d_view.height(), 1),
                )
            finally:
                self.live2d_view.doneCurrent()

    def open_settings(self):
        SettingsDialog(self).exec()

    def _populate_screen_share_menu(self) -> None:
        m = self._screen_share_menu
        m.clear()
        act_full = m.addAction("전체 화면 (멀티 모니터)")
        act_full.triggered.connect(self._start_screen_share_full)
        sub = m.addMenu("모니터 하나만")
        for i, scr in enumerate(QGuiApplication.screens()):
            label = (scr.name() or "").strip() or f"화면 {i + 1}"
            act_m = sub.addAction(label)
            act_m.triggered.connect(
                lambda _=False, s=scr: self._start_screen_share_monitor(s)
            )
        act_win = m.addAction("창 선택…")
        if sys.platform == "win32":
            act_win.triggered.connect(self._screen_share_pick_window)
        else:
            act_win.setEnabled(False)
            act_win.setToolTip("창 단위 캡처는 Windows에서만 지원합니다.")
        m.addSeparator()
        act_stop = m.addAction("공유 중지")
        act_stop.setEnabled(self._screen_share_active)
        act_stop.triggered.connect(self._stop_screen_share)

    def _stop_screen_share(self) -> None:
        self._screen_share_timer.stop()
        self._screen_share_active = False
        self._screen_share_mode = ""
        self._screen_share_screen = None
        self._screen_share_hwnd = None
        self._screen_share_attachment = None
        self.btn_screen_share.setText("화면 공유")

    def _arm_screen_share(self) -> None:
        self._screen_share_active = True
        self.btn_screen_share.setText("공유 중")
        self._tick_screen_share_capture()
        self._screen_share_timer.start()

    def _start_screen_share_full(self) -> None:
        self._screen_share_mode = "full"
        self._screen_share_screen = None
        self._screen_share_hwnd = None
        self._arm_screen_share()

    def _start_screen_share_monitor(self, screen: QScreen) -> None:
        self._screen_share_mode = "monitor"
        self._screen_share_screen = screen
        self._screen_share_hwnd = None
        self._arm_screen_share()

    def _start_screen_share_hwnd(self, hwnd: int) -> None:
        self._screen_share_mode = "window"
        self._screen_share_screen = None
        self._screen_share_hwnd = int(hwnd)
        self._arm_screen_share()

    def _screen_share_pick_window(self) -> None:
        wins = list_visible_windows_win32()
        if not wins:
            QMessageBox.information(
                self,
                "화면 공유",
                "선택할 수 있는 창이 없습니다.",
            )
            return
        dlg = WindowPickerDialog(self, wins)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        h = dlg.selected_hwnd()
        if h is not None:
            self._start_screen_share_hwnd(h)

    def _tick_screen_share_capture(self) -> None:
        pm = QPixmap()
        try:
            if self._screen_share_mode == "full":
                pm = grab_full_virtual_desktop()
            elif (
                self._screen_share_mode == "monitor"
                and self._screen_share_screen is not None
            ):
                pm = grab_monitor(self._screen_share_screen)
            elif (
                self._screen_share_mode == "window"
                and self._screen_share_hwnd is not None
            ):
                pm = grab_native_window(self._screen_share_hwnd)
        except Exception:
            pm = QPixmap()
        att = pixmap_to_llm_attachment(pm, "screen_share.jpg")
        if att is not None:
            self._screen_share_attachment = att

    def current_screen_share_attachment_for_llm(self) -> Optional[LLMMediaAttachment]:
        if not self._screen_share_active or self._screen_share_attachment is None:
            return None
        a = self._screen_share_attachment
        return LLMMediaAttachment(
            mime_type=a.mime_type,
            raw_bytes=a.raw_bytes,
            original_name=a.original_name,
        )

    def _set_splitter_handle_stylesheet(self) -> None:
        """스플리터 핸들만 테마색 (채팅 모드)."""
        if not hasattr(self, "_main_splitter"):
            return
        dark = bool(self.config.get("ui", {}).get("dark_mode", True))
        if dark:
            self._main_splitter.setStyleSheet(
                """
                QSplitter#mainChatSplitter::handle:horizontal {
                    background-color: #2a2a36;
                    width: 6px;
                    margin: 0px;
                }
                QSplitter#mainChatSplitter::handle:horizontal:hover {
                    background-color: #45475a;
                }
                """
            )
        else:
            self._main_splitter.setStyleSheet(
                """
                QSplitter#mainChatSplitter::handle:horizontal {
                    background-color: #cfd6e0;
                    width: 6px;
                    margin: 0px;
                }
                QSplitter#mainChatSplitter::handle:horizontal:hover {
                    background-color: #a8b4c8;
                }
                """
            )

    def _apply_normal_window_chrome_opaque(self) -> None:
        """채팅 모드: main 브랜치와 동일 — 창은 레이어드 합성 경로, 배경색은 스타일시트로 막음."""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._central_widget.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._central_widget.setAttribute(
            Qt.WidgetAttribute.WA_OpaquePaintEvent, False
        )
        self._central_widget.setAutoFillBackground(False)
        self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")
        self._central_widget.setStyleSheet("background-color: #f0f0f0;")
        if hasattr(self, "_main_splitter"):
            self._set_splitter_handle_stylesheet()

    def _apply_pet_transparent_chrome(self) -> None:
        """캐릭터 모드: main 브랜치와 동일 — 스플리터/쉘 별도 투명 조작 없음."""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._central_widget.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._central_widget.setAttribute(
            Qt.WidgetAttribute.WA_OpaquePaintEvent, False
        )
        self._central_widget.setAutoFillBackground(False)
        self.setStyleSheet(
            "QMainWindow { background-color: rgba(0,0,0,0); border: none; }"
        )
        self._central_widget.setStyleSheet(
            "background-color: rgba(0,0,0,0); border: none;"
        )

    def _apply_live2d_alpha_overlay(self, use_alpha_clear: bool) -> None:
        """
        GL 버퍼 클리어를 투명(a=0) / 불투명으로 전환합니다.
        Live2D 위젯의 WA_TranslucentBackground 등은 __init__에서 고정해 두고 여기서는 건드리지 않습니다.
        """
        self.live2d_view.set_transparent_clear(use_alpha_clear)

    def _refresh_transparency_after_window_state_change(self) -> None:
        """
        Windows 등에서 WA_TranslucentBackground·Frameless 변경 직후 한 프레임 뒤
        합성/GL 상태를 다시 맞춥니다. 채팅 모드는 불투명, 캐릭터 모드 모드만 투명·알파.
        """
        if getattr(self, "_desktop_pet_mode", False):
            self._apply_pet_transparent_chrome()
            self._apply_live2d_alpha_overlay(True)
        else:
            self._apply_normal_window_chrome_opaque()
            self._apply_live2d_alpha_overlay(False)
        self.live2d_view.update()
        self._central_widget.update()
        self.update()

    def _sync_live2d_gl_after_window_surface_change(self) -> None:
        """무테/투명 전환 후 한 틱 뒤 창 속성 + Live2D GL 재바인딩."""
        self._refresh_transparency_after_window_state_change()
        self.live2d_view.recreate_live2d_gl_for_alpha_mode()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._persist_active_chat_session()
        if hasattr(self, "_main_splitter") and self._main_splitter.count() >= 1:
            sizes = self._main_splitter.sizes()
            if sizes and sizes[0] >= 180:
                self.config.setdefault("ui", {})["chat_sidebar_width"] = int(sizes[0])
                save_config(self.config)
        if hasattr(self, "mcp_client"):
            self.mcp_client.stop()
        super().closeEvent(event)

    def _schedule_persist_chat(self) -> None:
        self._persist_chat_requested.emit()

    def _persist_active_chat_session(self) -> None:
        mf = self.current_live2d_model_folder()
        sid = self._active_chat_session_id
        if not mf or not sid:
            return
        save_session(
            repo_root(),
            mf,
            sid,
            self._active_session_title,
            self.vmate_manager.history_snapshot(),
        )

    def current_live2d_model_folder(self) -> str:
        return str(self.config.get("live2d", {}).get("model_folder", "") or "").strip()

    def active_chat_session_id(self) -> str | None:
        return self._active_chat_session_id

    def _bind_session_to_state(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        title: str,
    ) -> None:
        self._active_chat_session_id = session_id
        self._active_session_title = title
        self.vmate_manager.set_chat_history(messages)
        self.chat_widget.load_history_messages(messages)
        write_last_active_session_id(
            repo_root(), self.current_live2d_model_folder(), session_id
        )

    def activate_chat_session(self, session_id: str) -> bool:
        if self.chat_widget.is_pipeline_busy():
            QMessageBox.warning(
                self,
                "대화 기록",
                "응답 생성 중에는 다른 기록으로 바꿀 수 없습니다.",
            )
            return False
        mf = self.current_live2d_model_folder()
        loaded = load_session_messages(repo_root(), mf, session_id)
        if not loaded:
            QMessageBox.warning(self, "대화 기록", "세션을 불러올 수 없습니다.")
            return False
        messages, title = loaded
        self._bind_session_to_state(session_id, messages, title)
        if hasattr(self, "_chat_history_sidebar"):
            self._chat_history_sidebar.select_session(session_id)
        return True

    def new_chat_session(self) -> None:
        if self.chat_widget.is_pipeline_busy():
            QMessageBox.warning(
                self,
                "대화 기록",
                "응답 생성 중에는 새 대화를 시작할 수 없습니다.",
            )
            return
        mf = self.current_live2d_model_folder()
        if not mf:
            QMessageBox.warning(
                self,
                "대화 기록",
                "Live2D 모델 폴더가 없습니다. 설정에서 모델을 지정하세요.",
            )
            return
        try:
            sid, title = create_empty_session(repo_root(), mf)
        except Exception as e:
            QMessageBox.warning(self, "대화 기록", str(e))
            return
        self._bind_session_to_state(sid, [], title)
        self._chat_history_sidebar.refresh_list(select_id=sid)

    def rename_chat_session_interactive(self, session_id: str) -> None:
        mf = self.current_live2d_model_folder()
        loaded = load_session_messages(repo_root(), mf, session_id)
        cur = loaded[1] if loaded else ""
        text, ok = QInputDialog.getText(
            self, "이름 바꾸기", "대화 기록 이름:", text=cur
        )
        if not ok:
            return
        nt = text.strip() or default_session_title()
        if rename_session(repo_root(), mf, session_id, nt):
            if self._active_chat_session_id == session_id:
                self._active_session_title = nt
            self._chat_history_sidebar.refresh_list(
                select_id=self._active_chat_session_id
            )
        else:
            QMessageBox.warning(self, "대화 기록", "이름을 바꾸지 못했습니다.")

    def delete_chat_session_interactive(self, session_id: str) -> None:
        ret = QMessageBox.question(
            self,
            "대화 기록 삭제",
            "이 대화 기록을 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        mf = self.current_live2d_model_folder()
        deleting_current = self._active_chat_session_id == session_id
        if not delete_session(repo_root(), mf, session_id):
            QMessageBox.warning(self, "대화 기록", "삭제하지 못했습니다.")
            return
        if deleting_current:
            rest = list_sessions(repo_root(), mf)
            if rest:
                self.activate_chat_session(rest[0]["id"])
            else:
                try:
                    sid, title = create_empty_session(repo_root(), mf)
                    self._bind_session_to_state(sid, [], title)
                except Exception:
                    self._active_chat_session_id = None
                    self._active_session_title = ""
                    self.vmate_manager.clear_chat_history()
                    self.chat_widget.clear_conversation_ui()
        self._chat_history_sidebar.refresh_list(
            select_id=self._active_chat_session_id
        )

    def _init_chat_sessions_for_current_model(self) -> None:
        mf = self.current_live2d_model_folder()
        side = getattr(self, "_chat_history_sidebar", None)
        if not mf:
            if side:
                side.refresh_list()
            return

        last = read_last_active_session_id(repo_root(), mf)
        loaded = load_session_messages(repo_root(), mf, last) if last else None
        if loaded:
            messages, title = loaded
            self._bind_session_to_state(last, messages, title)
            if side:
                side.refresh_list(select_id=last)
            return

        sl = list_sessions(repo_root(), mf)
        if sl:
            sid = sl[0]["id"]
            lo = load_session_messages(repo_root(), mf, sid)
            if lo:
                messages, title = lo
                self._bind_session_to_state(sid, messages, title)
                if side:
                    side.refresh_list(select_id=sid)
                return

        try:
            sid, title = create_empty_session(repo_root(), mf)
            self._bind_session_to_state(sid, [], title)
            if side:
                side.refresh_list(select_id=sid)
        except Exception:
            if side:
                side.refresh_list()

    def _apply_dark_mode_from_config(self, *, reload_chat: bool) -> None:
        dark = bool(self.config.get("ui", {}).get("dark_mode", True))
        if hasattr(self, "_main_splitter") and not getattr(
            self, "_desktop_pet_mode", False
        ):
            self._set_splitter_handle_stylesheet()
        if hasattr(self, "_chat_history_sidebar"):
            self._chat_history_sidebar.apply_dark_mode(dark)
        if hasattr(self, "chat_widget"):
            self.chat_widget.apply_dark_mode(dark)
        if hasattr(self, "_top_bar"):
            self._top_bar.setStyleSheet(
                LIVE2D_TOP_BAR_QSS if dark else LIVE2D_TOP_BAR_LIGHT_QSS
            )
        if (
            reload_chat
            and hasattr(self, "chat_widget")
            and hasattr(self, "vmate_manager")
            and not self.chat_widget.is_pipeline_busy()
        ):
            self.chat_widget.load_history_messages(
                self.vmate_manager.history_snapshot()
            )

    def _maybe_rebind_chat_sessions_for_model(self) -> None:
        mf = self.current_live2d_model_folder()
        if mf == getattr(self, "_chat_sessions_bound_model", None):
            return
        self._chat_sessions_bound_model = mf
        self._init_chat_sessions_for_current_model()

    def apply_ui_from_config(self):
        if hasattr(self, "mcp_client"):
            self.mcp_client.apply_config(self.config)
        if hasattr(self, "vmate_manager"):
            self.vmate_manager.set_mcp_client(self.mcp_client)
        if hasattr(self, "chat_widget"):
            self.chat_widget.apply_assistant_display_settings()
        self._apply_dark_mode_from_config(reload_chat=True)
        ui = self.config.get("ui", {})
        if not bool(ui.get("mouse_tracking", True)):
            self.live2d_view.release_pointer_target()
        if getattr(self, "_desktop_pet_mode", False):
            return

        w = int(ui.get("window_width", 1280))
        h = int(ui.get("window_height", 720))
        self.resize(w, h)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(ui.get("always_on_top", False)))
        self._apply_normal_window_chrome_opaque()
        self._apply_live2d_alpha_overlay(False)
        self.show()
        QTimer.singleShot(0, self._refresh_transparency_after_window_state_change)

    def enter_desktop_pet_mode(self) -> None:
        if self._desktop_pet_mode:
            return
        ui = self.config.get("ui", {})
        self._normal_geometry = self.geometry()
        self._saved_window_flags = self.windowFlags()

        self._desktop_pet_mode = True
        self._pet_floating_chat_placed = False
        if hasattr(self, "_chat_history_sidebar"):
            self._chat_sidebar_visible_before_pet = (
                self._chat_history_sidebar.isVisible()
            )
            self._chat_history_sidebar.hide()
        if hasattr(self, "btn_chat_history"):
            self._chat_sidebar_toggle_checked_before_pet = (
                self.btn_chat_history.isChecked()
            )
            self.btn_chat_history.hide()
        self.chat_widget.set_pet_compact_mode(False)
        self.chat_widget.hide()
        self.btn_settings.hide()
        self.btn_desktop_pet.hide()
        self.btn_screen_share.hide()
        self._top_bar.hide()

        self._apply_pet_transparent_chrome()
        self._apply_live2d_alpha_overlay(True)

        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)

        self._apply_pet_transparent_chrome()
        self._apply_live2d_alpha_overlay(True)

        pw = int(ui.get("pet_window_width", 520))
        ph = int(ui.get("pet_window_height", 720))
        self.resize(max(200, pw), max(200, ph))
        fg = self.frameGeometry()
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            cp = scr.availableGeometry().center()
            fg.moveCenter(cp)
            self.move(fg.topLeft())

        self.live2d_view.makeCurrent()
        try:
            if self.live2d_view.model:
                self.live2d_view.model.Resize(
                    max(self.live2d_view.width(), 1),
                    max(self.live2d_view.height(), 1),
                )
        finally:
            self.live2d_view.doneCurrent()

        self._layout_overlay_widgets()
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._sync_live2d_gl_after_window_surface_change)

    def exit_desktop_pet_mode(self) -> None:
        if not self._desktop_pet_mode:
            return
        self._desktop_pet_mode = False

        if self._saved_window_flags is not None:
            self.setWindowFlags(self._saved_window_flags)
        self._saved_window_flags = None

        ui = self.config.get("ui", {})
        if self._normal_geometry is not None:
            self.setGeometry(self._normal_geometry)
            self._normal_geometry = None
        else:
            self.resize(
                int(ui.get("window_width", 1280)),
                int(ui.get("window_height", 720)),
            )

        self.setWindowFlag(
            Qt.WindowType.WindowStaysOnTopHint, bool(ui.get("always_on_top", False))
        )
        self._apply_normal_window_chrome_opaque()

        self._apply_live2d_alpha_overlay(False)

        self._pet_floating_chat_placed = False
        self.chat_widget.set_pet_compact_mode(False)
        self.chat_widget.show()
        if hasattr(self, "_chat_history_sidebar"):
            if getattr(self, "_chat_sidebar_visible_before_pet", True):
                self._chat_history_sidebar.show()
                sw = self._sidebar_saved_width
                if sw is None or sw < 180:
                    sw = int(
                        self.config.get("ui", {}).get("chat_sidebar_width", 252)
                    )
                sw = max(180, min(560, int(sw)))
                if hasattr(self, "_main_splitter"):
                    sp = self._main_splitter
                    total = max(400, sp.width())
                    handle = sp.handleWidth()
                    rest = max(200, total - sw - handle)
                    sp.setSizes([sw, rest])
            else:
                self._chat_history_sidebar.hide()
        if hasattr(self, "btn_chat_history"):
            self.btn_chat_history.show()
            self.btn_chat_history.setChecked(
                getattr(self, "_chat_sidebar_toggle_checked_before_pet", True)
            )
        self.btn_settings.show()
        self.btn_desktop_pet.show()
        self.btn_screen_share.show()
        self._top_bar.show()

        self.live2d_view.makeCurrent()
        try:
            if self.live2d_view.model:
                self.live2d_view.model.Resize(
                    max(self.live2d_view.width(), 1),
                    max(self.live2d_view.height(), 1),
                )
        finally:
            self.live2d_view.doneCurrent()

        self._layout_overlay_widgets()
        self.show()
        self.config.setdefault("ui", {}).pop("desktop_pet_mode", None)
        QTimer.singleShot(0, self._sync_live2d_gl_after_window_surface_change)

    def get_available_models(self):
        base = repo_root()
        models_dir = os.path.join(base, "assets", "live2d-models")

        def scan_dir(root):
            found = []
            if not os.path.isdir(root):
                return found
            for folder in os.listdir(root):
                folder_path = os.path.join(root, folder)
                if not os.path.isdir(folder_path):
                    continue
                json_files = glob.glob(
                    os.path.join(folder_path, '**', '*.model3.json'), recursive=True
                )
                if not json_files:
                    json_files = glob.glob(
                        os.path.join(folder_path, '**', '*.model.json'), recursive=True
                    )
                if json_files:
                    found.append({"folder_name": folder, "json_path": json_files[0]})
            return found

        return scan_dir(models_dir)

    def reload_live2d(self):
        folder_name = self.config.get('live2d', {}).get('model_folder', 'shizuku')
        scale = self.config.get('live2d', {}).get('scale', 0.25)

        models = self.get_available_models()
        target_model = next((m for m in models if m['folder_name'] == folder_name), None)
        if not target_model and models:
            target_model = models[0]
            logger.warning(
                "설정 폴더 '%s' 없음, '%s' 로 대체합니다.",
                folder_name,
                target_model["folder_name"],
            )
        if target_model:
            # emotionMap·프로필은 실제 로드된 모델 폴더와 맞춤(폴백 시 설정명과 불일치 방지)
            loaded_folder = str(target_model["folder_name"] or "").strip()
            self.live2d_view.load_model(
                target_model["json_path"],
                scale,
                folder_name=loaded_folder or folder_name,
            )
        else:
            logger.warning(
                "사용 가능한 모델이 없습니다. 설정에서 폴더를 불러오거나 "
                "assets/live2d-models 에 모델을 넣으세요."
            )
        if hasattr(self, "vmate_manager"):
            self.vmate_manager.reload_from_config(self.config)
        self._maybe_rebind_chat_sessions_for_model()

