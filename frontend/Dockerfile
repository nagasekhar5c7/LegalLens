# Multi-stage: Node builds the static bundle, nginx serves it. No Node at runtime.
FROM node:22.17.0-alpine AS build

WORKDIR /app

# Dependency layer first, so a source change does not reinstall node_modules.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Empty means same-origin: nginx below proxies /api to the backend, which keeps
# the SameSite=Lax session cookie working (see src/api/env.ts). Vite inlines
# this at build time, so changing it means rebuilding the image.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27.5-alpine AS runtime

# Where nginx forwards /api, /healthz and /readyz. The default reaches a backend
# published on the host by Docker Desktop; override with -e for anything else.
ENV BACKEND_URL=http://host.docker.internal:8000

# The image's entrypoint renders templates/*.template into conf.d at start,
# substituting only variables that are set in the environment.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# Non-root image: nginx listens on 8080, not 80.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/nginx-health || exit 1
