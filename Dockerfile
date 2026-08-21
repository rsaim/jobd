# jobd — dashboard + pipeline in one image.
#
# Stage 1 builds the frontend into src/jobd/web/static (the path the server
# serves from); stage 2 installs the Python package on top of it. The image
# runs entirely offline out of the box: the demo seeder and the rule-based
# classifier need no API key, no Gmail credentials and no S3 — raw mail goes
# to a filesystem store on a volume. Real Gmail ingestion and LLM extraction
# work in the same image once their credentials are provided (see AGENTS.md).

FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* frontend/
RUN cd frontend && npm ci
COPY frontend/ frontend/
# vite.config.ts writes to ../src/jobd/web/static — mirror the repo layout.
RUN mkdir -p src/jobd/web && cd frontend && npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY --from=frontend /build/src/jobd/web/static src/jobd/web/static
RUN pip install --no-cache-dir ".[llm]"

ENV JOBD_LOCAL_STORE=/data/raw
VOLUME /data
EXPOSE 8100

CMD ["jobd", "serve", "--host", "0.0.0.0", "--port", "8100"]
