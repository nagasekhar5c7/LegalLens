"""⊕ Not in the architecture's endpoint list. FR-2 requires the client-side
rejection message to name the real limit, and the client should not hold a
second copy of what the server enforces."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.schemas.documents import ServerConfig

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=ServerConfig)
async def config() -> ServerConfig:
    s = get_settings()
    return ServerConfig(
        max_bytes=s.max_upload_bytes,
        max_pages=s.max_pages,
        max_documents=s.max_documents,
        accepted_extensions=s.accepted_extensions,
        scenarios_enabled=s.scenarios_enabled,
        generation_budget_s=s.generation_budget_s,
    )
