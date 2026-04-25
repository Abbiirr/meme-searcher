FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY vidsearch/ vidsearch/
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.11.0
RUN pip install --no-cache-dir .

COPY infra/ infra/

EXPOSE 8000

CMD ["uvicorn", "vidsearch.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
