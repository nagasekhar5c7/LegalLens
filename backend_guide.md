# Backend Guide — Legal Lens

FastAPI + SQLite backend. Runs at **http://127.0.0.1:8000** (API docs at `/docs`).

---

## 1. Software needed

| Software | Why | Install (macOS) | Install (Windows) |
|---|---|---|---|
| **VS Code** | The editor | [code.visualstudio.com](https://code.visualstudio.com) | same |
| **Git** | Clone the repo | `xcode-select --install` | [git-scm.com](https://git-scm.com) |
| **Python 3.12+** | The runtime | comes with uv (below) | comes with uv (below) |
| **uv** | Package + venv manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| **make** *(optional)* | Shortcut commands | already on macOS | use the raw `uv` commands instead |

Check it worked:

```bash
uv --version
git --version
```

### A coding agent (pick one)

You will need one of these in VS Code:

- **Claude Code** — `npm install -g @anthropic-ai/claude-code`, then run `claude` in the project folder (or install the VS Code extension)
- **GitHub Copilot** — VS Code Extensions panel → search "GitHub Copilot" → Install → sign in
- **OpenAI Codex** — `npm install -g @openai/codex`, then run `codex`

### Useful VS Code extensions

`Python` · `Pylance` · `Ruff` · `Even Better TOML`

---

## 2. About the virtual environment

A **virtual environment (venv)** is a private folder of Python packages for *this project only*, so
projects don't break each other.

**You do not create it manually here.** `uv` does it for you — it makes a `.venv/` folder inside
`backend/` the first time you run `uv sync`, and `uv run` automatically uses it.

```bash
uv sync        # creates .venv/ + installs exactly the versions in uv.lock
uv run <cmd>   # runs <cmd> inside .venv — no "activate" needed
```

If you *want* to activate it manually (so plain `python` points at it):

```bash
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows
deactivate                     # to exit
```

In VS Code: `Cmd/Ctrl + Shift + P` → **Python: Select Interpreter** → pick `backend/.venv`.

---

## 3. Running the backend

Open a terminal in the project, then:

```bash
cd backend
```

**Step 1 — install dependencies**

```bash
uv sync --extra dev
```

> `--extra dev` also installs pytest, ruff and mypy. Plain `uv sync` is enough just to run the app.

**Step 2 — set up the database + demo data** (first time only)

```bash
uv run alembic upgrade head     # create the database tables
uv run python -m app.seed       # create the demo user + sample documents
```

**Step 3 — start the server**

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/docs** to see the API.

### Login credentials (printed by the seed step)

```
dana@example.com / counsel-demo-2026
```

### The shortcut: `make`

Same three steps, if you have `make`:

```bash
make install     # uv sync --extra dev
make seed        # migrate + seed
make dev         # start with auto-reload
make help        # list every command
```

---

## 4. Other commands

```bash
make test        # run the tests          → uv run pytest
make lint        # check code style       → uv run ruff check app tests scripts
make fmt         # auto-fix formatting    → uv run ruff format app tests scripts
make typecheck   # type checking          → uv run mypy app
make check       # everything CI runs (lint + types + tests)
make reset       # wipe the database and re-seed — destructive
make openapi     # regenerate openapi.json after changing a route
```

---

## 5. Configuration (optional)

Everything has a default, so the app runs with no setup. To change something:

```bash
cp .env.example .env
```

Common ones: `LL_PORT`, `LL_CORS_ORIGINS`, `LL_DATABASE_URL`.
Secrets (`LL_BLOB_KEY`, `LL_SECRET_KEY`) are auto-generated in dev — only required in production.

---

## 6. Tips & common problems

- **Keep this terminal running.** The backend must stay up while you use the frontend. Use a second
  terminal tab for the frontend.
- **Stop the server** with `Ctrl + C`.
- `--reload` means the server restarts automatically when you save a Python file.
- **`uv: command not found`** → close and reopen the terminal after installing uv.
- **Port 8000 already in use** → `lsof -ti:8000 | xargs kill -9` (macOS), or run on another port.
- **Weird database errors** → `make reset` gives you a clean database with fresh demo data.
- **Never commit** `.env`, `.venv/`, `legallens.db` or the `data/` folder — they're already in
  `.gitignore`.
- Add a package with `uv add <name>` (not `pip install`) so `pyproject.toml` and `uv.lock` stay in sync.

---

## 7. Run everything with Docker instead

If you'd rather not install Python at all — needs [Docker Desktop](https://docker.com/products/docker-desktop):

```bash
docker compose up --build      # from the project root, starts backend + frontend
```

Then open **http://localhost:5173**.
