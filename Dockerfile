# Dockerfile for Render deployment
FROM python:3.11-slim

WORKDIR /app

# Copy dependency requirements
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY ./app ./app

# Expose port (default for Render is 8000 or dynamically set via PORT env)
EXPOSE 8000

# Run FastAPI app via uvicorn
CMD ["uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8000"]