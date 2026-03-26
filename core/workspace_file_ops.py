"""
워크스페이스 파일 에이전트용 경로 샌드박스 및 I/O.

허용 범위:
- 논리적으로는 ``workspace/`` 이하만 (텍스트·바이너리 생성·수정·삭제·목록)
- ``workspace/`` 접두어 없이 ``notes/a.txt`` 처럼만 오면 자동으로 ``workspace/`` 아래로 간주합니다.

텍스트(.py, .cpp, .md, .json 등)는 UTF-8로 읽고 씁니다.
엑셀·PDF·이미지 등은 읽기 시 ``[DAON_FILE_AGENT_BASE64]`` 한 줄 다음 base64 본문으로 돌려주며,
쓰기 시 ``content_base64=True`` 로 동일 형식(또는 순수 base64 문자열)을 넘기면 됩니다.
``.xlsx`` 경로에 UTF-8 텍스트를 넘기면 내장 OOXML 작성기로 시트를 만듭니다(구분자 있으면 CSV, 없으면 줄마다 A열).

절대 경로, ``..`` 탈출, 위 경로 밖 접근은 거부합니다.
"""

from __future__ import annotations

import base64
import csv
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024

WORKSPACE_SEGMENT = "workspace"

# 읽기 시 확장자만으로도 이진으로 간주 (UTF-8 시도 생략)
_BINARY_SUFFIXES = frozenset(
    {
        ".xlsx",
        ".xls",
        ".xlsm",
        ".xlsb",
        ".ods",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".odt",
        ".odp",
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".zip",
        ".7z",
        ".rar",
        ".gz",
        ".tar",
        ".bz2",
        ".xz",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".obj",
        ".lib",
        ".a",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".webm",
        ".wav",
        ".flac",
        ".mkv",
        ".avi",
        ".mov",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".wasm",
        ".pak",
    }
)

FILE_AGENT_BASE64_MARKER = "[DAON_FILE_AGENT_BASE64]\n"

# OOXML/ODF 등 ZIP 시그니처(PK)로 시작해야 하는 확장자
_ZIP_MAGIC_SUFFIXES = frozenset(
    {
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".ods",
        ".docx",
        ".pptx",
        ".odt",
        ".odp",
        ".zip",
    }
)


class WorkspacePathError(ValueError):
    """허용되지 않은 경로."""


def normalize_relative(rel: str) -> str:
    s = (rel or "").strip().replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    s = s.lstrip("/")
    while s.startswith("./"):
        s = s[2:]
    return s.strip()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_allowed_path(repo_root: str, relative_path: str) -> Path:
    rel = normalize_relative(relative_path)
    if not rel:
        raise WorkspacePathError("경로가 비어 있습니다.")
    if rel.startswith("/") or (len(rel) > 2 and rel[1] == ":"):
        raise WorkspacePathError("절대 경로는 사용할 수 없습니다.")
    parts = [p for p in rel.split("/") if p != "."]
    if not parts or ".." in parts:
        raise WorkspacePathError("경로에 .. 또는 잘못된 구성 요소가 있습니다.")

    root = Path(repo_root).resolve()

    if parts[0] != WORKSPACE_SEGMENT:
        parts = [WORKSPACE_SEGMENT] + parts

    base = (root / WORKSPACE_SEGMENT).resolve()
    target = base.joinpath(*parts[1:]).resolve() if len(parts) > 1 else base
    if not _is_under(target, base) and target != base:
        raise WorkspacePathError("workspace 폴더 범위를 벗어났습니다.")
    return target


def ensure_workspace_dir(repo_root: str) -> Path:
    p = Path(repo_root).resolve() / WORKSPACE_SEGMENT
    p.mkdir(parents=True, exist_ok=True)
    return p


def _is_probably_binary_path(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_SUFFIXES


def read_text_file(repo_root: str, relative_path: str) -> str:
    """UTF-8 텍스트로만 읽습니다."""
    path = resolve_allowed_path(repo_root, relative_path)
    if not path.is_file():
        raise WorkspacePathError(f"파일이 없거나 파일이 아닙니다: {relative_path}")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise WorkspacePathError(
            f"파일이 너무 큽니다 ({size} bytes). 상한 {MAX_READ_BYTES} bytes."
        )
    return path.read_text(encoding="utf-8", errors="replace")


def read_file(repo_root: str, relative_path: str) -> str:
    """
    텍스트는 UTF-8 문자열로, 그 외(확장자·NUL·디코딩 실패)는 base64 블록으로 반환합니다.
    """
    path = resolve_allowed_path(repo_root, relative_path)
    if not path.is_file():
        raise WorkspacePathError(f"파일이 없거나 파일이 아닙니다: {relative_path}")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise WorkspacePathError(
            f"파일이 너무 큽니다 ({size} bytes). 상한 {MAX_READ_BYTES} bytes."
        )

    data = path.read_bytes()
    if _is_probably_binary_path(path):
        return FILE_AGENT_BASE64_MARKER + base64.b64encode(data).decode("ascii")
    head = data[: min(len(data), 8192)]
    if b"\x00" in head:
        return FILE_AGENT_BASE64_MARKER + base64.b64encode(data).decode("ascii")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return FILE_AGENT_BASE64_MARKER + base64.b64encode(data).decode("ascii")


def _looks_like_only_base64_payload(s: str) -> bool:
    """공백 제거 후 base64 문자만으로 구성됐는지(모델이 플래그 없이 넘긴 경우 감지)."""
    p = "".join((s or "").split())
    if len(p) < 16:
        return False
    if len(p) % 4 == 1:
        return False
    for c in p:
        if c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
            return False
    return True


def _content_looks_like_base64_for_binary_write(content: str) -> bool:
    t = (content or "").strip()
    if not t:
        return False
    if t.startswith("[DAON_FILE_AGENT_BASE64]"):
        rest = t.split("\n", 1)[1].strip() if "\n" in t else ""
        return _looks_like_only_base64_payload(rest)
    return _looks_like_only_base64_payload(t)


_XLSX_CELL_MAX = 32767

_XLSX_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_XLSX_RELS_ROOT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_XLSX_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

_XLSX_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

_XLSX_STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>"""


def _xlsx_sanitize_cell(s: str) -> str:
    t = (s or "")[:_XLSX_CELL_MAX]
    out: list[str] = []
    for c in t:
        o = ord(c)
        if o < 32 and c not in "\t\n\r":
            continue
        if o in (0xFFFE, 0xFFFF):
            continue
        out.append(c)
    return "".join(out)


def _xlsx_col_name(col_idx: int) -> str:
    n = col_idx + 1
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_sheet_xml_from_rows(rows: list[list[str]]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for ri, row in enumerate(rows, start=1):
        cells: list[str] = []
        for ci, val in enumerate(row):
            ref = f"{_xlsx_col_name(ci)}{ri}"
            inner = escape(_xlsx_sanitize_cell(str(val)))
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{inner}</t></is></c>')
        parts.append(f'<row r="{ri}">{"".join(cells)}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _xlsx_bytes_from_rows_stdlib(rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _XLSX_CT)
        zf.writestr("_rels/.rels", _XLSX_RELS_ROOT)
        zf.writestr("xl/workbook.xml", _XLSX_WORKBOOK)
        zf.writestr("xl/_rels/workbook.xml.rels", _XLSX_WORKBOOK_RELS)
        zf.writestr("xl/styles.xml", _XLSX_STYLES)
        zf.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet_xml_from_rows(rows))
    return buf.getvalue()


def _rows_from_utf8_for_xlsx(text: str) -> list[list[str]] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        lines = [raw[:_XLSX_CELL_MAX]]
    looks_delimited = any("\t" in ln or "," in ln or ";" in ln for ln in lines[:10])
    if looks_delimited:
        sample = "\n".join(lines[: min(5, len(lines))])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        sio = io.StringIO("\n".join(raw.splitlines()))
        reader = csv.reader(sio, dialect)
        rows: list[list[str]] = []
        for row in reader:
            if any((c or "").strip() for c in row):
                rows.append([str(c)[:_XLSX_CELL_MAX] for c in row])
        if rows:
            return rows
        return [[ln[:_XLSX_CELL_MAX]] for ln in lines]
    return [[ln[:_XLSX_CELL_MAX]] for ln in lines]


def _try_build_xlsx_bytes_from_table_text(text: str) -> bytes | None:
    """
    UTF-8 텍스트를 표준 라이브러리만으로 .xlsx (OOXML) 바이트로 만듭니다.
    쉼표/탭 등이 있으면 CSV 로 파싱하고, 아니면 줄마다 단일 열(A열)에 넣습니다.
    """
    rows = _rows_from_utf8_for_xlsx(text)
    if not rows:
        return None
    return _xlsx_bytes_from_rows_stdlib(rows)


def _decode_base64_content(content: str) -> bytes:
    s = (content or "").strip()
    if s.startswith("[DAON_FILE_AGENT_BASE64]"):
        parts = s.split("\n", 1)
        s = parts[1].strip() if len(parts) > 1 else ""
    payload = "".join(s.split())
    if not payload:
        raise WorkspacePathError("base64 내용이 비어 있습니다.")
    try:
        return base64.b64decode(payload, validate=False)
    except Exception as e:
        raise WorkspacePathError(f"base64 디코딩 실패: {e}") from e


def write_file(
    repo_root: str,
    relative_path: str,
    content: str,
    *,
    content_base64: bool = False,
) -> str:
    """
    ``content_base64=False``: UTF-8 텍스트로 저장(바이너리 확장자는 아래 예외).
    ``content_base64=True``: 순수 base64 또는 read_file이 돌려준 ``[DAON_FILE_AGENT_BASE64]`` 블록.
    """
    path = resolve_allowed_path(repo_root, relative_path)
    binary_ext = _is_probably_binary_path(path)

    if content_base64:
        raw_b = _decode_base64_content(content)
    elif binary_ext:
        if _content_looks_like_base64_for_binary_write(content):
            raw_b = _decode_base64_content(content)
            suf = path.suffix.lower()
            if suf in _ZIP_MAGIC_SUFFIXES and len(raw_b) >= 4 and not raw_b.startswith(b"PK"):
                raise WorkspacePathError(
                    f"{suf} 파일은 ZIP(Office) 바이너리여야 합니다. "
                    "지금 넘긴 내용은 base64로 디코딩해도 유효한 엑셀/Office 파일이 아닙니다. "
                    "실제 .xlsx 바이너리를 base64로 인코딩해 content_base64=true 로 보내거나, "
                    "표를 텍스트로만 저장하려면 확장자를 .csv 또는 .txt 로 바꾸세요."
                )
        elif path.suffix.lower() == ".xlsx":
            xlsx_b = _try_build_xlsx_bytes_from_table_text(content)
            if xlsx_b is not None:
                raw_b = xlsx_b
            else:
                raise WorkspacePathError(
                    ".xlsx 에서 UTF-8 표 변환에 실패했습니다(내용이 비어 있음). "
                    "실제 엑셀 바이너리는 content_base64=true 로 전달하세요."
                )
        else:
            raise WorkspacePathError(
                f"{path.suffix} 등 바이너리 확장자에는 UTF-8 텍스트를 그대로 쓸 수 없습니다. "
                "content_base64=true 와 base64 본문(또는 workspace_read가 준 [DAON_FILE_AGENT_BASE64] 블록)을 사용하거나, "
                "텍스트 표만 필요하면 .csv / .txt 로 저장하세요."
            )
    else:
        raw_s = content if isinstance(content, str) else str(content)
        raw_b = raw_s.encode("utf-8")

    if len(raw_b) > MAX_WRITE_BYTES:
        raise WorkspacePathError(
            f"내용이 너무 큽니다 ({len(raw_b)} bytes). 상한 {MAX_WRITE_BYTES} bytes."
        )

    if _is_under(path, Path(repo_root).resolve() / WORKSPACE_SEGMENT):
        ensure_workspace_dir(repo_root)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_b)
    return f"저장 완료: {relative_path} ({len(raw_b)} bytes)"


def write_text_file(repo_root: str, relative_path: str, content: str) -> str:
    """UTF-8 텍스트 저장(기존 호환)."""
    return write_file(repo_root, relative_path, content, content_base64=False)


def delete_path(repo_root: str, relative_path: str) -> str:
    path = resolve_allowed_path(repo_root, relative_path)
    if not path.exists():
        raise WorkspacePathError(f"존재하지 않습니다: {relative_path}")
    if path.is_dir():
        raise WorkspacePathError("폴더 삭제는 지원하지 않습니다. 파일만 삭제할 수 있습니다.")
    path.unlink()
    return f"삭제 완료: {relative_path}"


def list_workspace_entries(repo_root: str, relative_dir: str = "") -> str:
    """``workspace`` 루트 또는 그 하위 폴더만 나열합니다. ``relative_dir``은 workspace 기준 하위 경로."""
    ensure_workspace_dir(repo_root)
    base = Path(repo_root).resolve() / WORKSPACE_SEGMENT
    rel = normalize_relative(relative_dir)
    if rel.startswith(WORKSPACE_SEGMENT + "/"):
        rel = rel[len(WORKSPACE_SEGMENT) + 1 :]
    elif rel == WORKSPACE_SEGMENT:
        rel = ""
    parts = [p for p in rel.split("/") if p and p != "."] if rel else []
    if ".." in parts:
        raise WorkspacePathError("경로에 .. 를 사용할 수 없습니다.")
    target = base.joinpath(*parts).resolve() if parts else base
    if not _is_under(target, base) and target != base:
        raise WorkspacePathError("workspace 폴더 범위를 벗어났습니다.")
    if not target.is_dir():
        raise WorkspacePathError(f"디렉터리가 아닙니다: {relative_dir or '.'}")
    names = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines: list[str] = []
    for p in names:
        kind = "dir" if p.is_dir() else "file"
        try:
            sz = p.stat().st_size if p.is_file() else 0
        except OSError:
            sz = -1
        if p.is_dir():
            lines.append(f"[{kind}] {p.name}/")
        else:
            lines.append(f"[{kind}] {p.name} ({sz} bytes)")
    if not lines:
        return "(비어 있음)"
    return "\n".join(lines)
