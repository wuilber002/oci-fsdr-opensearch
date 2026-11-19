FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt requirements-api.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt -r requirements-api.txt \
    && addgroup --system --gid 10001 fsdrsvc \
    && adduser --system --uid 10001 --ingroup fsdrsvc --home /nonexistent fsdrsvc
COPY fsdr_opensearch ./fsdr_opensearch
USER 10001:10001
EXPOSE 1604
ENV PYTHONUNBUFFERED=1 FSDR_API_HOST=127.0.0.1 FSDR_API_PORT=1604
ENTRYPOINT ["python", "-m", "fsdr_opensearch.api"]
