FROM python:3.14-alpine3.24

# Install minimal runtime dependencies
RUN apk add --no-cache \
    curl \
    sqlite \
    su-exec \
    && rm -rf /var/cache/apk/*

# The app runs as this user (see docker-entrypoint.sh); PUID/PGID can change its IDs at start
RUN addgroup -g 1001 -S appuser && \
    adduser -u 1001 -S appuser -G appuser

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .

# Install Python packages with build dependencies
RUN apk add --no-cache --virtual .build-deps \
    gcc \
    musl-dev \
    libffi-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

# Copy application code
COPY . .

# Build information shown in the app footer and used by the update check (set by CI)
ARG MYLABVAULT_BUILD=dev
ARG MYLABVAULT_COMMIT=
ARG MYLABVAULT_BUILD_DATE=
ARG MYLABVAULT_REPOSITORY=zaydons/MyLabVault
ENV MYLABVAULT_BUILD=$MYLABVAULT_BUILD \
    MYLABVAULT_COMMIT=$MYLABVAULT_COMMIT \
    MYLABVAULT_BUILD_DATE=$MYLABVAULT_BUILD_DATE \
    MYLABVAULT_REPOSITORY=$MYLABVAULT_REPOSITORY
LABEL org.opencontainers.image.version=$MYLABVAULT_BUILD \
      org.opencontainers.image.revision=$MYLABVAULT_COMMIT \
      org.opencontainers.image.created=$MYLABVAULT_BUILD_DATE

# Data directory, owned by the app user (a mounted volume is fixed up at start)
RUN mkdir -p /app/data/uploads/pdfs && chown -R appuser:appuser /app/data \
    && chmod +x /app/docker-entrypoint.sh

ENV PYTHONPATH=/app \
    DATABASE_URL=sqlite:///./data/mylabvault.db \
    DOCKER_ENV=true \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start as root only to fix data-folder ownership, then run the app as appuser
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "run.py"] 