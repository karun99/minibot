# syntax=docker/dockerfile:1
# minibot is a terminal client for a local Ollama server. The image contains
# the bot only; point it at Ollama with `--ollama-url` (or config.example.json)
# so a host or sidecar instance provides the model.
#
#   docker build -t minibot .
#   docker run --rm -it -v "$PWD/workspace:/workspace" -w /workspace \
#       minibot --ollama-url http://host.docker.internal:11434

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY minibot ./minibot
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 minibot \
    && mkdir -p /workspace \
    && chown -R minibot:minibot /workspace
USER minibot
WORKDIR /workspace

ENTRYPOINT ["minibot"]
CMD ["--help"]
