# syntax=docker/dockerfile:1.7
# Frontend Notiva — build de Angular + nginx para servir.

# ---------- Stage 1: build ----------
FROM node:20-alpine AS build
WORKDIR /build

# Variable que el build de Angular reemplaza en environment.prod.ts
ARG NG_APP_API_URL=http://localhost:8000
ENV NG_APP_API_URL=${NG_APP_API_URL}

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .

# Sustituye la URL del API en environment.prod.ts antes de compilar.
# (Si el archivo no existe, el sed no falla.)
RUN if [ -f src/environments/environment.prod.ts ]; then \
      sed -i "s|apiUrl: ['\"][^'\"]*['\"]|apiUrl: '${NG_APP_API_URL}'|g" src/environments/environment.prod.ts && \
      sed -i "s|apiUrl: ['\"][^'\"]*['\"]|apiUrl: '${NG_APP_API_URL}'|g" src/environments/environment.ts ; \
    fi

RUN npm run build -- --configuration=production

# ---------- Stage 2: runtime nginx ----------
FROM nginx:alpine AS runtime

# Angular 17+ produce dist/<project>/browser/
COPY --from=build /build/dist /tmp/dist
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Detecta el path real del bundle (varía si el project name cambia) y lo
# mueve a /usr/share/nginx/html.
RUN set -eux; \
    BROWSER_DIR=$(find /tmp/dist -type d -name browser | head -n1); \
    if [ -z "$BROWSER_DIR" ]; then BROWSER_DIR=$(ls -d /tmp/dist/*/ | head -n1); fi; \
    rm -rf /usr/share/nginx/html && \
    cp -r "$BROWSER_DIR" /usr/share/nginx/html && \
    rm -rf /tmp/dist

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q --spider http://127.0.0.1:80 || exit 1
