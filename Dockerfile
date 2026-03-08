FROM python:3.12-slim

WORKDIR /app

# Build tools needed by some Python packages (cryptography, bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy and register the entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# entrypoint.sh re-creates data/images and data/pdfs at container start so
# the app works even when data/ is a freshly-mounted empty volume.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
