from __future__ import annotations

import glob
import logging
import math
import os
import shutil
import time

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QOpenGLFunctions,
    QPalette,
    QScreen,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import live2d.v3 as live2d

# 프로세스당 한 번만 (미리보기용 두 번째 QOpenGLWidget이 init 을 다시 호출하면 전역 상태가 깨질 수 있음)
_live2d_library_inited = False

logger = logging.getLogger(__name__)

from app.styles import LIVE2D_CONTEXT_MENU_QSS, LIVE2D_CONTEXT_SUBMENU_QSS
from app.windows.identity import is_app_main_window
from core.emotion_apply_debug_log import log_emotion_apply_step
from core.live2d_emotion_tags import build_emo_map_from_profile, extract_emotion_indices
from core.model_profile import load_motion_catalog_for_folder, tap_motion_for_folder

def live2d_gl_surface_format() -> QSurfaceFormat:
    """
    Live2D용 GL 서피스: 알파 비트를 defaultFormat 병합 없이 고정.
    멀티샘플은 일부 환경에서 데스크톱 합성 시 알파가 무시되는 경우가 있어 0으로 둡니다.
    """
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    try:
        fmt.setSamples(0)
    except Exception:
        pass
    try:
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
    except Exception:
        pass
    try:
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    except Exception:
        pass
    return fmt



class Live2DWidget(QOpenGLWidget):
    """
    웹 브라우저(WebEngine) 없이 파이썬 네이티브 OpenGL로 Live2D를 직접 렌더링하는 위젯
    """

    def __init__(
        self,
        parent=None,
        *,
        wheel_zoom_without_main_window: bool = False,
        embed_preview_controls: bool = False,
    ):
        super().__init__(parent)
        self.model = None
        self.model_path = ""
        self.scale = 0.25
        # 설정 미리보기 등: 메인 창 밖에서 휠 스케일·좌클릭 드래그 패닝(탭 모션 없음)
        self._embed_preview_controls = bool(embed_preview_controls)
        self._wheel_zoom_without_main_window = bool(
            wheel_zoom_without_main_window
        ) or self._embed_preview_controls
        self._embed_skip_paint = False
        self._gl_ready = False
        self._transparent_clear = False
        self._pet_press_global: QPoint | None = None
        self._pet_drag_offset: QPoint | None = None
        self._pet_dragging = False
        # 채팅 모드: Live2D 뷰 패닝 (SetOffset, 휠 스케일과 함께 기준 위치 조정)
        self._camera_offset_x = 0.0
        self._camera_offset_y = 0.0
        self._cam_pan_pressed = False
        self._cam_pan_last: tuple[float, float] = (0.0, 0.0)
        self._pet_shift_moves_window = False
        self._gl_funcs: QOpenGLFunctions | None = None
        # 왼쪽 버튼: 짧은 클릭 vs 카메라 패닝 드래그 구분 (클릭 시 탭 모션)
        self._cam_click_start: tuple[float, float] | None = None
        self._cam_drag_moved = False
        self._CAM_TAP_DRAG_THRESHOLD_PX = 12.0
        self._live2d_folder_name = ""
        self._last_applied_expression_index: int | None = None

        # 알파 버퍼는 앱 기본 포맷과 동일하게 명시(초기 컨텍스트부터 알파 경로)
        self.setFormat(live2d_gl_surface_format())
        # 내부 FBO→위젯 복사 시 알파를 살리려면 프리멀 ARGB (Qt 문서·포럼 권장)
        try:
            self.setTextureFormat(QImage.Format.Format_ARGB32_Premultiplied)
        except Exception:
            pass
        try:
            self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        except Exception:
            pass
        self.setMinimumSize(400, 300)

        # 창을 나중에 투명 모드로 바꿀 때와 달리, 시작부터 레이어드·알파 합성 경로로 잡히게 함
        # (예전 설정의 투명 배경을 켠 채로 실행하던 것과 동일한 Live2D 위젯 초기화)
        self._init_alpha_compositing_widget_state()

        # OpenGL 상수 (Qt에 GL_* 바인딩 없음)
        self._GL_BLEND = 3042
        self._GL_ONE = 1
        self._GL_SRC_ALPHA = 770
        self._GL_ONE_MINUS_SRC_ALPHA = 771

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(16)

        # 마우스 시선/고개 추적 (정규화 -1~1, 부드럽게 보간)
        self._target_look_x = 0.0
        self._target_look_y = 0.0
        self._smooth_look_x = 0.0
        self._smooth_look_y = 0.0
        self._look_smoothing = 0.22
        self._max_angle_x = 30.0
        self._max_angle_y = 25.0
        self._max_eye = 1.0

        # 답변 말하기 립싱크 (텍스트 길이 기반 지속 시간 + 파형)
        self._lip_sync_active = False
        self._lip_sync_t0 = 0.0
        self._lip_sync_duration = 0.0
        self._lip_decay = 0.22

    def _init_alpha_compositing_widget_state(self) -> None:
        """QOpenGLWidget을 첫 표시 전부터 알파 합성에 맞는 속성으로 둡니다(토글 시 깨짐 완화)."""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background-color: rgba(0,0,0,0);")

    def begin_lip_sync_for_text(
        self, text: str, duration_sec: float | None = None
    ) -> None:
        """모델이 말하는 것처럼 입을 움직입니다. duration_sec가 있으면 TTS 길이에 맞춥니다."""
        raw = (text or "").strip()
        if duration_sec is not None and duration_sec > 0:
            self._lip_sync_duration = max(0.5, min(120.0, float(duration_sec)))
        elif raw.startswith("[LLM]") or raw.startswith("[오류]"):
            self._lip_sync_duration = max(0.7, min(3.0, len(raw) * 0.04))
        else:
            self._lip_sync_duration = max(1.2, min(55.0, len(raw) * 0.086))
        self._lip_sync_t0 = time.monotonic()
        self._lip_sync_active = True

    def stop_lip_sync(self) -> None:
        """재생/스트림 중단 시 입 움직임을 즉시 끕니다."""
        self._lip_sync_active = False
        self._safe_set_param("ParamMouthOpenY", 0.0)
        self._safe_set_param("ParamMouthForm", 0.0)

    def _apply_lip_sync(self) -> None:
        if not self.model or not self._lip_sync_active:
            return
        now = time.monotonic()
        elapsed = now - self._lip_sync_t0
        total = self._lip_sync_duration + self._lip_decay
        if elapsed >= total:
            self._safe_set_param("ParamMouthOpenY", 0.0)
            self._safe_set_param("ParamMouthForm", 0.0)
            self._lip_sync_active = False
            return

        if elapsed < self._lip_sync_duration:
            env = 1.0
            if elapsed < 0.07:
                env = elapsed / 0.07
        else:
            rel = elapsed - self._lip_sync_duration
            env = max(0.0, 1.0 - rel / self._lip_decay)

        t = elapsed
        wave = (
            math.sin(t * 13.2) * 0.36
            + math.sin(t * 7.0 + 1.1) * 0.26
            + math.sin(t * 20.5) * 0.11
        )
        open_y = (0.4 + wave) * env
        open_y = max(0.0, min(1.0, open_y))
        form = math.sin(t * 8.8 + 0.4) * 0.5 * env
        form = max(-1.0, min(1.0, form))
        self._safe_set_param("ParamMouthOpenY", open_y)
        self._safe_set_param("ParamMouthForm", form)

    def set_pointer_target(self, x: float, y: float):
        """위젯 로컬 좌표 (0~width, 0~height). 화면 중앙 기준 -1~1로 변환."""
        w, h = max(self.width(), 1), max(self.height(), 1)
        self._target_look_x = (x / w - 0.5) * 2.0
        self._target_look_y = (0.5 - y / h) * 2.0

    def release_pointer_target(self):
        self._target_look_x = 0.0
        self._target_look_y = 0.0

    def _safe_set_param(self, param_id: str, value: float, weight: float = 1.0):
        if not self.model:
            return
        try:
            self.model.SetParameterValue(param_id, float(value), weight)
        except Exception:
            pass

    def _apply_mouse_look(self):
        s = self._look_smoothing
        self._smooth_look_x += (self._target_look_x - self._smooth_look_x) * s
        self._smooth_look_y += (self._target_look_y - self._smooth_look_y) * s

        lx = max(-1.0, min(1.0, self._smooth_look_x))
        ly = max(-1.0, min(1.0, self._smooth_look_y))

        ax = lx * self._max_angle_x
        ay = ly * self._max_angle_y
        ex = lx * self._max_eye
        ey = ly * self._max_eye
        az = lx * 12.0

        self._safe_set_param("ParamAngleX", ax)
        self._safe_set_param("ParamAngleY", ay)
        self._safe_set_param("ParamAngleZ", az)
        self._safe_set_param("ParamEyeBallX", ex)
        self._safe_set_param("ParamEyeBallY", ey)
        self._safe_set_param("ParamBodyAngleX", lx * 10.0)
        self._safe_set_param("ParamBodyAngleY", ly * 8.0)

    def initializeGL(self):
        global _live2d_library_inited
        if not _live2d_library_inited:
            live2d.init()
            _live2d_library_inited = True
        live2d.glInit()
        ctx = self.context()
        if ctx is not None:
            self._gl_funcs = QOpenGLFunctions(ctx)
            self._gl_funcs.initializeOpenGLFunctions()
        self._gl_ready = True
        if self.model_path and os.path.exists(self.model_path):
            self._load_model_gl()

    def _load_model_gl(self):
        """OpenGL 컨텍스트가 유효할 때만 호출"""
        path = os.path.abspath(self.model_path)
        if not os.path.exists(path):
            logger.warning("모델 파일을 찾을 수 없습니다: %s", path)
            return
        try:
            self.makeCurrent()
            if self.model:
                del self.model
                self.model = None
            self._camera_offset_x = 0.0
            self._camera_offset_y = 0.0
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(path)
            self.model.Resize(max(self.width(), 1), max(self.height(), 1))
            self._sync_model_scale_offset()
            logger.debug("네이티브 모델 로드 성공: %s", path)
        except Exception as e:
            logger.error("모델 로드 중 오류: %s", e)
        finally:
            self.doneCurrent()

    def set_transparent_clear(self, enabled: bool) -> None:
        self._transparent_clear = bool(enabled)
        self.update()

    def recreate_live2d_gl_for_alpha_mode(self) -> None:
        """
        창 플래그·투명 합성 전환 뒤 GL 서피스가 바뀌면 Cubism 셰이더/상태가 맞지 않을 수 있어
        glRelease -> glInit 후 모델을 다시 로드합니다.
        """
        if not self._gl_ready or not self.isValid():
            return
        path = (self.model_path or "").strip()
        if not path or not os.path.exists(path):
            self.update()
            return
        abspath = os.path.abspath(path)
        self.makeCurrent()
        try:
            live2d.glRelease()
            live2d.glInit()
            if self.model:
                try:
                    del self.model
                except Exception:
                    pass
                self.model = None
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(abspath)
            self.model.Resize(max(self.width(), 1), max(self.height(), 1))
            self._sync_model_scale_offset()
            self._last_applied_expression_index = None
        except Exception:
            pass
        finally:
            self.doneCurrent()
        self.update()

    def load_model(self, path, scale=0.25, folder_name: str | None = None):
        self.model_path = path
        self.scale = scale
        self._last_applied_expression_index = None
        if folder_name is not None and str(folder_name).strip():
            self._live2d_folder_name = str(folder_name).strip()
        elif path:
            norm = os.path.abspath(path).replace("\\", "/")
            low = norm.lower()
            for marker in ("assets/live2d-models/", "/live2d-models/"):
                idx = low.find(marker)
                if idx >= 0:
                    rest = norm[idx + len(marker) :].lstrip("/")
                    seg = rest.split("/", 1)[0] if rest else ""
                    if seg:
                        self._live2d_folder_name = seg
                    break
        if not path or not os.path.exists(path):
            logger.warning("모델 경로가 없거나 파일이 없습니다: %s", path)
            self.update()
            return
        if self._gl_ready and self.isValid():
            self._load_model_gl()
        else:
            self.update()

    def resizeGL(self, w, h):
        if self._embed_skip_paint:
            return
        if self.model and w > 0 and h > 0:
            self.model.Resize(w, h)

    def paintGL(self):
        if self._embed_skip_paint:
            f = self._gl_funcs
            if f is not None:
                f.glDisable(self._GL_BLEND)
            live2d.clearBuffer(0.12, 0.12, 0.14, 1.0)
            return
        f = self._gl_funcs
        if self._transparent_clear:
            if f is not None:
                f.glColorMask(True, True, True, True)
                f.glDisable(self._GL_BLEND)
            live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
            if f is not None:
                f.glEnable(self._GL_BLEND)
                # 일반 알파 블렌드(프리멀 조합은 일부 환경에서 전체가 검은 직사각형으로 보임)
                f.glBlendFunc(self._GL_SRC_ALPHA, self._GL_ONE_MINUS_SRC_ALPHA)
        else:
            if f is not None:
                f.glDisable(self._GL_BLEND)
            live2d.clearBuffer(0.12, 0.12, 0.14, 1.0)
        if self.model:
            self.model.Update()
            self._apply_mouse_look()
            self._apply_lip_sync()
            self._sync_model_scale_offset()
            self.model.Draw()

    def start_motion(self, motion_group, index=0):
        if not self.model:
            return
        ctx = False
        try:
            ctx = bool(self._gl_ready) and self.isValid()
        except Exception:
            pass
        try:
            if ctx:
                self.makeCurrent()
            try:
                self.model.StartMotion(
                    motion_group, index, live2d.MotionPriority.FORCE
                )
            finally:
                if ctx:
                    self.doneCurrent()
        except Exception:
            pass
        self.update()

    def play_tap_interaction(self) -> None:
        """model_dict.json + 현재 model_folder 기준 탭/반응 모션."""
        g, idx = tap_motion_for_folder(self._live2d_folder_name)
        self.start_motion(g, idx)

    def _apply_menu_chrome(self, menu: QMenu, qss: str) -> None:
        """Live2D 컨텍스트 메뉴·서브메뉴 공통: 투명 합성 회피 + 밝은 팔레트."""
        menu.setStyleSheet(qss)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        menu.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        menu.setAutoFillBackground(True)
        pal = menu.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.Text, QColor("#000000"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
        menu.setPalette(pal)

    def _style_live2d_context_menu(self, menu: QMenu) -> None:
        self._apply_menu_chrome(menu, LIVE2D_CONTEXT_MENU_QSS)

    def _style_live2d_submenu(self, sm: QMenu) -> None:
        self._apply_menu_chrome(sm, LIVE2D_CONTEXT_SUBMENU_QSS)

    def _expression_ids_for_menu(self) -> list[str]:
        if not self.model:
            return []
        try:
            if self._gl_ready and self.isValid():
                self.makeCurrent()
                try:
                    raw = self.model.GetExpressionIds()
                    return list(raw) if raw else []
                finally:
                    self.doneCurrent()
        except Exception:
            pass
        try:
            raw = self.model.GetExpressionIds()
            return list(raw) if raw else []
        except Exception:
            return []

    def _apply_expression_for_menu(self, index: int) -> None:
        """우클릭 메뉴에서 표정 적용. index < 0 이면 표정만 초기화."""
        if not self.model:
            return
        ctx = False
        try:
            ctx = bool(self._gl_ready) and self.isValid()
        except Exception:
            pass
        try:
            if ctx:
                self.makeCurrent()
            try:
                if index < 0:
                    self.model.ResetExpressions()
                    self._last_applied_expression_index = None
                else:
                    ok = self._set_expression_by_index(index)
                    if ok:
                        self._last_applied_expression_index = index
            finally:
                if ctx:
                    self.doneCurrent()
        except Exception:
            pass
        self.update()

    @staticmethod
    def _context_menu_payload(data):
        if not isinstance(data, tuple) or len(data) < 1:
            return None
        if data[0] == "m" and len(data) == 3:
            g, idx = data[1], data[2]
            if isinstance(g, str) and isinstance(idx, int):
                return ("m", g, idx)
            if isinstance(g, str) and isinstance(idx, float):
                return ("m", g, int(idx))
        if data[0] == "e" and len(data) == 2 and isinstance(data[1], int):
            return ("e", data[1])
        return None

    def _deferred_show_context_menu(self, global_pos: QPoint) -> None:
        if not self.model or self._embed_preview_controls:
            return
        if not self._main_window_live2d_drag():
            return
        self._show_live2d_context_menu(global_pos)

    def _show_live2d_context_menu(self, global_pos: QPoint) -> None:
        """메인 Live2D: 우클릭으로 표정(전체)·모션·펫 전용 항목."""
        if not self.model or self._embed_preview_controls:
            return
        if not self._main_window_live2d_drag():
            return
        win = self.window()
        menu_parent = win if is_app_main_window(win) else self
        menu = QMenu(menu_parent)
        menu.setObjectName("live2dContextMenu")
        self._style_live2d_context_menu(menu)

        sub_expr = menu.addMenu("표정")
        self._style_live2d_submenu(sub_expr)
        act_expr_reset = sub_expr.addAction("표정 끄기 (초기화)")
        act_expr_reset.setData(("e", -1))
        ids = self._expression_ids_for_menu()
        if ids:
            sub_expr.addSeparator()
            for i, eid in enumerate(ids):
                label = str(eid).strip() or f"#{i}"
                a = sub_expr.addAction(f"{i} — {label}")
                a.setData(("e", i))
        else:
            sub_expr.addSeparator()
            na = sub_expr.addAction("(등록된 표정 없음)")
            na.setEnabled(False)

        menu.addSeparator()
        act_tap = menu.addAction("기본 탭 반응 (모션)")
        menu.addSeparator()
        catalog = load_motion_catalog_for_folder(self._live2d_folder_name)
        for group in sorted(catalog.keys(), key=lambda k: (k == "", k.lower())):
            n = int(catalog[group])
            if n <= 0:
                continue
            disp_group = "(기본 풀)" if group == "" else group
            if n == 1:
                a = menu.addAction(disp_group)
                a.setData(("m", group, 0))
            else:
                sub = menu.addMenu(disp_group)
                self._style_live2d_submenu(sub)
                for i in range(n):
                    a = sub.addAction(f"모션 {i}")
                    a.setData(("m", group, i))

        act_chat = None
        act_exit = None
        if self._main_is_desktop_pet() and is_app_main_window(win):
            menu.addSeparator()
            placed = bool(getattr(win, "_pet_floating_chat_placed", False))
            act_chat = menu.addAction(
                "플로팅 채팅 숨기기" if placed else "플로팅 채팅 보이기"
            )
            act_exit = menu.addAction("채팅 모드로 복귀")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == act_tap:
            self.play_tap_interaction()
            return
        if act_chat is not None and chosen == act_chat and is_app_main_window(win):
            if getattr(win, "_pet_floating_chat_placed", False):
                win.hide_pet_floating_chat()
            else:
                lp = self.mapFromGlobal(global_pos)
                win.show_pet_floating_chat_at(int(lp.x()), int(lp.y()))
            return
        if act_exit is not None and chosen == act_exit and is_app_main_window(win):
            win.exit_desktop_pet_mode()
            return
        payload = self._context_menu_payload(chosen.data())
        if payload is None:
            return
        kind = payload[0]
        if kind == "e":
            self._apply_expression_for_menu(int(payload[1]))
        elif kind == "m":
            self.start_motion(payload[1], payload[2])

    def clear_emotion_dedup(self) -> None:
        """새 assistant 턴마다 호출: 이전 답과 같은 표정 인덱스여도 다시 적용되게 함."""
        self._last_applied_expression_index = None

    def _set_expression_by_index(self, index: int) -> bool:
        if not self.model or index < 0:
            return False
        try:
            ids = self.model.GetExpressionIds()
        except Exception:
            return False
        if not ids or index >= len(ids):
            return False
        try:
            eid = ids[index]
            self.model.ResetExpressions()
            self.model.SetExpression(eid)
            return True
        except Exception:
            return False

    def set_expression_preview_index(self, index: int) -> bool:
        """설정 미리보기 등: 표정 인덱스만 적용하고 즉시 다시 그립니다."""
        ok = self._set_expression_by_index(int(index))
        if ok:
            self.update()
        return ok

    def _resolve_app_config(self):
        """MainWindow.config: window() 실패·중첩 시 부모 체인으로 탐색."""
        w = self.window()
        if w is not None:
            cfg = getattr(w, "config", None)
            if isinstance(cfg, dict):
                return cfg
        p = self.parentWidget()
        for _ in range(32):
            if p is None:
                break
            cfg = getattr(p, "config", None)
            if isinstance(cfg, dict):
                return cfg
            p = p.parentWidget()
        return None

    def apply_emotion_for_assistant_text(self, plain: str) -> None:
        """Open-LLM-VTuber 방식: 답변 속 [joy] 등 태그 → emotionMap 정수 = 표정(Expression) 인덱스."""
        cfg = self._resolve_app_config()
        if not isinstance(cfg, dict):
            return
        live = cfg.get("live2d") or {}
        if not bool(live.get("auto_emotion_from_assistant", True)):
            return
        llm_on = bool((cfg.get("llm") or {}).get("use_emotion_tags", True))
        if not llm_on:
            return
        t = (plain or "").strip()
        if not t or t.startswith("[LLM]") or t.startswith("[오류]"):
            return

        from core.live2d_emotion_tags import (
            build_emo_map_from_profile,
            extract_emotion_indices,
        )
        from core.model_profile import effective_profile_for_folder

        # 실제 로드된 모델 폴더 우선 (설정과 불일치 시에도 emotionMap·표정 인덱스가 맞게)
        folder_key = (self._live2d_folder_name or "").strip()
        if not folder_key:
            folder_key = str(live.get("model_folder", "") or "").strip()
        prof = effective_profile_for_folder(folder_key)
        em = build_emo_map_from_profile(prof)
        if not em:
            log_emotion_apply_step(
                cfg,
                folder_key,
                "no_emo_map",
                profile=prof.get("name") if prof else None,
            )
            return
        idxs = extract_emotion_indices(t, em)
        chosen: int | None = None
        if idxs:
            chosen = int(idxs[-1])
        elif em.get("neutral") is not None:
            chosen = int(em["neutral"])
        if chosen is None:
            log_emotion_apply_step(
                cfg,
                folder_key,
                "no_chosen",
                profile=prof.get("name") if prof else None,
                emo_keys=sorted(em.keys()),
                idxs=idxs,
                text_snip=t[:200],
            )
            return
        try:
            ids = self.model.GetExpressionIds() if self.model else None
        except Exception:
            ids = None
        n_expr = len(ids) if ids else 0
        expr_sample = list(ids[:12]) if ids else []
        chosen_before_oob = chosen
        if n_expr <= 0:
            log_emotion_apply_step(
                cfg,
                folder_key,
                "no_expressions",
                profile=prof.get("name") if prof else None,
                emo_map=em,
                idxs=idxs,
                chosen_from_map=chosen_before_oob,
                text_snip=t[:200],
            )
            return
        if chosen < 0 or chosen >= n_expr:
            nu = em.get("neutral")
            if nu is None:
                log_emotion_apply_step(
                    cfg,
                    folder_key,
                    "oob_no_neutral",
                    profile=prof.get("name") if prof else None,
                    emo_map=em,
                    idxs=idxs,
                    chosen=chosen_before_oob,
                    n_expr=n_expr,
                    expr_sample=expr_sample,
                    text_snip=t[:200],
                )
                return
            ni = int(nu)
            if 0 <= ni < n_expr:
                chosen = ni
            else:
                log_emotion_apply_step(
                    cfg,
                    folder_key,
                    "oob_neutral_invalid",
                    profile=prof.get("name") if prof else None,
                    emo_map=em,
                    chosen=chosen_before_oob,
                    neutral=ni,
                    n_expr=n_expr,
                    text_snip=t[:200],
                )
                return
        dedup_skip = self._last_applied_expression_index == chosen
        set_ok = False
        if not dedup_skip:
            set_ok = self._set_expression_by_index(chosen)
            if set_ok:
                self._last_applied_expression_index = chosen
        log_emotion_apply_step(
            cfg,
            folder_key,
            "apply",
            profile=prof.get("name") if prof else None,
            emo_map=em,
            idxs=idxs,
            chosen_from_tags_last=int(idxs[-1]) if idxs else None,
            chosen_applied=chosen,
            oob_fallback_from=chosen_before_oob
            if chosen_before_oob != chosen
            else None,
            n_expr=n_expr,
            expr_sample=expr_sample,
            dedup_skip=dedup_skip,
            set_ok=set_ok,
            text_snip=t[:200],
        )

    def _sync_model_scale_offset(self) -> None:
        """스케일·뷰 오프셋을 엔진에 반영."""
        if not self.model:
            return
        try:
            self.model.SetScale(float(self.scale))
            self.model.SetOffset(
                float(self._camera_offset_x), float(self._camera_offset_y)
            )
        except Exception:
            pass

    def _pixel_delta_to_scene(self, dx_px: float, dy_px: float) -> tuple[float, float]:
        """live2d-py v3 MatrixManager::UpdateScreenToScene 와 동일 순서(Scale 후 Translate)."""
        w, h = max(self.width(), 1), max(self.height(), 1)
        from live2d.v2.framework.matrix.l2d_matrix44 import L2DMatrix44

        m = L2DMatrix44()
        m.identity()
        ratio = float(w) / float(h)
        left = -ratio
        right = ratio
        if w > h:
            sw = abs(right - left)
            m.multScale(sw / float(w), -sw / float(w))
        else:
            sh = abs(1.0 - (-1.0))
            m.multScale(sh / float(h), -sh / float(h))
        m.multTranslate(-w / 2.0, -h / 2.0)
        sx = m.transformX(dx_px) - m.transformX(0.0)
        sy = m.transformY(dy_px) - m.transformY(0.0)
        return sx, sy

    def _main_is_desktop_pet(self) -> bool:
        w = self.window()
        return is_app_main_window(w) and getattr(w, "_desktop_pet_mode", False)

    def _main_window_live2d_drag(self) -> bool:
        """메인 창 Live2D: 일반·펫 모드 모두 왼쪽 드래그로 뷰 패닝, 펫+Shift는 창 이동."""
        return is_app_main_window(self.window())

    def _apply_camera_pan_delta(self, dx_px: float, dy_px: float) -> None:
        if not self.model or (dx_px == 0.0 and dy_px == 0.0):
            return
        dsx, dsy = self._pixel_delta_to_scene(dx_px, dy_px)
        s = max(float(self.scale), 0.001)
        self._camera_offset_x += dsx / s
        self._camera_offset_y += dsy / s
        try:
            self.model.SetOffset(
                float(self._camera_offset_x), float(self._camera_offset_y)
            )
        except Exception:
            pass
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            (
                self._main_window_live2d_drag()
                or self._embed_preview_controls
            )
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if self._main_window_live2d_drag() and self._main_is_desktop_pet():
                self._pet_press_global = event.globalPosition().toPoint()
                self._pet_shift_moves_window = bool(
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                )
                if self._pet_shift_moves_window:
                    win = self.window()
                    self._pet_drag_offset = (
                        event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                    )
                    self._pet_dragging = False
                    self._cam_pan_pressed = False
                else:
                    self._pet_drag_offset = None
                    self._pet_dragging = False
                    self._cam_pan_pressed = True
                    pos = event.position()
                    lx, ly = float(pos.x()), float(pos.y())
                    self._cam_pan_last = (lx, ly)
                    self._cam_click_start = (lx, ly)
                    self._cam_drag_moved = False
            else:
                self._pet_shift_moves_window = False
                self._cam_pan_pressed = True
                pos = event.position()
                lx, ly = float(pos.x()), float(pos.y())
                self._cam_pan_last = (lx, ly)
                self._cam_click_start = (lx, ly)
                self._cam_drag_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._main_is_desktop_pet()
            and self._main_window_live2d_drag()
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._pet_press_global is not None
            and self._pet_shift_moves_window
            and self._pet_drag_offset is not None
        ):
            g = event.globalPosition().toPoint()
            if not self._pet_dragging:
                dx = abs(g.x() - self._pet_press_global.x())
                dy = abs(g.y() - self._pet_press_global.y())
                if dx + dy > 6:
                    self._pet_dragging = True
            if self._pet_dragging:
                self.window().move(g - self._pet_drag_offset)
            event.accept()
            return
        if (
            (
                self._main_window_live2d_drag()
                or self._embed_preview_controls
            )
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._cam_pan_pressed
            and self.model
            and not (
                self._main_is_desktop_pet()
                and self._pet_shift_moves_window
            )
        ):
            pos = event.position()
            lx, ly = float(pos.x()), float(pos.y())
            if self._cam_click_start is not None:
                csx, csy = self._cam_click_start
                thr = self._CAM_TAP_DRAG_THRESHOLD_PX
                if max(abs(lx - csx), abs(ly - csy)) > thr:
                    self._cam_drag_moved = True
            plx, ply = self._cam_pan_last
            dx_px, dy_px = lx - plx, ly - ply
            self._cam_pan_last = (lx, ly)
            self._apply_camera_pan_delta(dx_px, dy_px)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            (
                self._main_window_live2d_drag()
                or self._embed_preview_controls
            )
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if self._main_window_live2d_drag() and self._main_is_desktop_pet():
                g0 = self._pet_press_global
                if g0 is not None:
                    g1 = event.globalPosition().toPoint()
                    moved = abs(g1.x() - g0.x()) + abs(g1.y() - g0.y()) > 6
                    if not moved and not self._pet_shift_moves_window:
                        self.play_tap_interaction()
                        w = self.window()
                        if is_app_main_window(w):
                            if getattr(w, "_pet_floating_chat_placed", False):
                                w.hide_pet_floating_chat()
                            else:
                                lp = event.position()
                                w.show_pet_floating_chat_at(int(lp.x()), int(lp.y()))
                self._pet_press_global = None
                self._pet_drag_offset = None
                self._pet_dragging = False
                self._pet_shift_moves_window = False
                self._cam_pan_pressed = False
                self._cam_click_start = None
                self._cam_drag_moved = False
            else:
                if (
                    self.model
                    and not self._cam_drag_moved
                    and self._cam_click_start is not None
                    and not self._embed_preview_controls
                ):
                    self.play_tap_interaction()
                self._cam_pan_pressed = False
                self._cam_click_start = None
                self._cam_drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if (
            self.model
            and (
                self._main_window_live2d_drag()
                or self._wheel_zoom_without_main_window
            )
        ):
            dy = event.angleDelta().y()
            if dy != 0:
                step = 0.09 if dy > 0 else -0.09
                self.scale = max(0.05, min(3.0, self.scale * (1.0 + step)))
                self._sync_model_scale_offset()
                self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._embed_preview_controls:
            super().contextMenuEvent(event)
            return
        if self.model and self._main_window_live2d_drag():
            event.accept()
            # QContextMenuEvent: PySide6 에서는 globalPos() (QMouseEvent 와 달리 globalPosition 없음)
            gp = event.globalPos()
            QTimer.singleShot(
                0, lambda p=QPoint(gp): self._deferred_show_context_menu(p)
            )
            return
        super().contextMenuEvent(event)

    def prepare_for_embedded_teardown(self) -> None:
        """
        설정 미리보기 등 embed_preview_controls 위젯 전용.
        자식 QOpenGLWidget은 closeEvent가 오지 않을 수 있어, 부모 다이얼로그 닫기 전에
        현재 GL 컨텍스트에서 모델을 해제하고 타이머를 멈춥니다(메인 Live2D와 충돌 방지).
        """
        if not self._embed_preview_controls:
            return
        self._embed_skip_paint = True
        try:
            self.hide()
        except Exception:
            pass
        try:
            self.timer.stop()
        except Exception:
            pass
        if not self._gl_ready:
            self.model = None
            return
        try:
            if not self.isValid():
                self.model = None
                return
        except Exception:
            self.model = None
            return
        self.makeCurrent()
        try:
            if self.model is not None:
                try:
                    del self.model
                except Exception:
                    pass
                self.model = None
            try:
                f = self._gl_funcs
                if f is not None:
                    f.glFinish()
            except Exception:
                pass
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass

    def closeEvent(self, event):
        if self._embed_preview_controls:
            super().closeEvent(event)
            return
        # 앱 종료 시 Live2D 엔진 정리 (메인 창 단일 위젯만 dispose)
        if self.model:
            del self.model
        live2d.dispose()
        super().closeEvent(event)

