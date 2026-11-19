# WebMind API image.
#   docker build -t webmind .
#   docker run --rm -p 8080:8080 -v $PWD/webmind.yaml:/app/config/webmind.yaml:ro webmind
FROM python:3.12-slim AS build
WORKDIR /src
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels ".[postgres,pdf,docx,xlsx,reports,numpy,anthropic]"

FROM python:3.12-slim
LABEL org.opencontainers.image.title="WebMind" \
      org.opencontainers.image.description="Self-hosted, permission-aware website intelligence" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/kowshikramtg/WebMind"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEBMIND_CONFIG=/app/config/webmind.yaml
RUN useradd --create-home --uid 10001 webmind
COPY --from=build /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels
WORKDIR /app
RUN mkdir -p /app/config /app/data /app/content && chown -R webmind:webmind /app
USER webmind
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4).status == 200 else 1)"
CMD ["webmind", "serve", "--host", "0.0.0.0", "--port", "8080"]
