from __future__ import annotations

# 문장/절 단위로 끊기 위한 구분자 (한·중·일·영 공통 일부)
_SPLIT_CHARS = frozenset(".!?。！？…\n")


class TextBatchAccumulator:
    """
    스트리밍 델타를 모아서 배치 문자열 목록으로보냅니다.
    - min_chars 이상일 때 문장부호/줄바꿈에서 끊음
    - 너무 길면 max_chars 근처(가능하면 마지막 공백)에서 끊음
    """

    def __init__(self, min_chars: int = 8, max_chars: int = 56) -> None:
        self.min_chars = max(2, int(min_chars))
        self.max_chars = max(self.min_chars + 1, int(max_chars))
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            taken = self._take_one_batch()
            if taken is None:
                break
            out.append(taken)
        return out

    def flush(self) -> list[str]:
        if not self._buf.strip():
            self._buf = ""
            return []
        chunk = self._buf
        self._buf = ""
        return [chunk]

    def _take_one_batch(self) -> str | None:
        if not self._buf:
            return None
        n = len(self._buf)
        if n >= self.max_chars:
            window = self._buf[: self.max_chars]
            cut = self.max_chars
            sp = window.rfind(" ")
            if sp >= self.min_chars:
                cut = sp + 1
            piece = self._buf[:cut]
            self._buf = self._buf[cut:]
            return piece

        if n < self.min_chars:
            return None

        for i, ch in enumerate(self._buf):
            if ch in _SPLIT_CHARS:
                end = i + 1
                if ch == ".":
                    while end < n and self._buf[end] == ".":
                        end += 1
                piece = self._buf[:end]
                self._buf = self._buf[end:]
                return piece

        return None
