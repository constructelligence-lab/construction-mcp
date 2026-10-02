# Hosts the server over Streamable HTTP. No dependencies to install: the standard library is enough.
FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY construction_mcp ./construction_mcp
RUN useradd --create-home --uid 10001 mcp
USER mcp
ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"
CMD ["python", "server.py", "--http", "--host", "0.0.0.0"]
