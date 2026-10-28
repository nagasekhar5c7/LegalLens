Task: Create (or review and improve, if one already exists) a production-ready Dockerfile for the code in backend/, so I can build and run it by hand with docker build and docker run on my Mac.

Requirements:

Read the codebase first. Look at backend/pyproject.toml, uv.lock, app/start.py, the settings modules, and any existing Dockerfile, .dockerignore, or DOCKER.md. Find the correct Python version, the dependency manager (uv), the entrypoint, the port, and the environment variables the app needs. Don't guess any of these.
Image design:
A multi-stage build: a builder stage that installs dependencies with uv sync --frozen, and a slim runtime stage.
A pinned base image version, not latest.
Runs as a non-root user.
Copy dependency files before the source code so Docker can cache the dependency layer.
A .dockerignore that keeps out .venv, tests, caches, .env, and git files.
A HEALTHCHECK or a documented health endpoint that AKS liveness and readiness probes can use later.
The port, workers, and other runtime settings come from environment variables. No secrets go into the image.
Note any ML or embedding models the app downloads at startup, and say whether they should be baked into the image or loaded at runtime, with the trade-off.
Explain your decisions. For every non-obvious choice (base image, how models are handled, how the server is run, image size), give one line on why.
Give me the exact commands to test it locally:
docker build with a sensible tag (for example legallens-backend:local).
docker run with port mapping and --env-file. List every required environment variable, with an example .env.docker template that contains no real secrets.
How to check that it works: a health endpoint curl, the OpenAPI docs URL, and one real API call.
How to view logs, open a shell inside the container, and stop or clean up.
Actually try it. If Docker is available, run the build and fix any errors until the image builds cleanly. Report the final image size and anything you could not verify.
Keep it AKS-friendly without building the AKS parts yet: the image should be stateless, log to stdout, shut down cleanly on SIGTERM, and work on linux/amd64 (I'm on Apple Silicon, so show the --platform flag).
