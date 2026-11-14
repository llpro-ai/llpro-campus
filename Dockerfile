# llpro-mcp as a container: the same thin stdio bridge as the PyPI package, tagged ?via=docker.
# Operated by LLPRO LLC. No dependencies are installed: the image copies one standard-library module.
# Run:  docker run -i --rm llproai/llpro-mcp
# Base image pinned by digest (python:3.12-slim, read 14 Nov 2026); refresh the digest when you rebuild.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

LABEL org.opencontainers.image.title="llpro-mcp" \
      org.opencontainers.image.description="Thin stdio bridge to the LLPRO campus MCP server (https://llpro.ai/mcp?via=docker)" \
      org.opencontainers.image.source="https://github.com/llpro-ai/llpro-campus" \
      org.opencontainers.image.vendor="LLPRO LLC" \
      org.opencontainers.image.version="0.1.0" \
      io.modelcontextprotocol.server.name="ai.llpro/campus"

COPY python/src/llpro_mcp /app/llpro_mcp
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLPRO_VIA=docker
USER 65534:65534
ENTRYPOINT ["python", "-m", "llpro_mcp"]
