"""
환경 설정 다이얼로그 (탭: System, LLM, TTS, MCP)
"""
import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config.config_loader import save_config
from config.llm_defaults import default_llm_api_url_for_provider
from core.model_profile import repo_root
from mcp_extension import DEFAULT_MCP_SERVERS_CONFIG_FILE
from ui.hover_button import HoverAnimPushButton

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Catppuccin Mocha 기반 — 설정 전용, 메인 앱 톤과 맞춤
SETTINGS_STYLESHEET = """
QDialog {
    background-color: #11111b;
}
QLabel {
    color: #cdd6f4;
    font-size: 13px;
}
QLabel#settingsTitleLabel {
    color: #eceff4;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.4px;
}
QLabel#settingsSubtitleLabel {
    color: #7f849c;
    font-size: 13px;
    font-weight: 400;
}
QLabel#settingsHint {
    color: #9399b2;
    font-size: 12px;
    line-height: 1.5;
}
QLabel#settingsRuntimeLabel {
    color: #a6adc8;
    font-size: 12px;
    font-weight: 500;
}
QLabel#settingsSectionLabel {
    color: #bac2de;
    font-size: 12px;
    font-weight: 600;
    margin-top: 4px;
}
QFrame#settingsHero {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 12px;
    border-left: 4px solid #89b4fa;
}
QFrame#settingsFooter {
    background-color: transparent;
    border: none;
    border-top: 1px solid #313244;
    margin-top: 4px;
    padding-top: 4px;
}
QGroupBox {
    font-weight: 600;
    font-size: 13px;
    color: #b4befe;
    border: 1px solid #313244;
    border-radius: 10px;
    margin-top: 18px;
    padding: 16px 14px 14px 14px;
    background-color: #181825;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
}
QGroupBox QLabel {
    color: #bac2de;
    font-size: 13px;
    font-weight: 400;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 8px 12px;
    color: #cdd6f4;
    font-size: 13px;
    min-height: 22px;
    selection-background-color: #45475a;
    selection-color: #eceff4;
}
QPlainTextEdit {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
    padding: 10px 12px;
    color: #cdd6f4;
    font-size: 13px;
    selection-background-color: #45475a;
    selection-color: #eceff4;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #89b4fa;
    background-color: #181825;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #7f849c;
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: #45475a;
    selection-color: #eceff4;
    outline: 0;
}
QTabWidget::pane {
    border: 1px solid #313244;
    border-radius: 0 10px 10px 10px;
    background-color: #181825;
    top: -1px;
    padding: 2px;
}
QTabWidget QWidget#settingsTabRoot {
    background-color: transparent;
}
QTabBar::tab {
    background-color: transparent;
    color: #7f849c;
    padding: 11px 22px 10px 22px;
    margin-right: 2px;
    border: none;
    border-bottom: 3px solid transparent;
    min-width: 76px;
    font-size: 13px;
    font-weight: 500;
}
QTabBar::tab:selected {
    color: #cdd6f4;
    font-weight: 600;
    border-bottom: 3px solid #89b4fa;
    background-color: rgba(137, 180, 250, 0.07);
}
QTabBar::tab:hover:!selected {
    color: #bac2de;
    background-color: rgba(255, 255, 255, 0.04);
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
    color: #eceff4;
}
QPushButton:pressed {
    background-color: #1e1e2e;
    border-color: #89b4fa;
}
QPushButton#secondaryBtn {
    background-color: transparent;
    color: #bac2de;
    border: 1px solid #45475a;
}
QPushButton#secondaryBtn:hover {
    background-color: #1e1e2e;
    border-color: #585b70;
    color: #cdd6f4;
}
QPushButton#primaryBtn {
    background-color: #89b4fa;
    color: #11111b;
    font-weight: 600;
    border: 1px solid #89b4fa;
}
QPushButton#primaryBtn:hover {
    background-color: #b4befe;
    border-color: #b4befe;
    color: #11111b;
}
QPushButton#primaryBtn:pressed {
    background-color: #7287fd;
    border-color: #7287fd;
    color: #11111b;
}
QCheckBox {
    color: #cdd6f4;
    spacing: 10px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 20px;
    height: 20px;
    border-radius: 5px;
    border: 1px solid #45475a;
    background-color: #1e1e2e;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
QCheckBox::indicator:hover {
    border-color: #89b4fa;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: #181825;
    width: 10px;
    margin: 4px 2px 4px 0;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #45475a;
    min-height: 32px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    height: 0;
}
"""


def _wrap_scroll(inner: QWidget) -> QScrollArea:
    inner.setObjectName("settingsTabRoot")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(inner)
    return scroll


class SettingsDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main = main_window
        self.setWindowTitle("환경 설정")
        self.setMinimumSize(640, 560)
        self.resize(760, 640)
        self.setStyleSheet(SETTINGS_STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("settingsHero")
        hero_l = QVBoxLayout(hero)
        hero_l.setContentsMargins(22, 18, 22, 18)
        hero_l.setSpacing(6)
        title = QLabel("환경 설정")
        title.setObjectName("settingsTitleLabel")
        hero_l.addWidget(title)
        # sub = QLabel("DAON-VMate · Live2D · 대화 · 음성 · MCP 확장을 한 화면에서 구성합니다.")
        # sub.setObjectName("settingsSubtitleLabel")
        # sub.setWordWrap(True)
        # hero_l.addWidget(sub)
        root.addWidget(hero)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.addTab(self._build_system_tab(), "System")
        self.tabs.addTab(self._build_llm_tab(), "LLM")
        self.tabs.addTab(self._build_tts_tab(), "TTS")
        self.tabs.addTab(self._build_mcp_tab(), "MCP")
        root.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(self._on_settings_tab_changed)

        self.combo_llm_provider.currentTextChanged.connect(
            self._sync_llm_form_for_provider
        )
        self.combo_tts_provider.currentTextChanged.connect(
            self._sync_tts_form_for_provider
        )

        foot = QFrame()
        foot.setObjectName("settingsFooter")
        btn_row = QHBoxLayout(foot)
        btn_row.setContentsMargins(0, 18, 0, 4)
        btn_row.setSpacing(12)
        btn_row.addStretch()
        cancel = HoverAnimPushButton("취소")
        cancel.setObjectName("secondaryBtn")
        cancel.clicked.connect(self.reject)
        save = HoverAnimPushButton("저장 및 적용")
        save.setObjectName("primaryBtn")
        save.clicked.connect(self._save_settings)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        root.addWidget(foot)

        self._load_values()
        self._sync_llm_form_for_provider()
        self._sync_tts_form_for_provider()

    def _cfg(self):
        return self.main.config

    def _build_system_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 10, 10, 10)
        layout.setSpacing(14)

        g_live = QGroupBox("Live2D")
        fl = QFormLayout(g_live)
        fl.setSpacing(10)
        self.combo_folder = QComboBox()
        row = QHBoxLayout()
        row.addWidget(self.combo_folder, 1)
        btn_import = HoverAnimPushButton("폴더 불러오기")
        btn_import.clicked.connect(self._import_model_folder)
        row.addWidget(btn_import)
        w = QWidget()
        w.setLayout(row)
        fl.addRow("모델 폴더", w)

        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(0.05, 3.0)
        self.spin_scale.setSingleStep(0.05)
        self.spin_scale.setDecimals(2)
        fl.addRow("표시 크기 (Scale)", self.spin_scale)
        self.chk_auto_emotion = QCheckBox(
            "응답에 따른 표정 반영 (Auto Emotion)"
        )
        fl.addRow(self.chk_auto_emotion)
        btn_char_prompt = HoverAnimPushButton("이 모델 전용 캐릭터 프롬프트…")
        btn_char_prompt.setToolTip(
            "모델 폴더에 daon_(폴더이름)_settings.json 으로 저장되며, "
            "채팅 시 LLM 시스템 프롬프트에 합쳐집니다."
        )
        btn_char_prompt.clicked.connect(self._open_live2d_character_prompt_dialog)
        fl.addRow(btn_char_prompt)
        self.chk_mouse_tracking = QCheckBox("마우스 시선 추적 (Mouse Tracking)")
        fl.addRow(self.chk_mouse_tracking)
        layout.addWidget(g_live)

        g_win = QGroupBox("창 (UI)")
        fw = QFormLayout(g_win)
        self.spin_w = QSpinBox()
        self.spin_w.setRange(480, 3840)
        self.spin_h = QSpinBox()
        self.spin_h.setRange(360, 2160)
        fw.addRow("너비 (px)", self.spin_w)
        fw.addRow("높이 (px)", self.spin_h)
        self.chk_top = QCheckBox("항상 위에 표시")
        self.spin_typing_ms = QSpinBox()
        self.spin_typing_ms.setRange(4, 200)
        self.spin_typing_ms.setSuffix(" ms")
        self.spin_typing_chars = QSpinBox()
        self.spin_typing_chars.setRange(1, 4)
        self.spin_pet_w = QSpinBox()
        self.spin_pet_w.setRange(200, 1200)
        self.spin_pet_h = QSpinBox()
        self.spin_pet_h.setRange(200, 1600)
        fw.addRow(self.chk_top)
        self.chk_dark_mode = QCheckBox("다크 모드 (채팅·사이드바·상단 바)")
        fw.addRow(self.chk_dark_mode)
        fw.addRow("타이핑 간격", self.spin_typing_ms)
        fw.addRow("타이핑 글자/틱", self.spin_typing_chars)
        fw.addRow("캐릭터 모드 창 너비", self.spin_pet_w)
        fw.addRow("캐릭터 모드 창 높이", self.spin_pet_h)
        pet_hint = QLabel(
            """메인 화면은 채팅 중심으로 시작합니다.
            \n드래그 (시점 조작): 캐릭터 모델 위에서 마우스 왼쪽 버튼을 누른 채 움직이면, 캐릭터를 회전시키거나 바라보는 각도를 변경할 수 있습니다. 캐릭터의 다양한 면을 관찰할 때 사용합니다. 
            캐릭터 모드 : 캐릭터 모드는 캐릭터 모델만 표시되는 모드입니다. 캐릭터 모드에서는 캐릭터 모델을 회전시키거나 바라보는 각도를 변경할 수 있습니다. 캐릭터의 다양한 면을 관찰할 때 사용합니다.
            \n드래그 (시점 조작): 캐릭터 모델 위에서 마우스 왼쪽 버튼을 누른 채 움직이면, 캐릭터를 회전시키거나 바라보는 각도를 변경할 수 있습니다. 캐릭터의 다양한 면을 관찰할 때 사용합니다.
            \nShift + 드래그 (창/위치 이동): Shift 키를 누른 상태에서 캐릭터를 드래그하면, 캐릭터 모델이 배치된 투명한 창 자체를 화면의 원하는 위치(예: 모니터 구석이나 중앙 등)로 옮길 수 있습니다.
            \n마우스 휠 (크기 조절): 휠을 위아래로 굴려 캐릭터의 크기를 실시간으로 키우거나 줄일 수 있습니다(Zoom In/Out). 작업 공간 확보나 몰입감 조절에 용이합니다.
            \n왼쪽 클릭시 컨텍스트 메뉴 (채팅모드 전환 포함)"""
        )
        pet_hint.setWordWrap(True)
        pet_hint.setObjectName("settingsHint")
        layout.addWidget(g_win)
        layout.addWidget(pet_hint)
        layout.addStretch()
        return _wrap_scroll(page)

    def _build_llm_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 10, 10, 10)
        layout.setSpacing(14)
        g = QGroupBox("대화 모델 (LLM)")
        f = QFormLayout(g)
        f.setSpacing(10)
        self._form_llm = f

        self.combo_llm_provider = QComboBox()
        self.combo_llm_provider.addItems(
            ["ollama", "openai_compatible", "lm_studio", "custom"]
        )
        self.edit_llm_model = QLineEdit()
        self.edit_llm_url = QLineEdit()
        self.edit_llm_url.setPlaceholderText(
            "custom 전용: OpenAI 호환 베이스 URL (예: http://127.0.0.1:8080/v1)"
        )
        self.edit_llm_api_key = QLineEdit()
        self.edit_llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_llm_api_key.setPlaceholderText(
            "OpenAI / 호환 API용 (LM Studio 로컬은 보통 비움)"
        )

        self.spin_llm_temp = QDoubleSpinBox()
        self.spin_llm_temp.setRange(0.0, 2.0)
        self.spin_llm_temp.setSingleStep(0.1)
        self.spin_llm_temp.setDecimals(2)
        self.spin_llm_tokens = QSpinBox()
        self.spin_llm_tokens.setRange(256, 128000)
        self.spin_llm_tokens.setSingleStep(256)
        self.spin_llm_timeout = QSpinBox()
        self.spin_llm_timeout.setRange(30, 600)
        self.spin_llm_timeout.setSuffix(" 초")
        self.chk_llm_stream = QCheckBox(
            "스트리밍 응답 (배치 단위 표시 + TTS 파이프라인)"
        )
        self.chk_llm_emotion_tags = QCheckBox(
            "감정 태그 안내 ([joy] 등) — 시스템 프롬프트에 Live2D용 태그 목록 추가"
        )
        self.spin_llm_stream_min = QSpinBox()
        self.spin_llm_stream_min.setRange(2, 200)
        self.spin_llm_stream_max = QSpinBox()
        self.spin_llm_stream_max.setRange(8, 500)

        f.addRow("제공자", self.combo_llm_provider)
        f.addRow("모델 이름", self.edit_llm_model)
        f.addRow("API URL (custom 전용)", self.edit_llm_url)
        f.addRow("API 키", self.edit_llm_api_key)
        f.addRow("Temperature", self.spin_llm_temp)
        f.addRow("최대 토큰", self.spin_llm_tokens)
        f.addRow("요청 타임아웃", self.spin_llm_timeout)
        f.addRow(self.chk_llm_stream)
        f.addRow(self.chk_llm_emotion_tags)
        f.addRow("배치 최소 글자수", self.spin_llm_stream_min)
        f.addRow("배치 최대 글자수", self.spin_llm_stream_max)
        layout.addWidget(g)

        g_sys = QGroupBox("시스템 프롬프트")
        g_sys_layout = QVBoxLayout(g_sys)
        self.text_llm_system = QPlainTextEdit()
        self.text_llm_system.setPlaceholderText(
            "역할·말투·금지 사항 등. Live2D 감정 태그([joy] 등)는 아래 체크 시 자동으로 태그 목록이 덧붙습니다.\n"
            "태그는 채팅·TTS에서는 제거되고 표정만 바뀝니다. Ollama·OpenAI 호환 모두 system 메시지로 전달됩니다."
        )
        self.text_llm_system.setMinimumHeight(160)
        g_sys_layout.addWidget(self.text_llm_system)
        layout.addWidget(g_sys)

        self.lbl_llm_hint = QLabel()
        self.lbl_llm_hint.setWordWrap(True)
        self.lbl_llm_hint.setObjectName("settingsHint")
        layout.addWidget(self.lbl_llm_hint)
        layout.addStretch()
        return _wrap_scroll(page)

    def _sync_llm_form_for_provider(self) -> None:
        if not hasattr(self, "_form_llm"):
            return
        p = self.combo_llm_provider.currentText()
        openai_style = p in ("openai_compatible", "lm_studio", "custom")
        self._form_llm.setRowVisible(self.edit_llm_api_key, openai_style)

        custom = p == "custom"
        self._form_llm.setRowVisible(self.edit_llm_url, custom)
        if custom and not self.edit_llm_url.text().strip():
            self.edit_llm_url.setText(
                str(self.main.config.get("llm", {}).get("api_url", ""))
            )

        hints = {
            "ollama": (
                "Ollama 로컬: 엔드포인트는 http://127.0.0.1:11434 로 고정됩니다. "
                "모델 이름만 Ollama에 설치된 이름과 맞추면 됩니다."
            ),
            "openai_compatible": (
                "OpenAI 공식 API: 베이스 URL은 https://api.openai.com/v1 로 고정됩니다. "
                "모델 이름과 API 키만 입력하세요."
            ),
            "lm_studio": (
                "LM Studio 로컬 서버: 베이스 URL은 http://127.0.0.1:1234/v1 로 고정됩니다. "
                "모델 이름은 LM Studio에서 로드한 모델 ID와 맞추고, API 키는 보통 비웁니다."
            ),
            "custom": (
                "자체/기타 OpenAI 호환 서버: 아래 API URL에 베이스(…/v1)를 직접 입력합니다. "
                "모델 이름·API 키(필요 시)를 맞춥니다."
            ),
        }
        self.lbl_llm_hint.setText(hints.get(p, hints["custom"]))

    def _build_tts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 10, 10, 10)
        layout.setSpacing(14)
        g = QGroupBox("음성 합성 (TTS)")
        f = QFormLayout(g)
        f.setSpacing(10)
        self._form_tts = f

        self.combo_tts_provider = QComboBox()
        self.combo_tts_provider.addItems(["gpt-sovits", "edge-tts", "openai_tts", "custom"])

        self.edit_tts_url = QLineEdit()
        self.edit_tts_url.setPlaceholderText(
            "GPT-SoVITS: .../tts | OpenAI TTS: https://api.openai.com | 커스텀: 오디오 URL"
        )
        self.edit_tts_char = QLineEdit()
        self.edit_tts_char.setPlaceholderText("GPT-SoVITS 메모용 (선택)")

        self.edit_tts_api_key = QLineEdit()
        self.edit_tts_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_tts_api_key.setPlaceholderText("OpenAI TTS 전용")

        self.edit_tts_edge_voice = QLineEdit()
        self.edit_tts_edge_voice.setPlaceholderText("예: ko-KR-SunHiNeural")

        self.edit_tts_openai_model = QLineEdit()
        self.edit_tts_openai_model.setPlaceholderText("tts-1 또는 tts-1-hd")
        self.edit_tts_openai_voice = QLineEdit()
        self.edit_tts_openai_voice.setPlaceholderText("alloy, echo, fable, onyx, nova, shimmer")

        self.spin_tts_timeout = QSpinBox()
        self.spin_tts_timeout.setRange(10, 600)
        self.spin_tts_timeout.setSuffix(" 초")

        self.edit_tts_text_lang = QLineEdit()
        self.edit_tts_ref_audio = QLineEdit()
        self.edit_tts_prompt_lang = QLineEdit()
        self.edit_tts_prompt_text = QLineEdit()
        self.edit_tts_split = QLineEdit()
        self.edit_tts_batch = QLineEdit()
        self.edit_tts_media = QLineEdit()
        self.edit_tts_streaming = QLineEdit()

        self._tts_gpt_only_widgets = [
            self.edit_tts_text_lang,
            self.edit_tts_ref_audio,
            self.edit_tts_prompt_lang,
            self.edit_tts_prompt_text,
            self.edit_tts_split,
            self.edit_tts_batch,
            self.edit_tts_media,
            self.edit_tts_streaming,
        ]

        f.addRow("제공자", self.combo_tts_provider)
        f.addRow("API URL", self.edit_tts_url)
        f.addRow("API 키 (OpenAI TTS)", self.edit_tts_api_key)
        f.addRow("Edge 음성 (voice)", self.edit_tts_edge_voice)
        f.addRow("OpenAI TTS 모델", self.edit_tts_openai_model)
        f.addRow("OpenAI TTS 보이스", self.edit_tts_openai_voice)
        f.addRow("캐릭터 / 메모", self.edit_tts_char)
        f.addRow("요청 타임아웃", self.spin_tts_timeout)
        f.addRow("텍스트 언어 (text_lang)", self.edit_tts_text_lang)
        f.addRow("참조 음성 경로 (ref_audio_path)", self.edit_tts_ref_audio)
        f.addRow("프롬프트 언어 (prompt_lang)", self.edit_tts_prompt_lang)
        f.addRow("프롬프트 텍스트 (prompt_text)", self.edit_tts_prompt_text)
        f.addRow("분할 방식 (text_split_method)", self.edit_tts_split)
        f.addRow("배치 크기 (batch_size)", self.edit_tts_batch)
        f.addRow("미디어 타입 (media_type)", self.edit_tts_media)
        f.addRow("스트리밍 (streaming_mode)", self.edit_tts_streaming)
        layout.addWidget(g)

        self.lbl_tts_hint = QLabel()
        self.lbl_tts_hint.setWordWrap(True)
        self.lbl_tts_hint.setObjectName("settingsHint")
        layout.addWidget(self.lbl_tts_hint)
        layout.addStretch()
        return _wrap_scroll(page)

    def _sync_tts_form_for_provider(self) -> None:
        if not hasattr(self, "_form_tts"):
            return
        p = self.combo_tts_provider.currentText()
        gpt = p == "gpt-sovits"
        edge = p == "edge-tts"
        oai = p == "openai_tts"
        cust = p == "custom"

        self._form_tts.setRowVisible(self.edit_tts_url, gpt or oai or cust)
        self._form_tts.setRowVisible(self.edit_tts_api_key, oai)
        self._form_tts.setRowVisible(self.edit_tts_edge_voice, edge)
        self._form_tts.setRowVisible(self.edit_tts_openai_model, oai)
        self._form_tts.setRowVisible(self.edit_tts_openai_voice, oai)
        self._form_tts.setRowVisible(self.edit_tts_char, gpt)
        self._form_tts.setRowVisible(self.spin_tts_timeout, True)
        for w in self._tts_gpt_only_widgets:
            self._form_tts.setRowVisible(w, gpt)

        hints = {
            "gpt-sovits": (
                "GPT-SoVITS: GET .../tts?text=... 또는 POST JSON. "
                "WAV(media_type: wav) 권장. ref_audio_path 등은 API 서버 규격에 맞게 입력."
            ),
            "edge-tts": (
                "Microsoft Edge TTS — edge_tts·miniaudio 모듈 필요 "
                "(앱을 실행하는 동일 Python에서: pip install edge-tts miniaudio). "
                "출력은 MP3. 음성 예: ko-KR-SunHiNeural. API URL은 사용하지 않습니다."
            ),
            "openai_tts": (
                "OpenAI Speech API: POST /v1/audio/speech. "
                "베이스 URL만 넣으면 /v1/audio/speech 가 붙습니다. API 키 필수."
            ),
            "custom": (
                "먼저 GET ?text= 로 오디오를 요청하고, 실패 시 POST JSON {\"text\":\"...\"} 를 시도합니다. "
                "응답은 바이너리 오디오여야 합니다."
            ),
        }
        self.lbl_tts_hint.setText(hints.get(p, hints["custom"]))

    def _build_mcp_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 10, 10, 10)
        layout.setSpacing(14)
        g = QGroupBox("Model Context Protocol")
        f = QFormLayout(g)
        self.chk_mcp_enabled = QCheckBox(
            "MCP stdio 클라이언트 사용 mcp_extension/servers 에 있는 서버를 사용합니다."
        )
        f.addRow(self.chk_mcp_enabled)
        self.lbl_mcp_runtime = QLabel("")
        self.lbl_mcp_runtime.setWordWrap(True)
        self.lbl_mcp_runtime.setObjectName("settingsRuntimeLabel")
        f.addRow("상태", self.lbl_mcp_runtime)
        self.chk_llm_use_mcp_tools = QCheckBox(
            "채팅에서 MCP 도구 사용"
        )
        f.addRow(self.chk_llm_use_mcp_tools)
        self.spin_mcp_max_rounds = QSpinBox()
        self.spin_mcp_max_rounds.setRange(1, 32)
        self.spin_mcp_max_rounds.setSuffix(" 회")
        f.addRow("MCP 도구 최대 라운드", self.spin_mcp_max_rounds)
        row = QHBoxLayout()
        self.edit_mcp_file = QLineEdit()
        self.edit_mcp_file.setPlaceholderText(DEFAULT_MCP_SERVERS_CONFIG_FILE)
        btn_browse = HoverAnimPushButton("찾아보기")
        btn_browse.clicked.connect(self._browse_mcp_file)
        row.addWidget(self.edit_mcp_file, 1)
        row.addWidget(btn_browse)
        w = QWidget()
        w.setLayout(row)
        f.addRow("설정 파일", w)
        layout.addWidget(g)
        layout.addStretch()
        return _wrap_scroll(page)

    def _mcp_path(self) -> str:
        rel = self._cfg().get("mcp", {}).get("config_file", DEFAULT_MCP_SERVERS_CONFIG_FILE)
        if os.path.isabs(rel):
            return rel
        return os.path.join(ROOT_DIR, rel)

    def _load_values(self):
        c = self._cfg()
        live = c.get("live2d", {})
        ui = c.get("ui", {})
        llm = c.get("llm", {})
        tts = c.get("tts", {})
        mcp = c.get("mcp", {})

        self._refresh_model_combo()
        self.spin_scale.setValue(float(live.get("scale", 0.25)))
        self.chk_auto_emotion.setChecked(
            bool(live.get("auto_emotion_from_assistant", True))
        )
        self.chk_mouse_tracking.setChecked(bool(ui.get("mouse_tracking", True)))
        self.spin_w.setValue(int(ui.get("window_width", 1280)))
        self.spin_h.setValue(int(ui.get("window_height", 720)))
        self.chk_top.setChecked(bool(ui.get("always_on_top", False)))
        self.chk_dark_mode.setChecked(bool(ui.get("dark_mode", True)))
        self.spin_typing_ms.setValue(
            max(4, min(200, int(ui.get("typing_interval_ms", 26))))
        )
        self.spin_typing_chars.setValue(
            max(1, min(4, int(ui.get("typing_chars_per_tick", 1))))
        )
        self.spin_pet_w.setValue(int(ui.get("pet_window_width", 520)))
        self.spin_pet_h.setValue(int(ui.get("pet_window_height", 720)))

        prov = llm.get("provider", "ollama")
        i = self.combo_llm_provider.findText(prov)
        if i >= 0:
            self.combo_llm_provider.setCurrentIndex(i)
        self.edit_llm_model.setText(str(llm.get("model", "llama3")))
        if prov == "custom":
            self.edit_llm_url.setText(str(llm.get("api_url", "")))
        else:
            self.edit_llm_url.clear()
        self.edit_llm_api_key.setText(str(llm.get("api_key", "")))
        self.spin_llm_temp.setValue(float(llm.get("temperature", 0.7)))
        self.spin_llm_tokens.setValue(int(llm.get("max_tokens", 2048)))
        self.spin_llm_timeout.setValue(int(llm.get("request_timeout_sec", 120)))
        self.chk_llm_stream.setChecked(bool(llm.get("stream_enabled", True)))
        self.chk_llm_emotion_tags.setChecked(bool(llm.get("use_emotion_tags", True)))
        self.spin_llm_stream_min.setValue(int(llm.get("stream_batch_min_chars", 8)))
        self.spin_llm_stream_max.setValue(int(llm.get("stream_batch_max_chars", 56)))
        self.text_llm_system.setPlainText(str(llm.get("system_prompt", "")))
        self.chk_llm_use_mcp_tools.setChecked(bool(llm.get("use_mcp_tools", False)))
        self.spin_mcp_max_rounds.setValue(int(llm.get("mcp_max_rounds", 8)))

        tp = tts.get("provider", "gpt-sovits")
        i = self.combo_tts_provider.findText(tp)
        if i >= 0:
            self.combo_tts_provider.setCurrentIndex(i)
        self.edit_tts_url.setText(str(tts.get("api_url", "http://127.0.0.1:9880/tts")))
        self.edit_tts_api_key.setText(str(tts.get("api_key", "")))
        self.edit_tts_edge_voice.setText(
            str(tts.get("edge_voice", "ko-KR-SunHiNeural"))
        )
        self.edit_tts_openai_model.setText(
            str(tts.get("openai_tts_model", "tts-1"))
        )
        self.edit_tts_openai_voice.setText(
            str(tts.get("openai_tts_voice", "nova"))
        )
        self.edit_tts_char.setText(str(tts.get("character_name", "daon")))
        self.spin_tts_timeout.setValue(int(tts.get("timeout_sec", 120)))
        self.edit_tts_text_lang.setText(str(tts.get("text_lang", "ko")))
        self.edit_tts_ref_audio.setText(str(tts.get("ref_audio_path", "")))
        self.edit_tts_prompt_lang.setText(str(tts.get("prompt_lang", "ko")))
        self.edit_tts_prompt_text.setText(str(tts.get("prompt_text", "")))
        self.edit_tts_split.setText(str(tts.get("text_split_method", "cut5")))
        self.edit_tts_batch.setText(str(tts.get("batch_size", "1")))
        self.edit_tts_media.setText(str(tts.get("media_type", "wav")))
        self.edit_tts_streaming.setText(str(tts.get("streaming_mode", "false")))

        self.chk_mcp_enabled.setChecked(bool(mcp.get("enabled", False)))
        self.edit_mcp_file.setText(str(mcp.get("config_file", DEFAULT_MCP_SERVERS_CONFIG_FILE)))
        self._refresh_mcp_status_label()

    def _refresh_mcp_status_label(self) -> None:
        if not hasattr(self, "lbl_mcp_runtime"):
            return
        m = getattr(self.main, "mcp_client", None)
        self.lbl_mcp_runtime.setText(m.status_summary() if m else "MCP 클라이언트 없음")

    def _on_settings_tab_changed(self, index: int) -> None:
        if self.tabs.tabText(index) == "MCP":
            self._refresh_mcp_status_label()

    def _refresh_model_combo(self):
        self.combo_folder.clear()
        for m in self.main.get_available_models():
            self.combo_folder.addItem(m["folder_name"])
        name = self._cfg().get("live2d", {}).get("model_folder", "shizuku")
        idx = self.combo_folder.findText(name)
        if idx >= 0:
            self.combo_folder.setCurrentIndex(idx)

    def _open_live2d_character_prompt_dialog(self) -> None:
        folder = self.combo_folder.currentText().strip()
        if not folder:
            QMessageBox.warning(self, "Live2D", "모델 폴더를 먼저 선택하세요.")
            return
        base = os.path.join(repo_root(), "assets", "live2d-models", folder)
        if not os.path.isdir(base):
            QMessageBox.warning(
                self,
                "Live2D",
                f"모델 폴더가 없습니다.\n{base}",
            )
            return
        from ui.live2d_character_prompt_dialog import Live2DCharacterPromptDialog

        dlg = Live2DCharacterPromptDialog(repo_root(), folder, self)
        dlg.exec()

    def _import_model_folder(self):
        source = QFileDialog.getExistingDirectory(self, "Live2D 모델 폴더 선택")
        if not source:
            return
        folder_name = os.path.basename(source)
        dest = os.path.join(ROOT_DIR, "assets", "live2d-models", folder_name)
        try:
            if not os.path.exists(dest):
                shutil.copytree(source, dest)
            QMessageBox.information(self, "완료", f"모델 '{folder_name}' 을(를) 추가했습니다.")
            self._refresh_model_combo()
            j = self.combo_folder.findText(folder_name)
            if j >= 0:
                self.combo_folder.setCurrentIndex(j)
        except OSError as e:
            QMessageBox.critical(self, "오류", str(e))

    def _browse_mcp_file(self):
        start = os.path.join(ROOT_DIR, self.edit_mcp_file.text() or DEFAULT_MCP_SERVERS_CONFIG_FILE)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "MCP 설정 파일",
            start if os.path.isfile(start) else ROOT_DIR,
            "JSON (*.json)",
        )
        if path:
            try:
                rel = os.path.relpath(path, ROOT_DIR)
            except ValueError:
                rel = path
            self.edit_mcp_file.setText(rel)

    def _save_settings(self):
        c = self.main.config
        c.setdefault("live2d", {})
        c.setdefault("ui", {})
        c.setdefault("llm", {})
        c.setdefault("tts", {})
        c.setdefault("mcp", {})

        c["live2d"]["model_folder"] = self.combo_folder.currentText()
        c["live2d"]["scale"] = float(self.spin_scale.value())
        c["live2d"]["auto_emotion_from_assistant"] = self.chk_auto_emotion.isChecked()
        c["ui"]["mouse_tracking"] = self.chk_mouse_tracking.isChecked()
        c["ui"].pop("chat_assistant_name", None)
        c["ui"]["window_width"] = int(self.spin_w.value())
        c["ui"]["window_height"] = int(self.spin_h.value())
        c["ui"].pop("transparent_background", None)
        c["ui"]["always_on_top"] = self.chk_top.isChecked()
        c["ui"]["dark_mode"] = self.chk_dark_mode.isChecked()
        c["ui"]["typing_interval_ms"] = max(
            4, min(200, int(self.spin_typing_ms.value()))
        )
        c["ui"]["typing_chars_per_tick"] = max(
            1, min(4, int(self.spin_typing_chars.value()))
        )
        c["ui"].pop("desktop_pet_mode", None)
        c["ui"]["pet_window_width"] = int(self.spin_pet_w.value())
        c["ui"]["pet_window_height"] = int(self.spin_pet_h.value())

        llm_prov = self.combo_llm_provider.currentText()
        llm_model = self.edit_llm_model.text().strip()
        if not llm_model:
            QMessageBox.warning(self, "LLM", "모델 이름을 입력하세요.")
            return
        if llm_prov == "custom":
            llm_url = self.edit_llm_url.text().strip()
            if not llm_url:
                QMessageBox.warning(
                    self,
                    "LLM",
                    "custom 제공자는 API URL이 필요합니다.",
                )
                return
        else:
            llm_url = default_llm_api_url_for_provider(llm_prov)

        c["llm"]["provider"] = llm_prov
        c["llm"]["model"] = llm_model
        c["llm"]["api_url"] = llm_url
        c["llm"]["api_key"] = self.edit_llm_api_key.text().strip()
        c["llm"]["temperature"] = float(self.spin_llm_temp.value())
        c["llm"]["max_tokens"] = int(self.spin_llm_tokens.value())
        c["llm"]["request_timeout_sec"] = int(self.spin_llm_timeout.value())
        c["llm"]["stream_enabled"] = self.chk_llm_stream.isChecked()
        c["llm"]["use_emotion_tags"] = self.chk_llm_emotion_tags.isChecked()
        mn = int(self.spin_llm_stream_min.value())
        mx = int(self.spin_llm_stream_max.value())
        if mx < mn:
            mx = mn
        c["llm"]["stream_batch_min_chars"] = mn
        c["llm"]["stream_batch_max_chars"] = mx
        c["llm"]["system_prompt"] = self.text_llm_system.toPlainText()
        c["llm"]["use_mcp_tools"] = self.chk_llm_use_mcp_tools.isChecked()
        c["llm"]["mcp_max_rounds"] = int(self.spin_mcp_max_rounds.value())

        c["tts"]["provider"] = self.combo_tts_provider.currentText()
        c["tts"]["api_url"] = self.edit_tts_url.text().strip()
        c["tts"]["api_key"] = self.edit_tts_api_key.text().strip()
        c["tts"]["edge_voice"] = self.edit_tts_edge_voice.text().strip() or "ko-KR-SunHiNeural"
        c["tts"]["openai_tts_model"] = (
            self.edit_tts_openai_model.text().strip() or "tts-1"
        )
        c["tts"]["openai_tts_voice"] = (
            self.edit_tts_openai_voice.text().strip() or "nova"
        )
        c["tts"]["character_name"] = self.edit_tts_char.text().strip()
        c["tts"]["timeout_sec"] = int(self.spin_tts_timeout.value())
        c["tts"]["text_lang"] = self.edit_tts_text_lang.text().strip() or "ko"
        c["tts"]["ref_audio_path"] = self.edit_tts_ref_audio.text().strip()
        c["tts"]["prompt_lang"] = self.edit_tts_prompt_lang.text().strip() or "ko"
        c["tts"]["prompt_text"] = self.edit_tts_prompt_text.text().strip()
        c["tts"]["text_split_method"] = self.edit_tts_split.text().strip() or "cut5"
        c["tts"]["batch_size"] = self.edit_tts_batch.text().strip() or "1"
        c["tts"]["media_type"] = self.edit_tts_media.text().strip() or "wav"
        c["tts"]["streaming_mode"] = self.edit_tts_streaming.text().strip() or "false"

        c["mcp"]["enabled"] = self.chk_mcp_enabled.isChecked()
        c["mcp"]["config_file"] = (
            self.edit_mcp_file.text().strip() or DEFAULT_MCP_SERVERS_CONFIG_FILE
        )

        if not save_config(c):
            QMessageBox.critical(self, "오류", "settings.yaml 저장에 실패했습니다.")
            return

        self.main.apply_ui_from_config()
        self._refresh_mcp_status_label()
        self.main.reload_live2d()
        if hasattr(self.main, "vtuber_manager"):
            self.main.vtuber_manager.reload_from_config(c)
        self.accept()
