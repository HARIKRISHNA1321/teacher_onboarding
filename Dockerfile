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

# Run Streamlit app
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port", "8000", "--server.address", "0.0.0.0"]