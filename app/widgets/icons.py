from __future__ import annotations

from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def make_paperclip_icon() -> QIcon:
    pm = QPixmap(26, 26)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(50, 55, 70))
    pen.setWidthF(2.25)
    p.setPen(pen)
    p.drawArc(5, 5, 10, 12, 35 * 16, 220 * 16)
    p.drawArc(9, 9, 10, 12, 215 * 16, 220 * 16)
    p.end()
    return QIcon(pm)
