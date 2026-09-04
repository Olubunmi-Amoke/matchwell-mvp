FROM python:3.12-slim

RUN useradd --create-home --uid 1000 user

USER user
ENV HOME=/home/user \
    PATH=/home/user/app/.venv/bin:/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR $HOME/app

RUN pip install --no-cache-dir --user uv==0.11.19

COPY --chown=user:user pyproject.toml uv.lock README.md ./
COPY --chown=user:user src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY --chown=user:user app ./app
COPY --chown=user:user assets ./assets
COPY --chown=user:user migrations ./migrations
COPY --chown=user:user alembic.ini ./
COPY --chown=user:user .streamlit/config.toml ./.streamlit/config.toml

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/_stcore/health', timeout=3)"

CMD ["sh", "-c", "streamlit run app/main.py --server.address=0.0.0.0 --server.port=${PORT:-7860}"]
