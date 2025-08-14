FROM python:3.11-alpine3.19

# Install minimal runtime dependencies
RUN apk add --no-cache \
    curl \
    sqlite \
    su-exec \
    && rm -rf /var/cache/apk/*

# Create non-root user (optional, for when user specifies --user)
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

# Create data directory
RUN mkdir -p /app/data

# Run as root by default - this eliminates permission issues with mounted volumes
# Users can still specify --user flag if they want to run as non-root
# Environment variables
ENV PYTHONPATH=/app \
    DATABASE_URL=sqlite:///./data/mylabvault.db \
    DOCKER_ENV=true \
    PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start application
CMD ["python", "run.py"] 