FROM python:3.11-slim

WORKDIR /app

# Skip heavy system dependencies (build-essential, libpq-dev) to save memory/time.
# We rely on 'psycopg2-binary' and pre-built wheels.
# RUN apt-get update && apt-get install -y build-essential libpq-dev git && rm -rf /var/lib/apt/lists/*

# Copy requirements first to utilize Docker cache
COPY requirements.txt .
# Install dependencies with memory constraints in mind
# --only-binary :all: tries to avoid compiling from source
RUN pip install --no-cache-dir --only-binary :all: -r requirements.txt || pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the application port
EXPOSE 5003

# Command to run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5003"]
