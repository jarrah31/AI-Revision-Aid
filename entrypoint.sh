#!/bin/sh
# Ensure data subdirectories exist even when data/ is a freshly-mounted
# empty volume (Docker volume mounts override the image's directory contents).
set -e
mkdir -p /app/data/images /app/data/pdfs
exec "$@"
