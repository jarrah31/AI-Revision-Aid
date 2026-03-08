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

# Ensure the data directories exist inside the image so the volume mount
# works correctly even on a first run with an empty host directory.
RUN mkdir -p data/images data/pdfs

EXPOSE 8000

# Use uvicorn directly (no --reload in production)
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
