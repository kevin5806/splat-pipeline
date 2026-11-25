# syntax=docker/dockerfile:1

# --- 1) pannello Svelte -> file statici
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- 2) immagine finale: Nerfstudio (COLMAP, ffmpeg, CUDA 11.8) + backend + pannello
FROM ghcr.io/nerfstudio-project/nerfstudio:latest

# GPU Pascal (Tesla P4/P40, GTX 10xx): docker compose build --build-arg PASCAL_PATCH=1
ARG PASCAL_PATCH=0
USER root
COPY docker/pascal_patch.sh /tmp/pascal_patch.sh
RUN if [ "$PASCAL_PATCH" = "1" ]; then bash /tmp/pascal_patch.sh; fi && rm -f /tmp/pascal_patch.sh

COPY backend/requirements.txt /tmp/requirements.txt
# pip check: l'immagine di Nerfstudio ha già l'avviso "ninja ... is not supported on this platform"; qualsiasi altro
# conflitto introdotto dalle nostre dipendenze fa fallire la build.
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt  && (python -m pip check 2>&1 | grep -v -E "^ninja .* is not supported on this platform" || true) > /tmp/pipcheck.txt  && cat /tmp/pipcheck.txt && test ! -s /tmp/pipcheck.txt

COPY backend/app /app/app
COPY --from=web /web/build /app/static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data STATIC_DIR=/app/static PASCAL_PATCH=${PASCAL_PATCH}
VOLUME /data
EXPOSE 8080 7007
ENTRYPOINT ["/entrypoint.sh"]
