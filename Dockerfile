# CPU default. GPU profile builds the same code against the official CUDA wheel index.
FROM python:3.12-slim-bookworm
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    DATA_DIR=/data ASR_MODEL_PATH=/models/gigaam TMPDIR=/data/tmp
RUN apt-get update && apt-get install --no-install-recommends -y ffmpeg git fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app && useradd -u 10001 -g app -m app
WORKDIR /app
COPY requirements.txt requirements-asr.lock ./
RUN pip install --require-hashes -r requirements.txt \
    && pip install torch==2.10.0 torchaudio==2.10.0 --index-url ${TORCH_INDEX_URL} \
    && pip install -r requirements-asr.lock
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
RUN mkdir -p /data/tmp /models && chown -R app:app /data /models
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
