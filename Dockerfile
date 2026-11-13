FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY skills ./skills
COPY web ./web
COPY samples ./samples

RUN pip install --no-cache-dir . && \
    groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid 10001 --create-home --shell /sbin/nologin appuser && \
    chown -R appuser:appuser /app

USER 10001:10001

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "prod-ready ui /app/samples/02_cloud_run_agent --host 0.0.0.0 --port ${PORT:-8080}"]
