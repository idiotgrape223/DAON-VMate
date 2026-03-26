"""메인 창 왼쪽: 모델별 채팅 세션 목록."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.chat_session_store import list_sessions
from core.model_profile import repo_root
from ui.hover_button import HoverAnimPushButton

if TYPE_CHECKING:
    from app.windows.main_window import MainWindow

SESSION_ID_ROLE = Qt.ItemDataRole.UserRole + 1

SIDEBAR_DARK_QSS = """
QWidget#ChatHistorySidebarRoot {
    background-color: #12121a;
}
QWidget#chatHistSep {
    background-color: #2a2a36;
    border: none;
    min-height: 1px;
    max-height: 1px;
}
QLabel#chatHistTitle {
    color: #f2f3f7;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.02em;
    background-color: transparent;
    border: none;
    padding: 0px;
}
QLabel#chatHistHint {
    color: #8f90a0;
    font-size: 10px;
    line-height: 1.35;
    background-color: transparent;
    border: none;
    padding: 0px;
}
QFrame#histListShell {
    background-color: #1a1a24;
    border: none;
    border-radius: 8px;
}
QWidget#chatHistListViewport {
    background-color: #1a1a24;
    border: none;
}
QListWidget#chatHistList {
    background-color: #1a1a24;
    color: #e8e8f0;
    border: none;
    border-radius: 8px;
    padding: 6px 4px;
    outline: none;
    font-size: 13px;
}
QListWidget#chatHistList::item {
    padding: 10px 12px;
    border-radius: 6px;
    margin: 2px 6px;
    border: none;
    min-height: 20px;
}
QListWidget#chatHistList::item:selected {
    background-color: #2e3350;
    color: #b8ccff;
}
QListWidget#chatHistList::item:hover:!selected {
    background-color: #23232e;
}
QPushButton#primaryBtn {
    background-color: #7c9ef0;
    color: #0d0d12;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 13px;
    min-height: 40px;
}
QPushButton#primaryBtn:hover {
    background-color: #9bb4f7;
}
QPushButton#primaryBtn:pressed {
    background-color: #6b8fe8;
}
QMenu {
    background-color: #22222e;
    color: #e8e8f0;
    border: 1px solid #3d3d4d;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 8px 28px 8px 14px;
}
QMenu::item:selected {
    background-color: #3d3d52;
}
"""

SIDEBAR_LIGHT_QSS = """
QWidget#ChatHistorySidebarRoot {
    background-color: #ebeff5;
}
QWidget#chatHistSep {
    background-color: #cfd6e0;
    border: none;
    min-height: 1px;
    max-height: 1px;
}
QLabel#chatHistTitle {
    color: #141820;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.02em;
    background-color: transparent;
    border: none;
    padding: 0px;
}
QLabel#chatHistHint {
    color: #5c6570;
    font-size: 10px;
    line-height: 1.35;
    background-color: transparent;
    border: none;
    padding: 0px;
}
QFrame#histListShell {
    background-color: #ffffff;
    border: 1px solid #d8dee8;
    border-radius: 8px;
}
QWidget#chatHistListViewport {
    background-color: #ffffff;
    border: none;
}
QListWidget#chatHistList {
    background-color: #ffffff;
    color: #1a1f26;
    border: none;
    border-radius: 8px;
    padding: 6px 4px;
    outline: none;
    font-size: 13px;
}
QListWidget#chatHistList::item {
    padding: 10px 12px;
    border-radius: 6px;
    margin: 2px 6px;
    border: none;
    min-height: 20px;
}
QListWidget#chatHistList::item:selected {
    background-color: #e8f1ff;
    color: #0b5cbf;
}
QListWidget#chatHistList::item:hover:!selected {
    background-color: #f4f6fa;
}
QPushButton#primaryBtn {
    background-color: #0d6efd;
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 13px;
    min-height: 40px;
}
QPushButton#primaryBtn:hover {
    background-color: #2b7fff;
}
QPushButton#primaryBtn:pressed {
    background-color: #0b5ed7;
}
QMenu {
    background-color: #ffffff;
    color: #1a1f26;
    border: 1px solid #d8dee8;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 8px 28px 8px 14px;
}
QMenu::item:selected {
    background-color: #e8f1ff;
}
"""


class _SolidSep(QWidget):
    """QSS가 레이어드 부모에서 무시될 때를 대비해 단색으로 직접 칠합니다."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._c = QColor("#2a2a36")

    def set_solid_color(self, c: QColor) -> None:
        self._c = QColor(c)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._c)
        p.end()


class _SolidListShell(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._c = QColor("#1a1a24")

    def set_solid_color(self, c: QColor) -> None:
        self._c = QColor(c)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._c)
        p.end()
        super().paintEvent(event)


class _SolidListViewport(QWidget):
    """목록 실제 그리기 대상. QAbstractItemView는 paintEvent 없이 QPainter(viewport())로 그릴 수 있어 색 접근용."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._c = QColor("#1a1a24")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def set_solid_color(self, c: QColor) -> None:
        self._c = QColor(c)
        self.update()

    def solid_color(self) -> QColor:
        return self._c

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._c)
        p.end()


class _SolidBackedListWidget(QListWidget):
    """뷰가 viewport에 직접 그리기 전에 바닥색을 한 번 깔아 레이어드 창에서 투명 구멍이 나지 않게 함."""

    def paintEvent(self, event) -> None:
        vp = self.viewport()
        if isinstance(vp, _SolidListViewport):
            p = QPainter(vp)
            p.fillRect(vp.rect(), vp.solid_color())
            p.end()
        super().paintEvent(event)


class ChatHistorySidebar(QWidget):
    def __init__(self, main_window: MainWindow):
        super().__init__(main_window)
        self.setObjectName("ChatHistorySidebarRoot")
        self._main = main_window
        self._root_bg = QColor("#12121a")
        self._root_border = QColor("#2a2a36")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(0)

        t = QLabel("대화 기록")
        t.setObjectName("chatHistTitle")
        _tf = QFont()
        _tf.setPointSize(10)
        _tf.setWeight(QFont.Weight.DemiBold)
        t.setFont(_tf)
        lay.addWidget(t)

        lay.addSpacing(10)

        self._sep = _SolidSep()
        self._sep.setObjectName("chatHistSep")
        self._sep.setFixedHeight(1)
        self._sep.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        lay.addWidget(self._sep)
        lay.addSpacing(12)

        self._list_shell = _SolidListShell()
        self._list_shell.setObjectName("histListShell")
        self._list_shell.setFrameShape(QFrame.Shape.NoFrame)
        shell_lay = QVBoxLayout(self._list_shell)
        shell_lay.setContentsMargins(0, 0, 0, 0)
        shell_lay.setSpacing(0)

        self._list = _SolidBackedListWidget()
        self._list.setObjectName("chatHistList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._list.setAutoFillBackground(True)
        self._list_viewport = _SolidListViewport(self._list)
        self._list_viewport.setObjectName("chatHistListViewport")
        self._list.setViewport(self._list_viewport)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.itemClicked.connect(self._on_item_clicked)
        shell_lay.addWidget(self._list)
        lay.addWidget(self._list_shell, 1)
        lay.addSpacing(12)

        self._btn_new = HoverAnimPushButton("새 대화")
        self._btn_new.setObjectName("primaryBtn")
        self._btn_new.clicked.connect(self._main.new_chat_session)
        lay.addWidget(self._btn_new)

    def paintEvent(self, event) -> None:
        """메인 창과 같이 단색을 직접 칠함(레이어드 부모 아래에서 QSS 배경이 비치는 문제 우회)."""
        p = QPainter(self)
        p.fillRect(self.rect(), self._root_bg)
        pen = QPen(self._root_border)
        pen.setWidth(1)
        p.setPen(pen)
        r = self.rect()
        p.drawLine(r.right(), r.top(), r.right(), r.bottom())
        p.end()
        super().paintEvent(event)

    def apply_dark_mode(self, dark: bool) -> None:
        self.setStyleSheet(SIDEBAR_DARK_QSS if dark else SIDEBAR_LIGHT_QSS)
        self._apply_sidebar_palette(dark)

    def _apply_sidebar_palette(self, dark: bool) -> None:
        if dark:
            win = QColor("#12121a")
            list_bg = QColor("#1a1a24")
            line = QColor("#2a2a36")
            text = QColor("#e8e8f0")
            hi = QColor("#2e3350")
            hi_text = QColor("#b8ccff")
        else:
            win = QColor("#ebeff5")
            list_bg = QColor("#ffffff")
            line = QColor("#cfd6e0")
            text = QColor("#1a1f26")
            hi = QColor("#e8f1ff")
            hi_text = QColor("#0b5cbf")

        self._root_bg = QColor(win)
        self._root_border = QColor(line)
        self._sep.set_solid_color(line)
        self._list_shell.set_solid_color(list_bg)
        self._list_viewport.set_solid_color(list_bg)
        self.update()

        pr = QPalette()
        pr.setColor(QPalette.ColorRole.Window, win)
        pr.setColor(QPalette.ColorRole.Base, win)
        self.setPalette(pr)
        self.setAutoFillBackground(True)

        pl = QPalette()
        pl.setColor(QPalette.ColorRole.Window, list_bg)
        pl.setColor(QPalette.ColorRole.Base, list_bg)
        pl.setColor(QPalette.ColorRole.Text, text)
        pl.setColor(QPalette.ColorRole.Highlight, hi)
        pl.setColor(QPalette.ColorRole.HighlightedText, hi_text)
        self._list.setPalette(pl)
        self._list.setAutoFillBackground(True)
        self._list_viewport.setPalette(pl)
        self._list_viewport.setAutoFillBackground(True)

        ps = QPalette()
        ps.setColor(QPalette.ColorRole.Window, line)
        ps.setColor(QPalette.ColorRole.Base, line)
        self._sep.setPalette(ps)
        self._sep.setAutoFillBackground(True)

        shell_bg = list_bg
        psh = QPalette()
        psh.setColor(QPalette.ColorRole.Window, shell_bg)
        psh.setColor(QPalette.ColorRole.Base, shell_bg)
        self._list_shell.setPalette(psh)
        self._list_shell.setAutoFillBackground(True)

    def refresh_list(self, *, select_id: str | None = None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        mf = self._main.current_live2d_model_folder()
        if not mf:
            self._list.blockSignals(False)
            return
        for row in list_sessions(repo_root(), mf):
            sid = str(row.get("id") or "")
            title = str(row.get("title") or sid[:8])
            it = QListWidgetItem(title)
            it.setData(SESSION_ID_ROLE, sid)
            self._list.addItem(it)
        self._list.blockSignals(False)

        pick = select_id
        if not pick:
            pick = self._main.active_chat_session_id()
        if pick:
            self.select_session(pick)
        elif self._list.count() > 0:
            self._list.setCurrentRow(0)

    def select_session(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it and it.data(SESSION_ID_ROLE) == sid:
                self._list.setCurrentItem(it)
                break
        self._list.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        sid = str(item.data(SESSION_ID_ROLE) or "").strip()
        if not sid:
            return
        self._main.activate_chat_session(sid)

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        act_rename = menu.addAction("이름 바꾸기")
        act_del = menu.addAction("삭제")
        if item is None:
            act_rename.setEnabled(False)
            act_del.setEnabled(False)
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is None or item is None:
            return
        sid = str(item.data(SESSION_ID_ROLE) or "")
        if not sid:
            return
        if chosen == act_rename:
            self._main.rename_chat_session_interactive(sid)
        elif chosen == act_del:
            self._main.delete_chat_session_interactive(sid)
