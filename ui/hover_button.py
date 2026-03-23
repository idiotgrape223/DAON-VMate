"""호버 시 그림자·블러가 부드럽게 커지는 QPushButton."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation
from PySide6.QtGui import QColor, QEnterEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QPushButton


class HoverAnimPushButton(QPushButton):
    """
    마우스를 올리면 drop shadow 블러가 애니메이션으로 커졌다가,
    벗어나면 줄어듭니다. (스타일시트 :hover 색상 변화와 함께 쓸 수 있음)
    """

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        hover_blur: int = 18,
        duration_ms: int = 200,
        shadow_color: QColor | None = None,
        shadow_offset_y: int = 3,
    ) -> None:
        super().__init__(text, parent)
        self._hover_blur = max(0, int(hover_blur))
        self._duration_ms = max(1, int(duration_ms))
        c = shadow_color or QColor(0, 0, 0, 72)

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(0)
        self._shadow.setOffset(0, shadow_offset_y)
        self._shadow.setColor(c)
        self.setGraphicsEffect(self._shadow)

        self._anim_in = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._anim_in.setDuration(self._duration_ms)
        self._anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_out = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._anim_out.setDuration(self._duration_ms)
        self._anim_out.setEasingCurve(QEasingCurve.Type.InCubic)

    def enterEvent(self, event: QEnterEvent) -> None:
        if self.isEnabled():
            self._animate_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate_hover(False)
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.EnabledChange
            and not self.isEnabled()
        ):
            self._animate_hover(False)

    def _animate_hover(self, hover_in: bool) -> None:
        if hover_in:
            self._anim_out.stop()
            start = int(self._shadow.blurRadius())
            self._anim_in.setStartValue(start)
            self._anim_in.setEndValue(self._hover_blur)
            self._anim_in.start()
        else:
            self._anim_in.stop()
            start = int(self._shadow.blurRadius())
            self._anim_out.setStartValue(start)
            self._anim_out.setEndValue(0)
            self._anim_out.start()
