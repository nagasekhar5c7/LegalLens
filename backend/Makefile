# Legal Lens backend. `make help` lists everything.
.DEFAULT_GOAL := help
.PHONY: help install install-all dev run migrate migration seed reset test test-fast lint typecheck fmt check openapi router ingest ingest-check index index-stats ask clean

UV ?= uv

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies, including dev and AI extras
	$(UV) sync --extra dev --extra ai

install-all: ## Also install retrieval and OCR. ~2.5 GB of wheels; tesseract is a separate brew install
	$(UV) sync --extra dev --extra ai --extra retrieval --extra ocr

dev: migrate ## Run the API with reload (http://127.0.0.1:8000)
	$(UV) run uvicorn app.start:app --reload --host 127.0.0.1 --port 8000

run: migrate ## Run the API without reload
	$(UV) run uvicorn app.start:app --host 0.0.0.0 --port 8000

migrate: ## Apply migrations
	$(UV) run alembic upgrade head

migration: ## Autogenerate a migration — make migration m="add x"
	@test -n "$(m)" || (echo 'usage: make migration m="what changed"' && exit 1)
	$(UV) run alembic revision --autogenerate -m "$(m)"

seed: migrate ## Create the dev user and the fixture corpus
	$(UV) run python -m app.seed

reset: ## Drop the database and blobs, then re-seed. Destructive.
	rm -f legallens.db legallens.db-wal legallens.db-shm
	rm -rf data/blobs data/work
	$(MAKE) seed

test: ## Run the test suite
	$(UV) run pytest

test-fast: ## Run the test suite, stopping at the first failure
	$(UV) run pytest -x -q

lint: ## Check formatting and lint rules
	$(UV) run ruff check app tests scripts
	$(UV) run ruff format --check app tests scripts

fmt: ## Apply formatting and safe lint fixes
	$(UV) run ruff check --fix app tests scripts
	$(UV) run ruff format app tests scripts

typecheck: ## Run mypy in strict mode
	$(UV) run mypy app

router: ## Score the intent router against the real model — needs ANTHROPIC_API_KEY, costs money
	$(UV) run python scripts/try_intent_router.py $(q)

ingest-check: ## Report which OCR engine and indexer would actually run
	$(UV) run python scripts/try_ingest.py --check

ingest: ## Run the real ingestion pipeline over one file — make ingest f=nda.pdf
	@test -n "$(f)" || (echo 'usage: make ingest f=<file> [q="a question"]' && exit 1)
	$(UV) run python scripts/try_ingest.py $(if $(q),--query "$(q)",) "$(f)"

index: ## Index documents into Qdrant — make index src=../docs
	@test -n "$(src)" || (echo 'usage: make index src=<folder-or-file>' && exit 1)
	$(UV) run python scripts/index_corpus.py $(src)

index-stats: ## Show what is currently indexed
	$(UV) run python scripts/index_corpus.py --stats

ask: ## Ask the indexed corpus — make ask q="what is the liability cap?". Costs money.
	@test -n "$(q)" || (echo 'usage: make ask q="your question"' && exit 1)
	$(UV) run python scripts/try_ask.py "$(q)"

openapi: ## Regenerate openapi.json
	$(UV) run python scripts/export_openapi.py

check: lint typecheck ## Everything CI runs
	$(UV) run python scripts/export_openapi.py --check
	$(UV) run pytest -q

clean: ## Remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
