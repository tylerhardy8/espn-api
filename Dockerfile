FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY setup.py pyproject.toml README.md ./
COPY espn_api/_version.py espn_api/_version.py

# Install the espn_api package and its dependencies
RUN pip install --no-cache-dir -e .

# Copy the rest of the project
COPY . .

# Install the analyzer's additional dependencies (anthropic is optional)
RUN pip install --no-cache-dir flask anthropic feedparser pytest

# Expose web UI port
EXPOSE 5000

# Default: run the web UI
ENTRYPOINT ["python", "-m", "fantasy_football_analyzer"]
CMD ["web", "--host", "0.0.0.0", "--port", "5000"]
