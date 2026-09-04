FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g @anthropic-ai/claude-code && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/

ENTRYPOINT ["python"]
