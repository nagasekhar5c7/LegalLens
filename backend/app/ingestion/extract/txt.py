"""Plain text. Paginated at a fixed character budget so `page` means something
in a citation — a TXT file has no pages of its own."""

from __future__ import annotations

from pathlib import Path

from app.ingestion.ports import ParsedPage, ParseResult

CHARS_PER_PAGE = 3000


class TxtParser:
    name = "txt/builtin"

    def supports(self, ext: str) -> bool:
        return ext == ".txt"

    def parse(self, path: str, *, max_pages: int) -> ParseResult:
        raw = Path(path).read_bytes()
        # Legal exports are frequently cp1252 or UTF-16 from Word. Try in order
        # rather than mangling the text with errors="replace" on the first miss.
        text = ""
        for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if not text:
            text = raw.decode("utf-8", errors="replace")

        pages: list[ParsedPage] = []
        paragraphs = text.replace("\r\n", "\n").split("\n\n")
        buf: list[str] = []
        size = 0
        for para in paragraphs:
            if size + len(para) > CHARS_PER_PAGE and buf:
                pages.append(
                    ParsedPage(page_no=len(pages) + 1, text="\n\n".join(buf), engine=self.name)
                )
                buf, size = [], 0
                if len(pages) >= max_pages:
                    break
            buf.append(para)
            size += len(para) + 2
        if buf and len(pages) < max_pages:
            pages.append(
                ParsedPage(page_no=len(pages) + 1, text="\n\n".join(buf), engine=self.name)
            )
        if not pages:
            pages = [ParsedPage(page_no=1, text="", engine=self.name)]

        return ParseResult(pages=pages, scanned=False, engine=self.name)
