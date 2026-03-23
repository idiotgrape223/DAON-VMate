"""LLM 멀티모달 첨부(이미지·텍스트·PDF 파일)."""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Any

# OpenAI·로컬 VLM 일반적 한도 참고
_MAX_ATTACHMENTS = 8
_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_MAX_TEXT_BYTES = 512 * 1024
# OpenAI 파일 입력: 파일당 최대 50MB (문서 기준)
_MAX_PDF_BYTES = 50 * 1024 * 1024

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml"}
_IMAGE_MIME_PREFIX = "image/"

# 일부 환경에서 image/jpg 등 비표준 MIME이 나오면 API/모델이 이미지를 무시하는 경우가 있음
_MIME_NORMALIZE = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/x-ms-bmp": "image/bmp",
}


def _normalize_image_mime(mime: str) -> str:
    m = (mime or "").strip().lower()
    return _MIME_NORMALIZE.get(m, m or "image/png")


def _is_svg_attachment(att: LLMMediaAttachment) -> bool:
    m = (att.mime_type or "").lower()
    return m in ("image/svg+xml", "text/svg+xml") or att.original_name.lower().endswith(
        ".svg"
    )


def _is_pdf_attachment(att: LLMMediaAttachment) -> bool:
    if (att.mime_type or "").lower() == "application/pdf":
        return True
    return att.original_name.lower().endswith(".pdf")


def _pdf_extract_text_truncated(raw: bytes, max_bytes: int = _MAX_TEXT_BYTES) -> str:
    """Ollama 등 PDF 바이너리를 보낼 수 없을 때 본문 추출용."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception:
        return ""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n…(텍스트 잘림)…"


@dataclass
class LLMMediaAttachment:
    mime_type: str
    raw_bytes: bytes
    original_name: str = ""

    def is_image(self) -> bool:
        return (self.mime_type or "").startswith(_IMAGE_MIME_PREFIX)


def load_attachment_from_path(path: str) -> tuple[LLMMediaAttachment | None, str | None]:
    """
    파일을 읽어 첨부 객체로 만듭니다.
    반환: (attachment, error_message) — 오류 시 (None, "이유").
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, str(e)

    ext = os.path.splitext(path)[1].lower()
    mime, _ = mimetypes.guess_type(path)
    if not mime or mime == "application/octet-stream":
        if ext in _TEXT_SUFFIXES:
            mime = "text/plain"
        elif ext in (".jpg", ".jpeg", ".jfif", ".jpe"):
            mime = "image/jpeg"
        elif ext == ".png":
            mime = "image/png"
        elif ext == ".gif":
            mime = "image/gif"
        elif ext == ".webp":
            mime = "image/webp"
        elif ext == ".bmp":
            mime = "image/bmp"
        elif ext in (".heic", ".heif"):
            mime = "image/heic"
        elif ext == ".pdf":
            mime = "application/pdf"
        elif not mime:
            mime = "application/octet-stream"

    if mime.startswith(_IMAGE_MIME_PREFIX):
        if size > _MAX_IMAGE_BYTES:
            return None, f"이미지가 너무 큽니다 (최대 {_MAX_IMAGE_BYTES // (1024*1024)}MB)."
    elif mime == "application/pdf" or ext == ".pdf":
        if size > _MAX_PDF_BYTES:
            return None, f"PDF가 너무 큽니다 (최대 {_MAX_PDF_BYTES // (1024*1024)}MB)."
    elif mime.startswith("text/") or ext in _TEXT_SUFFIXES:
        if size > _MAX_TEXT_BYTES:
            return None, "텍스트 파일이 너무 큽니다."
    else:
        return None, (
            "지원 형식: 이미지(png, jpg, gif, webp), 텍스트(txt, md 등), PDF(.pdf)."
        )

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, str(e)

    name = os.path.basename(path)
    return LLMMediaAttachment(mime_type=mime, raw_bytes=raw, original_name=name), None


def merge_text_file_into_prompt(user_text: str, att: LLMMediaAttachment) -> str:
    """텍스트 첨부를 사용자 문장에 합칩니다."""
    try:
        body = att.raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        body = att.raw_bytes.decode("latin-1", errors="replace")
    header = f"--- 파일: {att.original_name} ---\n"
    footer = f"\n--- 끝: {att.original_name} ---"
    block = header + body.strip() + footer
    if user_text.strip():
        return user_text.strip() + "\n\n" + block
    return block


def _is_text_attachment(att: LLMMediaAttachment) -> bool:
    if att.is_image():
        return False
    ext = os.path.splitext(att.original_name)[1].lower()
    return att.mime_type.startswith("text/") or ext in _TEXT_SUFFIXES


def split_attachments(
    attachments: list[LLMMediaAttachment],
) -> tuple[str, list[LLMMediaAttachment]]:
    """
    텍스트류는 하나의 추가 프롬프트 문자열로 합치고, 이미지만 리스트로 남깁니다.
    (레거시·히스토리용) 첨부 목록 순서는 유지합니다.
    반환: (merged_extra_text, image_attachments)
    """
    extra_parts: list[str] = []
    images: list[LLMMediaAttachment] = []
    for a in attachments:
        if a.is_image() and not _is_svg_attachment(a):
            images.append(a)
        elif _is_text_attachment(a):
            extra_parts.append(merge_text_file_into_prompt("", a).strip())
        else:
            continue
    return "\n\n".join(extra_parts), images


def build_openai_user_message(
    user_text: str, attachments: list[LLMMediaAttachment]
) -> dict[str, Any]:
    """
    OpenAI Chat Completions 호환 user 메시지.
    첨부 순서대로 텍스트·이미지(image_url)·PDF(type=file, base64 file_data)를 넣습니다.
    """
    ut = (user_text or "").strip()
    parts: list[dict[str, Any]] = []

    if ut:
        parts.append({"type": "text", "text": ut})

    for att in attachments:
        if att.is_image():
            if _is_svg_attachment(att):
                try:
                    svg = att.raw_bytes.decode("utf-8", errors="replace")
                    cap = 12000
                    snippet = svg if len(svg) <= cap else svg[:cap] + "\n…(잘림)…"
                    parts.append(
                        {
                            "type": "text",
                            "text": f"--- SVG 파일: {att.original_name} ---\n{snippet}",
                        }
                    )
                except Exception:
                    pass
                continue
            mime = _normalize_image_mime(att.mime_type)
            b64 = base64.standard_b64encode(att.raw_bytes).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": "auto",
                    },
                }
            )
        elif _is_text_attachment(att):
            block = merge_text_file_into_prompt("", att).strip()
            if block:
                parts.append({"type": "text", "text": block})
        elif _is_pdf_attachment(att):
            b64 = base64.standard_b64encode(att.raw_bytes).decode("ascii")
            parts.append(
                {
                    "type": "file",
                    "file": {
                        "filename": att.original_name or "document.pdf",
                        "file_data": b64,
                    },
                }
            )

    parts = [
        p
        for p in parts
        if (p.get("type") == "text" and (p.get("text") or "").strip())
        or p.get("type") == "image_url"
        or p.get("type") == "file"
    ]

    if not parts:
        return {"role": "user", "content": ""}

    if all(p.get("type") == "image_url" for p in parts):
        parts.insert(
            0,
            {
                "type": "text",
                "text": "첨부 이미지를 보고 질문에 답해 주세요.",
            },
        )
    elif all(p.get("type") == "file" for p in parts):
        parts.insert(
            0,
            {
                "type": "text",
                "text": "첨부 PDF(파일)를 참고하여 질문에 답해 주세요.",
            },
        )
    elif not any(p.get("type") == "text" for p in parts) and any(
        p.get("type") in ("image_url", "file") for p in parts
    ):
        parts.insert(
            0,
            {
                "type": "text",
                "text": "첨부 이미지·파일을 참고하여 질문에 답해 주세요.",
            },
        )

    return {"role": "user", "content": parts}


def format_user_text_for_history(
    user_text: str, attachments: list[LLMMediaAttachment]
) -> str:
    """채팅 히스토리에는 텍스트+첨부 요약만 남깁니다(바이너리 미포함)."""
    t = (user_text or "").strip()
    if not attachments:
        return t
    names = ", ".join(a.original_name for a in attachments if a.original_name)
    if not names:
        names = f"{len(attachments)}개"
    note = f"[첨부 {len(attachments)}개: {names}]"
    if t:
        return f"{t}\n{note}"
    return note


def build_ollama_user_message(
    user_text: str, attachments: list[LLMMediaAttachment]
) -> dict[str, Any]:
    """
    Ollama /api/chat vision: content + images(base64) 배열.
    이미지 순서는 첨부 순서와 동일. 텍스트는 사용자 입력 뒤에 텍스트 첨부를 첨부 순서대로 이어 붙임.
    """
    ut = (user_text or "").strip()
    text_blocks: list[str] = []
    if ut:
        text_blocks.append(ut)

    image_b64_list: list[str] = []

    for att in attachments:
        if att.is_image():
            if _is_svg_attachment(att):
                try:
                    svg = att.raw_bytes.decode("utf-8", errors="replace")
                    cap = 12000
                    snippet = svg if len(svg) <= cap else svg[:cap] + "\n…(잘림)…"
                    text_blocks.append(
                        f"--- SVG 파일: {att.original_name} ---\n{snippet}"
                    )
                except Exception:
                    pass
                continue
            image_b64_list.append(
                base64.standard_b64encode(att.raw_bytes).decode("ascii")
            )
        elif _is_text_attachment(att):
            block = merge_text_file_into_prompt("", att).strip()
            if block:
                text_blocks.append(block)
        elif _is_pdf_attachment(att):
            extracted = _pdf_extract_text_truncated(att.raw_bytes)
            header = f"--- PDF: {att.original_name} (추출 텍스트) ---\n"
            if extracted:
                text_blocks.append(header + extracted)
            else:
                text_blocks.append(
                    header
                    + "(본문을 추출하지 못했습니다. 스캔 PDF이거나 pypdf 미설치일 수 있습니다.)"
                )

    text = "\n\n".join(t for t in text_blocks if t)
    if not text and not image_b64_list:
        return {"role": "user", "content": ""}
    if not text:
        text = "첨부 이미지를 보고 질문에 답해 주세요."
    if len(image_b64_list) > 1:
        text = (
            "첨부된 이미지는 선택한 순서와 배열 순서가 같습니다.\n\n" + text
        )
    msg: dict[str, Any] = {"role": "user", "content": text}
    if image_b64_list:
        msg["images"] = image_b64_list
    return msg


# UI·엔진에서 동일 상한 사용
MAX_LLM_ATTACHMENTS = _MAX_ATTACHMENTS
