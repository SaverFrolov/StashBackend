FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
# pypi.org недоступен с этой машины — зеркало Aliyun.
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.txt faster-whisper==1.2.1 av==15.1.0

COPY app ./app
COPY migrations ./migrations

# Транскрибация внутри контейнера: модель монтируется в /opt/stash-transcribe/models.
ENV TRANSCRIBE_PYTHON=/usr/local/bin/python \
    TRANSCRIBE_SCRIPT=/opt/stash-transcribe/transcribe.py \
    FILES_DIR=/data/files

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
