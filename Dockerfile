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
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt && python -m pip check

COPY backend/app /app/app
COPY --from=web /web/build /app/static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DATA_DIR=/data STATIC_DIR=/app/static PASCAL_PATCH=${PASCAL_PATCH}
VOLUME /data
EXPOSE 8080 7007
ENTRYPOINT ["/entrypoint.sh"]
