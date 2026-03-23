"""순환 import 없이 메인 앱 창인지 판별."""


def is_app_main_window(w) -> bool:
    return getattr(w, "live2d_view", None) is not None and getattr(
        w, "vtuber_manager", None
    ) is not None
