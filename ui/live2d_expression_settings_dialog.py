"""Live2D 감정 태그([joy] 등) ↔ 표정 인덱스·모션 그룹 편집 (daon_{folder}_expression_settings.json)."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.widgets.live2d_widget import Live2DWidget
from core.live2d_expression_settings import (
    clear_expression_settings_cache,
    load_expression_overlay,
    normalize_emotion_map,
    save_expression_overlay,
)
from core.model_profile import (
    emotion_motion_group_name,
    idle_motion_group,
    load_expression_catalog_for_folder,
    load_motion_catalog_for_folder,
    model3_json_path_for_folder,
    profile_for_folder,
    repo_root,
)
from ui.hover_button import HoverAnimPushButton

_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Live2DExpressionSettingsDialog(QDialog):
    def __init__(self, folder_name: str, parent=None, *, style_parent=None):
        """
        parent: Qt 부모 창. 미리보기용 두 번째 QOpenGLWidget 은 중첩 모달(환경 설정) 자식이면
        GL/이벤트가 깨지기 쉬우므로, 설정에서 열 때는 parent=메인 창을 권장합니다.
        style_parent: 스타일시트만 이 위젯에서 복사(보통 환경 설정 다이얼로그).
        """
        super().__init__(parent)
        self._folder = (folder_name or "").strip()
        self.setWindowTitle(f"모델 추가 셋팅 — {self._folder}")
        self.setMinimumSize(960, 580)
        self.resize(1080, 640)
        for sp in (style_parent, parent):
            if sp is not None and getattr(sp, "styleSheet", None):
                ss = sp.styleSheet()
                if isinstance(ss, str) and ss.strip():
                    self.setStyleSheet(ss)
                    break

        self._preview_scale = 0.32
        pw = parent
        for _ in range(12):
            if pw is None:
                break
            cfg = getattr(pw, "config", None)
            if isinstance(cfg, dict):
                self._preview_scale = float(
                    (cfg.get("live2d") or {}).get("scale", 0.25)
                )
                break
            nxt = getattr(pw, "main", None)
            if nxt is not None:
                pw = nxt
                continue
            pw = pw.parentWidget()

        self._catalog = load_expression_catalog_for_folder(self._folder)
        self._base = profile_for_folder(self._folder)
        self._overlay = load_expression_overlay(repo_root(), self._folder)
        mk = set(load_motion_catalog_for_folder(self._folder).keys())
        if self._base:
            for k in ("emotionMotionGroup", "idleMotionGroupName"):
                v = self._base.get(k)
                if isinstance(v, str):
                    mk.add(v)
        self._motion_keys = sorted(mk, key=lambda x: (x == "", x.lower()))

        self._block_preview_signals = False
        self._preview_init_scheduled = False
        self._preview_model_loaded = False
        self._preview_live2d: Live2DWidget | None = None
        self._shutting_down = False
        self._preview_teardown_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_inner = QWidget()
        left_l = QVBoxLayout(left_inner)
        left_l.setContentsMargins(4, 4, 8, 4)
        left_l.setSpacing(12)

        hint = QLabel(
            "왼쪽에서 태그와 표정을 연결하고, 오른쪽 미리보기에서 바로 확인합니다. "
            "행을 선택하거나 표정 콤보를 바꾸면 캐릭터 표정이 갱신됩니다."
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        left_l.addWidget(hint)

        path_hint = QLabel(
            f"저장: daon_{self._folder}_expression_settings.json"
        )
        path_hint.setObjectName("settingsHint")
        path_hint.setWordWrap(True)
        left_l.addWidget(path_hint)

        g_motion = QGroupBox("모션 그룹 (감정 모션 재생 시)")
        fm = QFormLayout(g_motion)
        fm.setSpacing(10)
        self.combo_emotion_motion_group = QComboBox()
        self.combo_emotion_motion_group.setEditable(True)
        for k in self._motion_keys:
            disp = "(기본 풀)" if k == "" else k
            self.combo_emotion_motion_group.addItem(disp, k)
        self.combo_idle_group = QComboBox()
        self.combo_idle_group.setEditable(True)
        for k in self._motion_keys:
            disp = "(기본 풀)" if k == "" else k
            self.combo_idle_group.addItem(disp, k)
        fm.addRow("감정 모션 그룹", self.combo_emotion_motion_group)
        fm.addRow("대기 모션 그룹", self.combo_idle_group)
        left_l.addWidget(g_motion)

        g_map = QGroupBox("감정 태그 → 표정")
        gv = QVBoxLayout(g_map)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["태그 ([joy] 키)", "표정 (인덱스)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        gv.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_add = HoverAnimPushButton("행 추가")
        self.btn_add.clicked.connect(self._add_row)
        self.btn_del = HoverAnimPushButton("선택 행 삭제")
        self.btn_del.clicked.connect(self._remove_selected_rows)
        self.btn_reset = HoverAnimPushButton("model_dict 기본으로 되돌리기")
        self.btn_reset.setToolTip(
            "이 모델 폴더의 표정·모션 오버레이 파일을 삭제하고 model_dict 설정만 씁니다."
        )
        self.btn_reset.clicked.connect(self._reset_overlay)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_del)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_reset)
        gv.addLayout(btn_row)
        left_l.addWidget(g_map, 1)

        scroll.setWidget(left_inner)
        split.addWidget(scroll)

        preview = QFrame()
        preview.setObjectName("expressionPreviewFrame")
        preview.setStyleSheet(
            "QFrame#expressionPreviewFrame {"
            " background-color: #11111b;"
            " border: 1px solid #313244;"
            " border-radius: 12px;"
            " }"
            "QLabel#expressionPreviewTitle { color: #bac2de; font-weight: 600; font-size: 13px; }"
        )
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(14, 12, 14, 12)
        pv.setSpacing(8)

        title = QLabel("Live2D 미리보기")
        title.setObjectName("expressionPreviewTitle")
        pv.addWidget(title)
        wh = QLabel("휠: 크기 조절 · 왼쪽 버튼 드래그: 모델 위치(뷰 이동)")
        wh.setObjectName("settingsHint")
        wh.setWordWrap(True)
        pv.addWidget(wh)

        scrub_l = QHBoxLayout()
        scrub_l.addWidget(QLabel("표정 훑어보기"))
        self._scrub_combo = QComboBox()
        self._populate_expression_combo_items(self._scrub_combo)
        self._scrub_combo.currentIndexChanged.connect(self._on_scrub_preview_changed)
        scrub_l.addWidget(self._scrub_combo, 1)
        pv.addLayout(scrub_l)

        self._preview_live2d = Live2DWidget(preview, embed_preview_controls=True)
        self._preview_live2d.setMinimumSize(300, 380)
        self._preview_live2d.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        pv.addWidget(self._preview_live2d, 1)

        self._lbl_preview_status = QLabel("모델을 불러오는 중…")
        self._lbl_preview_status.setObjectName("settingsHint")
        self._lbl_preview_status.setWordWrap(True)
        pv.addWidget(self._lbl_preview_status)

        split.addWidget(preview)
        split.setStretchFactor(0, 100)
        split.setStretchFactor(1, 72)
        split.setSizes([560, 420])

        root.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel = HoverAnimPushButton("닫기")
        cancel.setObjectName("secondaryBtn")
        cancel.clicked.connect(self.reject)
        save = HoverAnimPushButton("저장")
        save.setObjectName("primaryBtn")
        save.clicked.connect(self._on_save)
        foot.addWidget(cancel)
        foot.addWidget(save)
        root.addLayout(foot)

        self._load_motion_combos()
        self._fill_table_from_state()
        sm = self.table.selectionModel()
        if sm is not None:
            sm.currentRowChanged.connect(self._on_table_current_row_changed)

    def _teardown_preview_before_close(self) -> None:
        """accept/reject/done 경로는 closeEvent 없이 WA_DeleteOnClose 만 일어날 수 있어 여기서 정리."""
        if self._preview_teardown_done:
            return
        self._preview_teardown_done = True
        self._shutting_down = True
        pv = self._preview_live2d
        if pv is not None:
            pv.prepare_for_embedded_teardown()

    def done(self, r: int) -> None:
        self._teardown_preview_before_close()
        super().done(r)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._teardown_preview_before_close()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._preview_init_scheduled:
            self._preview_init_scheduled = True
            QTimer.singleShot(100, self._init_preview_model)

    def _populate_expression_combo_items(self, cb: QComboBox) -> None:
        cb.clear()
        if self._catalog:
            for i, name in self._catalog:
                cb.addItem(f"{i} — {name}", i)
        else:
            for j in range(0, 64):
                cb.addItem(str(j), j)

    def _expr_label_for_index(self, idx: int) -> str:
        for i, name in self._catalog:
            if i == idx:
                return str(name)
        return str(idx)

    def _init_preview_model(self) -> None:
        if self._shutting_down:
            return
        if self._preview_model_loaded or self._preview_live2d is None:
            return
        path = model3_json_path_for_folder(self._folder)
        if not path or not os.path.isfile(path):
            self._lbl_preview_status.setText(
                "이 폴더에서 model3.json 을 찾을 수 없습니다. 미리보기만 생략됩니다."
            )
            self._preview_model_loaded = True
            return
        self._preview_live2d.load_model(path, self._preview_scale, self._folder)
        self._preview_model_loaded = True
        QTimer.singleShot(150, self._first_preview_apply)

    def _first_preview_apply(self) -> None:
        if self._shutting_down:
            return
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self._apply_preview_index(0)

    def _on_table_current_row_changed(self, current, _previous) -> None:
        if not current.isValid():
            return
        r = current.row()
        cb = self.table.cellWidget(r, 1)
        if isinstance(cb, QComboBox):
            self._sync_scrub_from_combo(cb)
            self._apply_preview_from_combo(cb)

    def _on_scrub_preview_changed(self, _index: int) -> None:
        if self._block_preview_signals:
            return
        cb = self._scrub_combo
        d = cb.currentData()
        if d is None:
            d = cb.currentIndex()
        self._apply_preview_index(int(d))

    def _sync_scrub_from_combo(self, row_cb: QComboBox) -> None:
        d = row_cb.currentData()
        if d is None:
            d = row_cb.currentIndex()
        self._block_preview_signals = True
        try:
            for i in range(self._scrub_combo.count()):
                if self._scrub_combo.itemData(i) == d:
                    self._scrub_combo.setCurrentIndex(i)
                    break
        finally:
            self._block_preview_signals = False

    def _apply_preview_from_combo(self, row_cb: QComboBox) -> None:
        d = row_cb.currentData()
        if d is None:
            d = row_cb.currentIndex()
        self._apply_preview_index(int(d))

    def _apply_preview_index(self, idx: int) -> None:
        if self._preview_live2d is None:
            return
        ok = self._preview_live2d.set_expression_preview_index(idx)
        if ok:
            r = self.table.currentRow()
            if r >= 0:
                self._update_preview_caption_for_row(r)
            else:
                self._lbl_preview_status.setText(
                    f"표정 {idx} — {self._expr_label_for_index(idx)}"
                )
        elif self._preview_model_loaded:
            model_path = model3_json_path_for_folder(self._folder)
            if model_path and os.path.isfile(model_path):
                self._lbl_preview_status.setText(
                    f"표정 인덱스 {idx} 을(를) 적용할 수 없습니다. 모델 표정 개수를 확인하세요."
                )

    def _update_preview_caption_for_row(self, r: int) -> None:
        w0 = self.table.cellWidget(r, 0)
        w1 = self.table.cellWidget(r, 1)
        if not isinstance(w0, QLineEdit) or not isinstance(w1, QComboBox):
            return
        tag = w0.text().strip().lower()
        d = w1.currentData()
        if d is None:
            d = w1.currentIndex()
        idx = int(d)
        name = self._expr_label_for_index(idx)
        if tag:
            self._lbl_preview_status.setText(f"[{tag}] → 표정 {idx} ({name})")
        else:
            self._lbl_preview_status.setText(f"표정 {idx} — {name}")

    def _on_row_expression_changed(self, row_cb: QComboBox) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        if self.table.cellWidget(r, 1) is not row_cb:
            return
        self._sync_scrub_from_combo(row_cb)
        self._apply_preview_from_combo(row_cb)
        self._update_preview_caption_for_row(r)

    def _wire_row_expression_combo(self, cb: QComboBox) -> None:
        cb.currentIndexChanged.connect(
            lambda _i, c=cb: self._on_row_expression_changed(c)
        )

    def _find_combo_data(self, combo: QComboBox, value: str) -> None:
        v = value.strip()
        for i in range(combo.count()):
            if combo.itemData(i) == v:
                combo.setCurrentIndex(i)
                return
        combo.setEditText(v)

    def _load_motion_combos(self) -> None:
        base = self._base
        ov = self._overlay
        if "emotionMotionGroup" in ov and isinstance(ov["emotionMotionGroup"], str):
            eg = ov["emotionMotionGroup"]
        elif base:
            eg = emotion_motion_group_name(base)
        else:
            eg = ""
        if "idleMotionGroupName" in ov and isinstance(ov["idleMotionGroupName"], str):
            ig = ov["idleMotionGroupName"]
        elif base:
            ig = idle_motion_group(base)
        else:
            ig = "Idle"
        self._find_combo_data(self.combo_emotion_motion_group, eg)
        self._find_combo_data(self.combo_idle_group, ig)

    def _current_motion_group_value(self, combo: QComboBox) -> str:
        d = combo.currentData()
        if isinstance(d, str):
            return d
        t = combo.currentText().strip()
        for i in range(combo.count()):
            if combo.itemText(i) == t:
                dd = combo.itemData(i)
                if isinstance(dd, str):
                    return dd
        return t

    def _display_emotion_map(self) -> dict[str, int]:
        ov = self._overlay
        if "emotionMap" in ov:
            return normalize_emotion_map(ov.get("emotionMap"))
        return normalize_emotion_map((self._base or {}).get("emotionMap"))

    def _make_expression_combo(self, selected_index: int) -> QComboBox:
        cb = QComboBox()
        self._populate_expression_combo_items(cb)
        if self._catalog:
            idx = next((j for j in range(cb.count()) if cb.itemData(j) == selected_index), -1)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                cb.setCurrentIndex(max(0, min(selected_index, cb.count() - 1)))
        else:
            cb.setCurrentIndex(max(0, min(selected_index, cb.count() - 1)))
        self._wire_row_expression_combo(cb)
        return cb

    def _fill_table_from_state(self) -> None:
        self.table.setRowCount(0)
        em = self._display_emotion_map()
        if not em and self._base:
            em = normalize_emotion_map(self._base.get("emotionMap"))
        for tag in sorted(em.keys()):
            self._append_row(tag, em[tag])

    def _append_row(self, tag: str, expr_index: int) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        ed = QLineEdit(tag)
        ed.textEdited.connect(self._on_any_tag_edited)
        self.table.setCellWidget(r, 0, ed)
        self.table.setCellWidget(r, 1, self._make_expression_combo(expr_index))

    def _on_any_tag_edited(self) -> None:
        snd = self.sender()
        if not isinstance(snd, QLineEdit):
            return
        for rr in range(self.table.rowCount()):
            if self.table.cellWidget(rr, 0) is snd:
                if self.table.currentRow() == rr:
                    self._update_preview_caption_for_row(rr)
                break

    def _add_row(self) -> None:
        n = 1
        while True:
            cand = f"tag{n}"
            taken = False
            for r in range(self.table.rowCount()):
                w = self.table.cellWidget(r, 0)
                if isinstance(w, QLineEdit) and w.text().strip().lower() == cand:
                    taken = True
                    break
            if not taken:
                break
            n += 1
        self._append_row(cand, 0)
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove_selected_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _reset_overlay(self) -> None:
        r = QMessageBox.question(
            self,
            "되돌리기",
            "저장된 표정·모션 오버레이를 삭제하고 model_dict 기본값만 사용할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        ok, msg = save_expression_overlay(repo_root(), self._folder, {})
        if not ok:
            QMessageBox.warning(self, "오류", msg)
            return
        clear_expression_settings_cache(self._folder)
        self._overlay = {}
        self._load_motion_combos()
        self._fill_table_from_state()
        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self._apply_preview_index(0)
        QMessageBox.information(self, "완료", "오버레이를 제거했습니다.")

    def _collect_emotion_map(self) -> dict[str, int] | None:
        out: dict[str, int] = {}
        for r in range(self.table.rowCount()):
            w0 = self.table.cellWidget(r, 0)
            w1 = self.table.cellWidget(r, 1)
            if not isinstance(w0, QLineEdit) or not isinstance(w1, QComboBox):
                continue
            tag = w0.text().strip().lower()
            if not tag:
                continue
            if tag in out:
                QMessageBox.warning(
                    self,
                    "중복 태그",
                    f"태그「{tag}」가 여러 행에 있습니다. 중복을 제거한 뒤 저장하세요.",
                )
                return None
            if not _TAG_RE.match(tag):
                QMessageBox.warning(
                    self,
                    "태그 형식",
                    f"태그「{tag}」는 소문자 영문으로 시작하고 영문·숫자·_ 만 사용할 수 있습니다.",
                )
                return None
            d = w1.currentData()
            if d is None:
                d = w1.currentIndex()
            out[tag] = int(d)
        return out

    def _base_motion_strings(self) -> tuple[dict[str, int], str, str]:
        b = self._base
        if not b:
            return {}, "", "Idle"
        em = normalize_emotion_map(b.get("emotionMap"))
        eg = emotion_motion_group_name(b)
        ig = idle_motion_group(b)
        return em, eg, ig

    def _build_save_payload(self, em: dict[str, int], eg: str, ig: str) -> dict:
        base_em, base_eg, base_ig = self._base_motion_strings()
        payload: dict = {"version": 1}
        if em != base_em:
            payload["emotionMap"] = em
        if eg != base_eg:
            payload["emotionMotionGroup"] = eg
        if ig != base_ig:
            payload["idleMotionGroupName"] = ig
        if len(payload) == 1:
            return {}
        return payload

    def _on_save(self) -> None:
        em = self._collect_emotion_map()
        if em is None:
            return
        eg = self._current_motion_group_value(self.combo_emotion_motion_group)
        ig = self._current_motion_group_value(self.combo_idle_group)
        payload = self._build_save_payload(em, eg, ig)
        ok, msg = save_expression_overlay(repo_root(), self._folder, payload)
        if not ok:
            QMessageBox.warning(self, "저장 실패", msg)
            return
        self._overlay = load_expression_overlay(repo_root(), self._folder)
        QMessageBox.information(self, "저장됨", msg if payload else "오버레이 없음(model_dict만 사용).")
        self.raise_()
        self.activateWindow()
        m = self.parent()
        while m is not None:
            cfg = getattr(m, "config", None)
            if isinstance(cfg, dict) and hasattr(m, "vmate_manager"):
                if hasattr(m, "_merged_config_for_vm"):
                    m.vmate_manager.reload_from_config(m._merged_config_for_vm())
                else:
                    m.vmate_manager.reload_from_config(m.config)
                break
            nxt = getattr(m, "main", None)
            if nxt is not None:
                m = nxt
                continue
            m = m.parentWidget()
        self.accept()


Live2DExpressionSettingsPanel = Live2DExpressionSettingsDialog
