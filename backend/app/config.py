"""Settings. Everything configurable is here, prefixed LL_, documented in .env.example."""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "test", "production"] = "dev"

    # --- transport -------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- persistence -----------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'legallens.db'}"
    data_dir: Path = REPO_ROOT / "data"

    # --- secrets ---------------------------------------------------------
    # NFR-9: AES-256-GCM over blobs and extracted text. 32 bytes, hex-encoded.
    # Generate with:  python -c "import secrets; print(secrets.token_hex(32))"
    blob_key: str = ""
    # Signs short-lived read URLs and session cookies.
    secret_key: str = ""

    # --- sessions (FR-27) ------------------------------------------------
    session_ttl_days: int = 14
    session_cookie_name: str = "ll_session"
    cookie_secure: bool = False  # True behind TLS in staging/production

    # --- limits (NFR-4, FR-2) --------------------------------------------
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    max_documents: int = 50
    accepted_extensions: list[str] = Field(default_factory=lambda: [".pdf", ".docx", ".txt"])

    # --- rate limits (middleware chain) ----------------------------------
    rate_limit_turns_per_min: int = 20
    rate_limit_uploads_per_min: int = 30
    rate_limit_reads_per_min: int = 600
    login_attempts_per_min: int = 10

    # --- ingestion (NFR-5) -----------------------------------------------
    ingest_concurrency: int = 3
    ingest_max_attempts: int = 3
    ingest_visibility_timeout_s: int = 300
    ocr_confidence_floor: float = 0.50
    text_layer_chars_per_page_floor: int = 120

    # --- ingestion: OCR (DR-1) -------------------------------------------
    #: auto | tesseract | rapidocr | stub. See `app/ingestion/ocr/__init__.py`
    #: for the full argument; the short version is that `uv sync` with no extras
    #: must still produce a startable backend, and that backend cannot do OCR.
    #: `auto` prefers tesseract (per-word confidence, which is what the DR-1
    #: floor actually needs) over rapidocr (no system binary) over the stub,
    #: and records which one ran on every page rather than assuming.
    ocr_engine: Literal["auto", "tesseract", "rapidocr", "stub"] = "auto"
    #: Tesseract's own floor for 10-12pt body text, which is what a contract is
    #: set in. 150 measurably loses small print; 600 quadruples the cost and
    #: buys nothing at this type size.
    ocr_dpi: int = 300
    #: Tesseract language data to load. `eng+spa` for a bilingual corpus — each
    #: extra language costs recognition time, so it is not a free switch.
    ocr_languages: str = "eng"
    #: Store a rendered image per page, encrypted, for the viewer to show.
    #: Scanned pages only by default: a PDF with a text layer is rendered
    #: client-side by PDF.js, so rasterising it server-side would be pure cost
    #: and 1-2 MB of ciphertext per page.
    page_images: Literal["scanned", "all", "off"] = "scanned"

    # --- ingestion: embedding and vector index ---------------------------
    #: auto | qdrant | stub. `auto` uses the real indexer when the retrieval
    #: extra is installed and the stub otherwise, reporting which on the
    #: document. `qdrant` is returned even when unavailable, so a deployment
    #: that means to index finds out by seeing the reason rather than by
    #: silently not indexing.
    indexer: Literal["auto", "qdrant", "stub"] = "auto"
    #: Texts per embedding call. bge-m3 pads a batch to its longest member, so a
    #: large batch mixing a short child with a 12,000-character parent wastes
    #: most of the padded width.
    embed_batch_size: int = 16

    # --- generation ------------------------------------------------------
    generation_budget_s: int = 60
    #: Multiplies every artificial delay in the fixture engine. 1.0 keeps the
    #: realistic ~600ms TTFT the streaming UI was built against; tests set it
    #: near zero so the suite is not dominated by deliberate sleeps.
    stream_delay_factor: float = 1.0
    sse_keepalive_s: int = 15

    # --- dev affordances -------------------------------------------------
    seed_email: str = "dana@example.com"
    seed_password: str = "counsel-demo-2026"  # noqa: S105 — dev seed only
    workspace_name: str = "Dana's workspace"

    @model_validator(mode="after")
    def _fill_dev_secrets(self) -> Settings:
        """Unset secrets get a *stable* dev key, persisted under `data_dir`.

        A per-process random key would make every blob written by the previous
        process undecryptable — so `make seed` then `make dev` would serve
        "authentication failed" for every page of every seeded document. The key
        file is gitignored and only ever used outside production, which
        `require_production_secrets` enforces.
        """
        if not self.blob_key:
            self.blob_key = _dev_secret(self.data_dir, "blob_key")
        if not self.secret_key:
            self.secret_key = _dev_secret(self.data_dir, "secret_key")
        return self

    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def scenarios_enabled(self) -> bool:
        """X-LegalLens-Scenario is a dev affordance and never ships enabled."""
        return self.env != "production"

    def require_production_secrets(self) -> None:
        import os

        if self.env != "production":
            return
        missing = [n for n in ("LL_BLOB_KEY", "LL_SECRET_KEY") if not os.environ.get(n)]
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} must be set in production — an ephemeral key would "
                "make every blob written by the previous process unreadable."
            )


DEV_KEYFILE = ".dev-secrets.json"


def _dev_secret(data_dir: Path, name: str) -> str:
    """A stable per-checkout dev secret. Created on first use, reused after."""
    path = data_dir / DEV_KEYFILE
    try:
        existing = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}

    if name not in existing:
        existing[name] = secrets.token_hex(32)
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, indent=2))
            path.chmod(0o600)
        except OSError:
            # Read-only filesystem: fall back to an ephemeral key rather than
            # refusing to start. Blobs from a previous run will be unreadable,
            # which is correct — the key really is gone.
            return existing[name]
    return str(existing[name])


@lru_cache
def get_settings() -> Settings:
    return Settings()
