"""Live2D 모델 폴더별 캐릭터 프롬프트 편집 (daon_*_settings.json)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from core.live2d_character_settings import (
    CHARACTER_NAME_KEY,
    SECTION_KEYS,
    character_settings_path,
    load_character_settings,
    save_character_settings,
)
from ui.hover_button import HoverAnimPushButton


class Live2DCharacterPromptDialog(QDialog):
    def __init__(self, repo_root: str, folder_name: str, parent=None):
        super().__init__(parent)
        self._repo_root = repo_root
        self._folder_name = (folder_name or "").strip()
        self.setWindowTitle(f"캐릭터 프롬프트 — {self._folder_name}")
        self.setMinimumSize(560, 520)
        self.resize(640, 560)
        p = parent
        if p is not None and getattr(p, "styleSheet", None):
            ss = p.styleSheet()
            if isinstance(ss, str) and ss.strip():
                self.setStyleSheet(ss)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        hint = QLabel(
            "이름은 채팅 말풍선에 표시되며 LLM에도 전달됩니다. "
            "성격·말투 등은 시스템 프롬프트에 합쳐지고, 전역 프롬프트(LLM 탭)는 공통 역할로 두면 됩니다."
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        path = character_settings_path(repo_root, self._folder_name)
        path_lbl = QLabel(f"저장 파일: {path}")
        path_lbl.setObjectName("settingsHint")
        path_lbl.setWordWrap(True)
        root.addWidget(path_lbl)

        g = QGroupBox(f"모델 폴더: {self._folder_name}")
        fl = QFormLayout(g)
        fl.setSpacing(10)

        data = load_character_settings(repo_root, self._folder_name)
        self.edit_character_name = QLineEdit()
        self.edit_character_name.setText(
            str(data.get(CHARACTER_NAME_KEY, "") or "")
        )
        self.edit_character_name.setPlaceholderText(
            "채팅에 표시되는 캐릭터 이름 (예: Alexia)"
        )
        fl.addRow("이름 (채팅 표시)", self.edit_character_name)

        self._edits: dict[str, QPlainTextEdit] = {}
        placeholders = {
            "personality": "예: 수줍지만 호기심 많음, 진지한 주제엔 진지하게 반응",
            "speech_style": "예: 짧은 문장, 존댓말, 가끔 말끝 흐림",
            "traits_or_habits": "예: 생각할 때 머리 긁음, 날씨 이야기 좋아함",
            "speech_examples": "예: 인사할 때 「안녕하세요… 오늘도 잘 부탁드려요」",
            "restrictions": "예: 폭력적 표현 금지, 특정 주제 회피",
            "extra_instructions": "위 칸에 안 맞는 자유 지시",
        }
        for key, title in SECTION_KEYS:
            ed = QPlainTextEdit()
            ed.setPlainText(str(data.get(key, "") or ""))
            ed.setPlaceholderText(placeholders.get(key, ""))
            ed.setMinimumHeight(72)
            self._edits[key] = ed
            fl.addRow(title, ed)

        root.addWidget(g, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch()
        save_btn = HoverAnimPushButton("저장")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = HoverAnimPushButton("닫기")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _on_save(self) -> None:
        payload: dict = {CHARACTER_NAME_KEY: self.edit_character_name.text()}
        payload.update({key: self._edits[key].toPlainText() for key, _ in SECTION_KEYS})
        ok, msg = save_character_settings(self._repo_root, self._folder_name, payload)
        if not ok:
            QMessageBox.warning(self, "저장 실패", msg)
            return
        QMessageBox.information(self, "저장됨", f"저장했습니다.\n{msg}")
        p = self.parent()
        if p is not None and hasattr(p, "main") and hasattr(p.main, "chat_widget"):
            p.main.chat_widget.apply_assistant_display_settings()
        self.accept()
