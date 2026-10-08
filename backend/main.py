"""LegalLens M0 — API contract stub.

Five endpoints from the BRD (§7), returning realistic canned responses,
including a canned refusal and a canned error. No AI, no storage yet.

Run:  uv run uvicorn backend.main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
import asyncio
import json
import uuid
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="LegalLens API", version="0.0.1-m0")

# React+Vite dev server runs on 5173
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------- models
class Citation(BaseModel):
    document_id: str
    page: int
    start: int
    end: int
    excerpt: str


class Document(BaseModel):
    id: str
    filename: str
    pages: int
    status: Literal["processing", "ready", "failed"]


class AskRequest(BaseModel):
    question: str
    scope: Literal["workspace", "document"] = "workspace"
    document_id: str | None = None
    stream: bool = False


class AskResponse(BaseModel):
    answer: str | None
    refused: bool
    citations: list[Citation]


class SummarizeRequest(BaseModel):
    document_id: str


class SummarizeResponse(BaseModel):
    document_type: Literal["contract", "judicial_opinion", "pleading"]
    fields: dict[str, str]
    citations: list[Citation]


class DraftRequest(BaseModel):
    kind: Literal["memo", "case_brief"]
    position: str
    document_ids: list[str] = []


class DraftResponse(BaseModel):
    draft: str
    is_draft: bool = True
    citations: list[Citation]


class Feedback(BaseModel):
    message_id: str
    rating: Literal["up", "down"]
    comment: str | None = None


# ---------------------------------------------------------------- canned data
DOCS: dict[str, Document] = {
    "doc-msa": Document(id="doc-msa", filename="Acme_MSA_2025.pdf", pages=24, status="ready"),
}

CITE = Citation(
    document_id="doc-msa", page=11, start=1204, end=1388,
    excerpt="Either party may terminate this Agreement upon sixty (60) days' "
            "prior written notice to the other party.",
)

# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok", "milestone": "M0"}


@app.get("/documents", response_model=list[Document])
def list_documents():
    return list(DOCS.values())


@app.post("/documents", response_model=Document, status_code=201)
async def upload_document(file: UploadFile = File(...)):
    allowed = (".pdf", ".docx", ".txt")
    if not file.filename.lower().endswith(allowed):
        # canned error
        raise HTTPException(415, f"Unsupported file type. Allowed: {', '.join(allowed)}")
    doc = Document(id=f"doc-{uuid.uuid4().hex[:8]}", filename=file.filename,
                   pages=0, status="processing")
    DOCS[doc.id] = doc
    return doc


@app.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str):
    if DOCS.pop(doc_id, None) is None:
        raise HTTPException(404, "Document not found")


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    # canned refusal: anything mentioning "weather" is "not in your documents"
    if "weather" in req.question.lower():
        resp = AskResponse(answer=None, refused=True, citations=[])
    else:
        resp = AskResponse(
            answer="Either party can terminate on 60 days' written notice [1].",
            refused=False, citations=[CITE])

    if not req.stream:
        return resp

    async def events():
        for token in (resp.answer or "Not answerable from your documents.").split(" "):
            yield f"event: token\ndata: {json.dumps(token + ' ')}\n\n"
            await asyncio.sleep(0.05)
        yield f"event: done\ndata: {resp.model_dump_json()}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    if req.document_id not in DOCS:
        raise HTTPException(404, "Document not found")
    return SummarizeResponse(
        document_type="contract",
        fields={
            "parties": "Acme Corp (Provider); Globex LLC (Customer)",
            "term": "3 years from 2025-01-01, auto-renews annually",
            "termination": "60 days' written notice, either party",
            "governing_law": "Texas",
        },
        citations=[CITE],
    )


@app.post("/draft", response_model=DraftResponse)
def draft(req: DraftRequest):
    return DraftResponse(
        draft=f"DRAFT — UNREVIEWED AI OUTPUT\n\n{req.kind.upper()}\n\n"
              f"Position: {req.position}\n\nUnder §14.2 of the MSA, either party "
              f"may terminate on sixty days' notice [1]...",
        citations=[CITE],
    )


@app.post("/feedback", status_code=202)
def feedback(fb: Feedback):
    return {"received": True, "message_id": fb.message_id}
