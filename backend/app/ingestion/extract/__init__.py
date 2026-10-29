"""Real text extraction. §7.1: real for TXT, DOCX and the PDF text layer."""

from app.ingestion.extract.docx_parser import DocxParser
from app.ingestion.extract.pdf import PdfParser, has_no_text_layer
from app.ingestion.extract.txt import TxtParser

PARSERS = (PdfParser(), DocxParser(), TxtParser())


def parser_for(ext: str) -> PdfParser | DocxParser | TxtParser | None:
    for p in PARSERS:
        if p.supports(ext):
            return p
    return None


__all__ = ["PARSERS", "DocxParser", "PdfParser", "TxtParser", "has_no_text_layer", "parser_for"]
