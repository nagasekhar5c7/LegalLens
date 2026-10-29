# Frontend Guide — Legal Lens

React 19 + TypeScript + Vite UI. Runs at **http://localhost:5173**.

---

## 1. Software needed

| Software | Why | Install (macOS) | Install (Windows) |
|---|---|---|---|
| **VS Code** | The editor | [code.visualstudio.com](https://code.visualstudio.com) | same |
| **Git** | Clone the repo | `xcode-select --install` | [git-scm.com](https://git-scm.com) |
| **Node.js 20+** (LTS) | Runs the dev server; `npm` comes with it | [nodejs.org](https://nodejs.org) or `brew install node` | [nodejs.org](https://nodejs.org) |

Check it worked:

```bash
node --version    # v20 or higher
npm --version
```

> **Tip:** if you juggle multiple projects later, install Node through **nvm**
> (`brew install nvm`) so you can switch versions per project. Not needed today.

### A coding agent (pick one)

- **Claude Code** — `npm install -g @anthropic-ai/claude-code`, then run `claude` in the project folder (or install the VS Code extension)
- **GitHub Copilot** — VS Code Extensions panel → search "GitHub Copilot" → Install → sign in
- **OpenAI Codex** — `npm install -g @openai/codex`, then run `codex`

### Useful VS Code extensions

`ESLint` · `Prettier` · `Tailwind CSS IntelliSense` · `ES7+ React snippets`

---

## 2. Start the backend first

The UI is useless without the API. In a **separate terminal tab**, follow
[backend_guide.md](backend_guide.md) — short version:

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Leave it running.

---

## 3. Running the frontend

New terminal tab, from the project root:

```bash
cd frontend
```

**Step 1 — install dependencies** (first time only, takes a minute)

```bash
npm install
```

> This creates a `node_modules/` folder — Node's version of a virtual environment. It's per-project
> and never committed to git.

**Step 2 — start the dev server**

```bash
npm run dev
```

Open **http://localhost:5173** and sign in:

```
dana@example.com / counsel-demo-2026
```

---

## 4. Other commands

```bash
npm run typecheck   # tsc --noEmit — catch TypeScript errors
npm run lint        # eslint
npm run build       # typecheck + production build into dist/
npm run preview     # serve the production build locally
```

---

## 5. How it talks to the backend

`vite.config.ts` **proxies** `/api`, `/healthz` and `/readyz` to `http://127.0.0.1:8000`. That makes
the app same-origin in development, so login cookies just work. Nothing to configure.

Only if you need a backend somewhere else:

```bash
cp .env.example .env
# then set VITE_API_BASE_URL=http://localhost:8000
```

> ⚠️ Always use **`localhost:5173`**, not `127.0.0.1:5173`. Mixing the two makes the login cookie
> get dropped — login looks successful, then every request returns 401.

---

## 6. Tips & common problems

- **Two terminals, always:** one for the backend, one for the frontend.
- **Stop the server** with `Ctrl + C`.
- Vite is hot-reloading — save a file and the browser updates instantly, no restart.
- **`npm: command not found`** → reopen the terminal after installing Node.
- **Port 5173 in use** → `npm run dev -- --port 5174`.
- **Blank page / 401 errors** → the backend isn't running, or you're on `127.0.0.1:5173`.
- **Install broke halfway** → `rm -rf node_modules package-lock.json && npm install`.
- **Never commit** `node_modules/`, `.env` or `dist/` — already in `.gitignore`.
- `/kitchen-sink` is a dev route showing every UI component with sample data — handy for styling work.

---

## 7. Where things live

```
frontend/src/
  api/          the backend contract — types, fetch client, SSE streaming
  store/        zustand state (session, workspace, conversations, ...)
  components/   ui · auth · shell · sidebar · composer · chat · answer · evidence · viewer
  pages/        Chat · Library · Document · Settings · Login
  hooks/        reusable React hooks
  lib/          helpers — formatting, templates, hotkeys, exporting
  styles/       theme.css = design tokens · global.css = reset
```

---

## 8. Run everything with Docker instead

No Node install needed — just [Docker Desktop](https://docker.com/products/docker-desktop):

```bash
docker compose up --build      # from the project root, starts backend + frontend
```

Then open **http://localhost:5173**.
