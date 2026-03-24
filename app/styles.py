"""반복되는 Qt 스타일시트."""

# Live2D 뷰 상단 툴바 높이(MainWindow 레이아웃과 맞출 것)
LIVE2D_TOP_BAR_HEIGHT_PX = 52

# Live2D 우클릭(표정·모션) 메뉴: 검정 텍스트 + 밝은 배경
LIVE2D_CONTEXT_MENU_QSS = """
QMenu#live2dContextMenu {
    background-color: #ffffff;
    border: 1px solid #c8ccd4;
    border-radius: 8px;
    padding: 6px 0px;
}
QMenu#live2dContextMenu::item {
    background-color: transparent;
    color: #000000;
    padding: 10px 28px 10px 18px;
    min-width: 170px;
}
QMenu#live2dContextMenu::item:selected {
    background-color: #e8eaef;
    color: #000000;
}
"""

# Live2D 우클릭 서브메뉴(표정·모션 그룹 팝업): objectName 없이 QMenu 단독 스타일
LIVE2D_CONTEXT_SUBMENU_QSS = """
QMenu {
    background-color: #ffffff;
    border: 1px solid #c8ccd4;
    border-radius: 8px;
    padding: 4px 0px;
}
QMenu::item {
    color: #000000;
    padding: 8px 22px 8px 14px;
    min-width: 140px;
}
QMenu::item:selected {
    background-color: #e8eaef;
    color: #000000;
}
"""

# Live2D 상단 오버레이: 반투명 바 + 자식 QPushButton (화면 공유 / 캐릭터 모드 / 대화 기록 / 설정)
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
#live2dTopBar QPushButton#chatHistoryToggleBtn:checked {
    background-color: rgba(137, 180, 250, 0.24);
    border-color: rgba(137, 180, 250, 0.8);
    color: #ffffff;
}
"""

LIVE2D_TOP_BAR_LIGHT_QSS = """
#live2dTopBar {
    background-color: rgba(248, 249, 252, 0.94);
    border: none;
    border-bottom: 1px solid rgba(0, 86, 179, 0.18);
}
#live2dTopBar QPushButton {
    padding: 7px 16px;
    min-height: 22px;
    background-color: rgba(255, 255, 255, 0.98);
    color: #222222;
    border: 1px solid #c8ccd4;
    border-radius: 9px;
}
#live2dTopBar QPushButton:hover {
    background-color: #ffffff;
    border-color: #007bff;
    color: #0056b3;
}
#live2dTopBar QPushButton:pressed {
    background-color: #e9ecef;
    border-color: #0069d9;
}
#live2dTopBar QPushButton::menu-indicator {
    width: 14px;
    height: 8px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
}
#live2dTopBar QPushButton#chatHistoryToggleBtn:checked {
    background-color: rgba(13, 110, 253, 0.12);
    border-color: #0d6efd;
    color: #0b5cbf;
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
