# 리팩토링 백로그 (후속 작업)

1~3단계(데드 코드 제거, 메뉴 DRY, `logging` 통일)는 적용 완료. 아래는 **동작 변경 범위가 큰** 분할 후보로, 별도 PR·마일스톤에서 진행하는 것을 권장합니다.

## `ui/settings_dialog.py` (~1050줄)

- 탭별 `_build_*_tab` 및 관련 위젯 생성을 `ui/settings_tabs/` 등 하위 모듈로 이동.
- `SettingsDialog`는 탭 조립, `exec`, 저장/로드 오케스트레이션만 유지.
- 한 탭씩 옮기고 회귀 확인하는 방식이 안전함.

## `app/windows/main_window.py` (~990줄)

- 데스크톱 펫 모드: `enter_desktop_pet_mode`, `exit_desktop_pet_mode`, 플로팅 채팅, 창 플래그.
- 화면 공유: `_populate_screen_share_menu`, 캡처 타이머, 첨부 생성.
- Live2D 동기화: `_sync_live2d_overlays`, `reload_live2d`, GL 리사이즈 헬퍼.

위 블록을 `main_window_pet.py`, `main_window_screen_share.py` 등으로 **믹스인 또는 순수 함수 모듈**로 분리할 수 있음. 순환 import 방지를 위해 `identity`·`load_config` 경계를 명확히 할 것.

## `core/llm_engine.py`

- 스트리밍 HTTP, MCP 도구 라운드, 시스템 프롬프트 조립을 서브모듈로 쪼개기 전에 **단위 테스트 또는 최소 통합 시나리오**를 두는 편이 안전함.

## 로그 레벨

- `main.py`의 `basicConfig(level=logging.INFO)`. 개발 시 `logging.getLogger("app.widgets.live2d_widget").setLevel(logging.DEBUG)` 등으로 모듈 단위 상향 가능.
