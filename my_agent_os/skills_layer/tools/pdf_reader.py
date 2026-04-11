"""
PDF Reader — extract text and metadata from PDF files.

Params:
  path       (str) — file path (absolute or workspace-relative)
  max_pages  (int) — max pages to extract, default 50
  page       (int) — extract a single page, 1-indexed (optional)

Requires: pypdf   (pip install pypdf)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from my_agent_os.skills_layer.base import Skill, skill_err, skill_ok
from my_agent_os.skills_layer.tools import register

_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE_DIR", Path.home() / "AgentOS" / "workspace"))


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    for candidate in [(_WORKSPACE / path_str).resolve(), Path.cwd() / path_str]:
        if candidate.exists():
            return candidate
    return p  # let caller handle "not found"


@register
class PDFReader(Skill):
    name = "pdf_reader"
    description = (
        "Extract text content from a PDF file. "
        "Params: path (str, required), max_pages (int, optional, default 50), "
        "page (int, optional — extract a specific page, 1-indexed). "
        "Requires pypdf (pip install pypdf)."
    )

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = (params.get("path") or "").strip()
        if not path_str:
            return skill_err("MISSING_PARAM", "Parameter 'path' is required.")

        file_path = _resolve(path_str)
        if not file_path.exists():
            return skill_err("FILE_NOT_FOUND", f"File not found: {path_str}")

        if file_path.suffix.lower() != ".pdf":
            return skill_err(
                "UNSUPPORTED_FORMAT",
                f"Expected a .pdf file, got '{file_path.suffix}'. Use image_analyzer for images.",
            )

        max_pages = max(1, int(params.get("max_pages", 50)))
        specific_page = params.get("page")

        try:
            return self._extract(file_path, max_pages, specific_page)
        except ImportError:
            return skill_err(
                "DEPENDENCY_MISSING",
                "pypdf is not installed. Run: pip install pypdf",
                retryable=False,
            )
        except Exception as exc:
            return skill_err("EXTRACTION_FAILED", f"PDF extraction error: {exc}")

    @staticmethod
    def _extract(path: Path, max_pages: int, specific_page: object) -> dict:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        total = len(reader.pages)

        if specific_page is not None:
            idx = int(specific_page) - 1
            if idx < 0 or idx >= total:
                return skill_err(
                    "PAGE_OUT_OF_RANGE",
                    f"Page {specific_page} is out of range (1–{total}).",
                )
            pages_idx = [idx]
        else:
            pages_idx = list(range(min(total, max_pages)))

        chunks: list[str] = []
        for i in pages_idx:
            text = (reader.pages[i].extract_text() or "").strip()
            chunks.append(f"--- Page {i + 1} ---\n{text}")

        full_text = "\n\n".join(chunks)
        return skill_ok(
            f"Extracted {len(pages_idx)} of {total} page(s) from '{path.name}'.",
            output=full_text,
            data={
                "file": path.name,
                "total_pages": total,
                "pages_extracted": len(pages_idx),
                "char_count": len(full_text),
            },
        )
