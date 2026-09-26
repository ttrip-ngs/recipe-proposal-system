# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# builder stage: uv で依存を解決し wheel を構築
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.5.4

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-dev --no-install-project

# pyproject.toml の readme = "README.md" を hatchling がメタデータ検証時に読むため、
# プロジェクト本体を install する 2 回目の uv sync より前に配置する必要がある.
COPY README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# runtime stage: 実行に必要な最小セット
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 食材正規化辞書・プロンプト YAML・静的アセットはいずれも src/ 配下にあるため
# この COPY 一本で入る (個別 COPY は重複のため削除).
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

RUN groupadd --system app && useradd --system --gid app --no-create-home app
USER app

EXPOSE 8080

CMD ["sh", "-c", "uvicorn recipe_system.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
