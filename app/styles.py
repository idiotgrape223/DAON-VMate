"""반복되는 Qt 스타일시트."""

# Live2D 뷰 상단 툴바 높이(MainWindow 레이아웃과 맞출 것)
LIVE2D_TOP_BAR_HEIGHT_PX = 52

# 캐릭터 모드만(투명 무테) 창 위에서 기본 QMenu가 비어 보이는 경우 대비
PET_DESKTOP_CONTEXT_MENU_QSS = """
QMenu#petDesktopContextMenu {
    background-color: #1e1e2e;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 6px 0px;
}
QMenu#petDesktopContextMenu::item {
    background-color: transparent;
    color: #cdd6f4;
    padding: 10px 28px 10px 18px;
    min-width: 170px;
}
QMenu#petDesktopContextMenu::item:selected {
    background-color: #45475a;
    color: #89b4fa;
}
"""

# Live2D 상단 오버레이: 반투명 바 + 자식 QPushButton (화면 공유 / 캐릭터 모드 / 설정)
LIVE2D_TOP_BAR_QSS = """
#live2dTopBar {
    background-color: rgba(24, 24, 37, 0.9);
    border: none;
    border-bottom: 1px solid rgba(137, 180, 250, 0.3);
}
#live2dTopBar QPushButton {
    padding: 7px 16px;
    min-height: 22px;
    background-color: rgba(49, 50, 68, 0.96);
    color: #e8e9f5;
    border: 1px solid rgba(88, 91, 112, 0.85);
    border-radius: 9px;
}
#live2dTopBar QPushButton:hover {
    background-color: rgba(69, 71, 90, 0.98);
    border-color: rgba(137, 180, 250, 0.55);
    color: #ffffff;
}
#live2dTopBar QPushButton:pressed {
    background-color: rgba(88, 91, 112, 0.98);
    border-color: #89b4fa;
}
#live2dTopBar QPushButton::menu-indicator {
    width: 14px;
    height: 8px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
}
"""

# 하위 호환·다른 화면용(필요 시)
OVERLAY_PANEL_BUTTON_QSS = (
    "QPushButton { padding: 8px 15px; background: rgba(255,255,255,230); "
    "border-radius: 5px; font-weight: bold; border: none; color: #222; }"
    "QPushButton:hover { background: rgba(255,255,255,255); }"
    "QPushButton:pressed { background: rgba(230,230,230,240); }"
)

OVERLAY_PANEL_BUTTON_QSS_COMPACT = (
    "QPushButton { padding: 8px 12px; background: rgba(255,255,255,230); "
    "border-radius: 5px; font-weight: bold; border: none; color: #222; }"
    "QPushButton:hover { background: rgba(255,255,255,255); }"
    "QPushButton:pressed { background: rgba(230,230,230,240); }"
)
