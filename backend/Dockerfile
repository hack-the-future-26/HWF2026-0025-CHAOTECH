# Built from the repo root: docker build -f backend/Dockerfile .
# (needs the root as build context so it can also copy pipeline/)
FROM python:3.11-slim

WORKDIR /app

# Only the packages actually used by backend/ + pipeline/ + intelligence/
# right now. NOT pipeline/requirements.txt wholesale -- that also lists
# sentence-transformers/spacy for embedding work that's only imported lazily
# inside function bodies, which pulls in torch (~gigabytes) for code paths
# that aren't hit at import time. numpy/scikit-learn ARE needed eagerly:
# intelligence/__init__.py imports recompute -> clustering, which imports
# sklearn.cluster.DBSCAN and numpy at module load.
COPY backend/requirements.txt ./backend-requirements.txt
RUN pip install --no-cache-dir -r backend-requirements.txt faster-whisper numpy scikit-learn

COPY backend/ ./backend/
COPY pipeline/ ./pipeline/
COPY intelligence/ ./intelligence/

WORKDIR /app/backend

EXPOSE 8000

CMD python create_tables.py && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
