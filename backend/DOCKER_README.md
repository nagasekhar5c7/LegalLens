# Running the LegalLens backend with Docker

This guide covers building the backend into a Docker image and running it on your machine.
When you're done, the API is available at http://localhost:8000.

## 1. Install Docker

Download and install Docker Desktop for your operating system:

**https://www.docker.com/get-started/**

When the install finishes, **open Docker Desktop** and wait until it says it is running.
On macOS, the whale icon in the menu bar stops animating when it's ready.

Check that it works:

```bash
docker info
```

If the output includes a `Server:` section with version details, Docker is ready.
If you see `Cannot connect to the Docker daemon`, Docker Desktop is not running yet.
Start it (macOS: `open -a Docker`), wait a few seconds, and try again.

## 2. Set up your environment file

Every command below runs from the `backend/` folder.

```bash
cd backend
cp .env.example .env
```

Open `.env` and add your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Without a key, the backend still starts, but answers come from the built-in fixture engine instead of Claude.

`.env` holds secrets. It is never copied into the image and should never be committed.

## 3. Build the image

```bash
docker build -t legallens-backend .
```

This packages Python, every dependency and the backend code into an **image**, a ready-to-run snapshot.

| Part | Meaning |
|---|---|
| `docker build` | Build an image by following the steps in `Dockerfile` |
| `-t legallens-backend` | **Tag** (name) the image so later commands can refer to it |
| `.` | The **build context**: the current folder. Docker can only copy files from here, and anything listed in `.dockerignore` (such as `.env` and `.venv`) is left out. |

The Dockerfile works in two stages:

1. **Dependencies:** installs the Python packages from `uv.lock`, including the AI stack (LangChain, Anthropic, Qdrant, and CPU-only PyTorch).
   This step is cached, so later builds are fast unless `pyproject.toml` or `uv.lock` changes.
2. **Runtime:** copies only the installed packages and the app code into a clean image that runs as a non-root user.
   When the container starts, it applies the database migrations and then starts the server.

The first build takes a few minutes and produces an image of about 2.5 GB.
Rebuild whenever you change code or dependencies.

## 4. Run the container

```bash
docker run -d --name legallens-backend \
  -p 8000:8000 \
  --env-file .env \
  -e LL_DATABASE_URL=sqlite+aiosqlite:////app/data/legallens.db \
  -e LL_DATA_DIR=/app/data \
  -e LL_CORS_ORIGINS='["http://localhost:5173"]' \
  -e LL_COOKIE_SECURE=false \
  -v legallens-data:/app/data \
  legallens-backend
```

This starts a **container**, a running instance of the image.

| Part | Meaning |
|---|---|
| `docker run` | Create and start a container |
| `-d` | **Detached**: runs in the background and gives you your terminal back |
| `--name legallens-backend` | A name to use with `docker logs`, `docker stop` and `docker rm` |
| `-p 8000:8000` | **Port mapping** in `your-machine:container` order. Lets `localhost:8000` reach the server inside the container. |
| `--env-file .env` | Loads every variable in `.env` (including `ANTHROPIC_API_KEY`) into the container when it starts |
| `-e LL_DATABASE_URL=...` | Puts the SQLite database inside the container's data folder. The four slashes mean the absolute path `/app/data/legallens.db`. |
| `-e LL_DATA_DIR=/app/data` | Where the backend keeps uploaded documents, generated keys and downloaded AI models |
| `-e LL_CORS_ORIGINS=...` | Allows the frontend at `http://localhost:5173` to call this API from the browser |
| `-e LL_COOKIE_SECURE=false` | Lets login cookies work over plain `http://localhost`. Set it to `true` when serving over HTTPS. |
| `-v legallens-data:/app/data` | **Volume**: storage managed by Docker and mounted at `/app/data`. Your database, documents and models survive when the container is deleted or rebuilt. |
| `legallens-backend` | The image to run: the tag from step 3 |

If a variable is set both in `.env` and with `-e`, the `-e` value wins.
That is how these flags override the local-machine paths in `.env`.

## 5. Check that it's running

```bash
curl http://localhost:8000/healthz
```

The expected response is `{"status":"ok"}`.

The first question you ask downloads the AI retrieval models (about 4.4 GB) into the volume, so that first answer is slow.
Later answers, including those after restarts and rebuilds, reuse the downloaded models.

## Everyday commands

```bash
docker logs -f legallens-backend   # follow the logs (Ctrl+C to stop watching)
docker stop legallens-backend      # stop the container
docker start legallens-backend     # start it again with the same settings
docker rm -f legallens-backend     # delete the container (the data volume is kept)
docker ps                          # list running containers
```

### After changing code

A running container keeps using the image it was created from.
To pick up changes, rebuild the image, remove the old container, and run it again:

```bash
docker build -t legallens-backend .
docker rm -f legallens-backend
# then repeat the docker run command from step 4
```

### Starting over with empty data

This **permanently deletes** the database, every uploaded document and the downloaded models:

```bash
docker rm -f legallens-backend
docker volume rm legallens-data
```

## Running frontend and backend together

To start both at once, run this from the repository root instead:

```bash
docker compose up --build
```

Then open http://localhost:5173.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running. Open it and wait until it's ready. |
| `Conflict. The container name "/legallens-backend" is already in use` | Remove the old container with `docker rm -f legallens-backend`, then run again |
| `port is already allocated` | Something else is using port 8000. Stop it, or use `-p 8001:8000` and open `localhost:8001`. |
| Container exits right after starting | Run `docker logs legallens-backend` to see the error |
| Answers don't come from Claude | Check that `ANTHROPIC_API_KEY` is set in `.env`, then recreate the container |
