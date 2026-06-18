FROM python:3.10

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# 让 `import app` 不依赖当前工作目录（项目未以 editable 包安装时必需）：
# 直接执行 `python app/xxx.py` 时 sys.path[0] 会变成 app/ 子目录，
# 显式把项目根 /app/backend 加入 PYTHONPATH 才能正确解析 `app` 顶层包。
ENV PYTHONPATH="/app/backend"

# Install dependencies from requirements.txt (设计文档要求提供 requirements.txt)
# 用 uv 创建独立 venv 并安装，避免 uv.lock 与新增 AI 依赖不同步导致 --frozen 失败。
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/requirements.txt,target=requirements.txt \
    uv venv /app/.venv && uv pip install -r requirements.txt

COPY ./backend/scripts /app/backend/scripts

COPY ./backend/pyproject.toml ./backend/alembic.ini /app/backend/

COPY ./backend/app /app/backend/app

WORKDIR /app/backend/

CMD ["fastapi", "run", "--workers", "4", "app/main.py"]
