FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY *.py ./

# Data volume
RUN mkdir -p /app/data/reports
VOLUME /app/data

# Dashboard port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import store; store.init_db(); print('ok')" || exit 1

# Default: schedule + dashboard
ENTRYPOINT ["python", "runner.py"]
CMD ["--schedule", "--dashboard"]
