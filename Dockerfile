# Use the slim Python 3.12 image to keep the image size small.
# "slim" omits build tools and docs that aren't needed at runtime.
FROM python:3.12-slim

# Set a working directory inside the container.
WORKDIR /app

# Copy only the files needed to install dependencies first.
# This layer is cached by Docker — it only re-runs when pyproject.toml changes,
# not every time you change application code.
COPY pyproject.toml ./
COPY src/ ./src/

# Install the package (no dev deps needed in production).
# --no-cache-dir keeps the image smaller by not caching pip's download cache.
RUN pip install --no-cache-dir .

# The data directory is mounted from the host at runtime (bind mount),
# so no PLM data ever lives inside the image itself.
# We just create the mount point so Docker knows it exists.
RUN mkdir -p /data

# Expose the default web port.
EXPOSE 8000

# Run the web server.
# Environment variables (PLM_PASSWORD, PLM_SESSION_SECRET, etc.) are passed
# in at runtime via docker run -e or docker-compose — not baked into the image.
CMD ["plm-web"]
