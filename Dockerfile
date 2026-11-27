# Stage 1: build the React app
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Python API that also serves the built app
FROM python:3.12-slim
WORKDIR /app
COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt
COPY gto/ gto/
COPY server/ server/
COPY trips/examples/ trips/examples/
COPY --from=web /web/dist web/dist
ENV PORT=8000
CMD uvicorn server.app:app --host 0.0.0.0 --port ${PORT}
