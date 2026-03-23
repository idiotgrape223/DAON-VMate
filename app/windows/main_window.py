from __future__ import annotations

import glob
import os
import sys
from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import (
    QColor,
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
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.styles import LIVE2D_TOP_BAR_HEIGHT_PX, LIVE2D_TOP_BAR_QSS
from app.widgets.chat_widget import ChatWidget
from app.widgets.live2d_widget import Live2DWidget
from config.config_loader import load_config
from core.llm_attachments import LLMMediaAttachment
from core.mcp_client import MCPClientService
from core.model_profile import repo_root
from core.tts_engine import TTSEngine
from core.vtuber_manager import VTuberManager
from ui.hover_button import HoverAnimPushButton
from ui.screen_share import (
    WindowPickerDialog,
    grab_full_virtual_desktop,
    grab_monitor,
    grab_native_window,
    list_visible_windows_win32,
    pixmap_to_llm_attachment,
)
from ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.mcp_client = MCPClientService(repo_root())
        self.mcp_client.apply_config(self.config)
        self.vtuber_manager = VTuberManager(self.config)
        self.vtuber_manager.set_mcp_client(self.mcp_client)
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
        main_layout = QVBoxLayout(self._central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self._screen_share_active = False
        self._screen_share_mode = ""
        self._screen_share_screen = None
        self._screen_share_hwnd: int | None = None
        self._screen_share_attachment: LLMMediaAttachment | None = None
        self._screen_share_timer = QTimer(self)
        self._screen_share_timer.setInterval(800)
        self._screen_share_timer.timeout.connect(self._tick_screen_share_capture)

        self.live2d_view = Live2DWidget(self)
        self.chat_widget = ChatWidget(self)

        main_layout.addWidget(self.live2d_view)

        self._top_bar = QWidget(self.live2d_view)
        self._top_bar.setObjectName("live2dTopBar")
        self._top_bar.setStyleSheet(LIVE2D_TOP_BAR_QSS)
        bar_layout = QHBoxLayout(self._top_bar)
        bar_layout.setContentsMargins(16, 8, 16, 8)
        bar_layout.setSpacing(10)

        _shadow = QColor(137, 180, 250, 90)
        self.btn_screen_share = HoverAnimPushButton(
            "화면 공유",
            shadow_color=_shadow,
            hover_blur=22,
        )
        self.btn_screen_share.setToolTip(
            "전체 화면·모니터·창(Windows)을 주기적으로 캡처합니다. "
            "메시지를 보낼 때마다 가장 최근 화면이 비전 LLM 첨부로 포함됩니다."
        )
        self._screen_share_menu = QMenu(self)
        self._screen_share_menu.aboutToShow.connect(self._populate_screen_share_menu)
        self.btn_screen_share.setMenu(self._screen_share_menu)

        self.btn_desktop_pet = HoverAnimPushButton(
            "캐릭터 모드",
            shadow_color=_shadow,
            hover_blur=22,
        )
        self.btn_desktop_pet.setToolTip(
            "데스크톱 캐릭터 모드: 무테 창과 Live2D 알파(배경 투명) 합성. "
            "왼쪽 클릭: 채팅 입력 줄 표시/같은 방식으로 한 번 더 누르면 닫힘(캐릭터 영역). "
            "채팅 바의 빈 여백을 눌러도 닫힘. 드래그: 시점, Shift+드래그: 창 이동, 휠: 크기. "
            "우클릭: 채팅 모드로 복귀. Esc: 입력창만 닫기."
        )
        self.btn_desktop_pet.clicked.connect(self.enter_desktop_pet_mode)

        self.btn_settings = HoverAnimPushButton(
            "설정",
            shadow_color=_shadow,
            hover_blur=22,
        )
        self.btn_settings.clicked.connect(self.open_settings)

        bar_layout.addWidget(self.btn_screen_share)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self.btn_desktop_pet)
        bar_layout.addWidget(self.btn_settings)

        _tb_font = QFont()
        _tb_font.setPointSize(10)
        _tb_font.setWeight(QFont.Weight.DemiBold)
        for _b in (self.btn_screen_share, self.btn_desktop_pet, self.btn_settings):
            _b.setFont(_tb_font)

        self.chat_widget.setParent(self.live2d_view)
        self.chat_widget.resize(600, 200)
        self._layout_overlay_widgets()

        self._apply_normal_window_chrome_opaque()
        self._apply_live2d_alpha_overlay(False)

        QTimer.singleShot(0, self.reload_live2d)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
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
        w, h = self.width(), self.height()
        lv_w = max(1, self.live2d_view.width())
        tb_h = LIVE2D_TOP_BAR_HEIGHT_PX
        self._top_bar.setFixedHeight(tb_h)
        self._top_bar.setGeometry(0, 0, lv_w, tb_h)
        self._top_bar.raise_()
        if getattr(self, "_desktop_pet_mode", False):
            if not getattr(self, "_pet_floating_chat_placed", False):
                self.chat_widget.hide()
            return
        self.chat_widget.resize(600, 200)
        self.chat_widget.move(max(0, (w - 600) // 2), max(0, h - 220))

    def show_pet_floating_chat_at(self, lx: int, ly: int) -> None:
        """캐릭터 모드 모드: 클릭 지점 근처에 입력창만(히스토리 숨김) 표시."""
        if not getattr(self, "_desktop_pet_mode", False):
            return
        lv_w = max(1, self.live2d_view.width())
        lv_h = max(1, self.live2d_view.height())
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

    def _apply_normal_window_chrome_opaque(self) -> None:
        """채팅 모드: 화면상 불투명이지만 창·중앙 위젯은 처음부터 알파 합성 경로로 둡니다."""
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

    def _apply_pet_transparent_chrome(self) -> None:
        """캐릭터 모드: 데스크톱이 비치도록 크롬 배경을 완전 투명으로."""
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
        if hasattr(self, "mcp_client"):
            self.mcp_client.stop()
        super().closeEvent(event)

    def apply_ui_from_config(self):
        if hasattr(self, "mcp_client"):
            self.mcp_client.apply_config(self.config)
        if hasattr(self, "vtuber_manager"):
            self.vtuber_manager.set_mcp_client(self.mcp_client)
        if hasattr(self, "chat_widget"):
            self.chat_widget.apply_assistant_display_settings()
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
            print(
                f"[Live2D] 설정 폴더 '{folder_name}' 없음, "
                f"'{target_model['folder_name']}' 로 대체합니다."
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
            print(
                "[Live2D] 사용 가능한 모델이 없습니다. "
                "설정에서 폴더를 불러오거나 assets/live2d-models 에 모델을 넣으세요."
            )
        if hasattr(self, "vtuber_manager"):
            self.vtuber_manager.reload_from_config(self.config)

