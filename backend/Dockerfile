# Built from the repo root: docker build -f backend/Dockerfile .
# (needs the root as build context so it can also copy pipeline/)
FROM python:3.11-slim

WORKDIR /app

# Only the packages actually used by backend/ + pipeline/ right now.
# NOT pipeline/requirements.txt wholesale -- that also lists
# sentence-transformers/spacy for P3's future clustering work, which pulls
# in torch (~gigabytes) for code that doesn't exist yet.
COPY backend/requirements.txt ./backend-requirements.txt
RUN pip install --no-cache-dir -r backend-requirements.txt faster-whisper

COPY backend/ ./backend/
COPY pipeline/ ./pipeline/

WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
