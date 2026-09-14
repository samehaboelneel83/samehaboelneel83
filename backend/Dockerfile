FROM python:3.11-slim

# libgomp is required by the OR-Tools and HiGHS shared objects.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY psp ./psp
COPY migrations ./migrations

# Solves run in worker subprocesses of this image, so the package must be
# importable from a bare interpreter, not only from the app process.
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "psp.main:app", "--host", "0.0.0.0", "--port", "8000"]
