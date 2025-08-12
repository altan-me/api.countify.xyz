# Production Python image (small, secure)
FROM python:3.12-alpine

# App env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Workdir
WORKDIR /app

# System deps and Python deps
RUN apk add --no-cache sqlite-libs && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir flask gunicorn

# Copy project
COPY . /app

# Create data dir and non-root user
RUN mkdir -p /data && \
    addgroup -S app && adduser -S app -G app && \
    chown -R app:app /data /app

# Runtime env
ENV DATABASE=/data/counters.db

# Expose port
EXPOSE 5000/tcp

# Basic healthcheck (no curl required)
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request,sys; \
  sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:5000/stats').getcode()==200 else sys.exit(1)" || exit 1

# Drop privileges
USER app

# Run server (stdout logs)
CMD ["gunicorn", "-w", "4", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:5000", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
