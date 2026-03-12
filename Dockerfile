# Use the slim Python 3.12 image to keep the image size small.
# "slim" omits build tools and docs that aren't needed at runtime.
FROM python:3.12-slim

# Set a working directory inside the container.
WORKDIR /app

# ── Dependency layer (cached unless pyproject.toml changes) ──────────────────
# Copy only pyproject.toml first, then install dependencies with a minimal stub
# package so pip can resolve and download everything.  Because src/ is NOT copied
# yet, this layer is only invalidated when pyproject.toml changes — not on every
# source code edit.  On a Pi that keeps rebuild time from ~60s to ~5s for
# code-only changes.
COPY pyproject.toml ./
RUN mkdir -p src/plm && touch src/plm/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf src/

# ── Source layer ──────────────────────────────────────────────────────────────
# Now copy the real source and reinstall just the package itself (--no-deps
# skips re-downloading dependencies that were already installed above).
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# The data directory is mounted from the host at runtime (bind mount),
# so no PLM data ever lives inside the image itself.
# We just create the mount point so Docker knows it exists.
RUN mkdir -p /data

# Default port. Override by setting PLM_PORT in .env — docker-compose.yml
# maps both sides of the host:container port binding to the same value, so
# changing PLM_PORT there updates both simultaneously.
ENV PLM_PORT=2026
EXPOSE 2026

# Run the web server.
# Environment variables (PLM_PASSWORD, PLM_SESSION_SECRET, etc.) are passed
# in at runtime via docker-compose — not baked into the image.
CMD ["plm-web"]
