FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm curl ca-certificates unzip && \
    npm install -g @anthropic-ai/claude-code && \
    curl -fsSL https://cli.kiro.dev/install | bash && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/
COPY .kiro/ .kiro/

ENTRYPOINT ["python"]
