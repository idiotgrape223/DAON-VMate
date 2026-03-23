"""데스크톱·창 캡처 후 LLM 이미지 첨부로 쓰기 위한 유틸."""

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QScreen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from core.llm_attachments import LLMMediaAttachment

# llm_attachments._MAX_IMAGE_BYTES 와 동일하게 유지
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _scale_image_max_side(img: QImage, max_side: int) -> QImage:
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    m = max(w, h)
    if m <= max_side:
        return img
    s = max_side / float(m)
    nw = max(1, int(w * s))
    nh = max(1, int(h * s))
    return img.scaled(
        nw,
        nh,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def pixmap_to_llm_attachment(
    pm: QPixmap,
    original_name: str = "screen_share.jpg",
) -> Optional[LLMMediaAttachment]:
    """PNG/JPEG 바이트로 변환. 용량 초과 시 축소·품질 낮춤."""
    if pm.isNull():
        return None
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGB32)
    if img.isNull():
        return None

    for max_side in (1600, 1280, 1024, 768, 640):
        scaled = _scale_image_max_side(img, max_side)
        for quality in (88, 78, 68, 58, 48, 38):
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            if not scaled.save(buf, "JPEG", quality):
                break
            raw = bytes(ba)
            if raw and len(raw) <= _MAX_IMAGE_BYTES:
                return LLMMediaAttachment(
                    mime_type="image/jpeg",
                    raw_bytes=raw,
                    original_name=original_name,
                )
        img = scaled
    return None


def grab_full_virtual_desktop() -> QPixmap:
    scr = QGuiApplication.primaryScreen()
    if scr is None:
        return QPixmap()
    return scr.grabWindow(0)


def grab_monitor(screen: QScreen) -> QPixmap:
    g = screen.geometry()
    return screen.grabWindow(0, g.x(), g.y(), g.width(), g.height())


def _qimage_mostly_black(img: QImage, sample_step: int = 24) -> bool:
    """
    캡처 실패 시(빈 DC·BitBlt) 거의 순검정만 나오는 경우 감지.
    터미널 등 어두운 UI는 샘플 중 일부라도 밝은 픽셀이 있으면 실패로 보지 않음.
    """
    if img.isNull():
        return True
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return True
    dark = 0
    total = 0
    peak = 0
    for y in range(0, h, sample_step):
        for x in range(0, w, sample_step):
            c = img.pixel(x, y)
            r = (c >> 16) & 0xFF
            g = (c >> 8) & 0xFF
            b = c & 0xFF
            m = max(r, g, b)
            peak = max(peak, m)
            if r + g + b < 20:
                dark += 1
            total += 1
    if total <= 0:
        return True
    return dark / float(total) > 0.94 and peak < 12


def _grab_native_window_win32_printwindow(hwnd: int) -> QPixmap:
    """
    PrintWindow + PW_RENDERFULLCONTENT.
    Chrome, Electron, 게임 등 GPU 합성 창은 Qt grabWindow(hwnd)가 검은 화면만 주는 경우가 많음.
    """
    import ctypes
    from ctypes import wintypes

    hwnd = int(hwnd)
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    PW_CLIENTONLY = 0x00000001
    PW_RENDERFULLCONTENT = 0x00000002

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER)]

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return QPixmap()
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    if w <= 0 or h <= 0:
        return QPixmap()
    # 비정상적으로 큰 창은 메모리 폭주 방지 (드물게 그림자/좌표 오류)
    if w * h > 45_000_000:
        return QPixmap()

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return QPixmap()
    hdc_mem = None
    hbmp = None
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            return QPixmap()
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hbmp:
            return QPixmap()
        gdi32.SelectObject(hdc_mem, hbmp)

        flags_list = (
            PW_RENDERFULLCONTENT | PW_CLIENTONLY,
            PW_RENDERFULLCONTENT,
            PW_CLIENTONLY,
            0,
        )
        printed = False
        for fl in flags_list:
            if user32.PrintWindow(hwnd, hdc_mem, int(fl)):
                printed = True
                break

        if not printed:
            return QPixmap()

        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth = w
        bih.biHeight = -h
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0

        bmi = BITMAPINFO()
        bmi.bmiHeader = bih

        row_bytes = ((w * 32 + 31) // 32) * 4
        buf = (ctypes.c_ubyte * (row_bytes * h))()
        lines = gdi32.GetDIBits(
            hdc_mem,
            hbmp,
            0,
            ctypes.c_uint(h),
            ctypes.cast(buf, ctypes.c_void_p),
            ctypes.byref(bmi),
            0,
        )
        if lines == 0:
            return QPixmap()

        data = bytes(buf)
        qimg = QImage(data, w, h, row_bytes, QImage.Format.Format_ARGB32)
        if qimg.isNull():
            return QPixmap()
        qimg = qimg.copy()
        pm = QPixmap.fromImage(qimg)
        return pm if not pm.isNull() else QPixmap()
    finally:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


def grab_native_window(hwnd: int) -> QPixmap:
    hwnd = int(hwnd)
    if sys.platform == "win32":
        pm = _grab_native_window_win32_printwindow(hwnd)
        if not pm.isNull() and not _qimage_mostly_black(pm.toImage()):
            return pm
    scr = QGuiApplication.primaryScreen()
    if scr is None:
        return QPixmap()
    return scr.grabWindow(hwnd)


def list_visible_windows_win32() -> list[tuple[int, str]]:
    """(hwnd, title) 목록. Windows 전용."""
    if sys.platform != "win32":
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    out: list[tuple[int, str]] = []

    def _proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW:
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = (buf.value or "").strip()
        if not title:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return True
        out.append((int(hwnd), title))
        return True

    cb = WNDENUMPROC(_proc)
    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: x[1].lower())
    return out


class WindowPickerDialog(QDialog):
    def __init__(self, parent, windows: list[tuple[int, str]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("공유할 창 선택")
        self.resize(480, 420)
        self._hwnd: int | None = None

        lst = QListWidget(self)
        lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lst.setAlternatingRowColors(True)
        for hwnd, title in windows:
            it = QListWidgetItem(title)
            it.setData(Qt.ItemDataRole.UserRole, hwnd)
            lst.addItem(it)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(lst)
        lay.addWidget(buttons)

        self._list = lst
        if lst.count() > 0:
            lst.setCurrentRow(0)

    def _on_ok(self) -> None:
        it = self._list.currentItem()
        if it is None:
            self.reject()
            return
        v = it.data(Qt.ItemDataRole.UserRole)
        self._hwnd = int(v) if v is not None else None
        if self._hwnd is None:
            self.reject()
            return
        self.accept()

    def selected_hwnd(self) -> int | None:
        return self._hwnd
