"""Stub port implementations for M3. Each one is replaced at M4."""

from app.ingestion.stub.chunker import SectionAwareChunker
from app.ingestion.stub.classifier import StubClassifier
from app.ingestion.stub.indexer import StubIndexer
from app.ingestion.stub.layout_qa import StubLayoutQa
from app.ingestion.stub.ocr import StubOcr

__all__ = [
    "SectionAwareChunker",
    "StubClassifier",
    "StubIndexer",
    "StubLayoutQa",
    "StubOcr",
]
