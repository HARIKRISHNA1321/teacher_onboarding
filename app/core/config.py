import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Application configuration
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
ENV = os.getenv("ENV", "production")

# Pinecone Database configurations
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_ENV = os.getenv("PINECONE_ENV", "")

# Mock Secrets / External tokens (never logged directly)
HR_EMAIL = os.getenv("HR_EMAIL", "hr@pes.edu")
SMTP_TOKEN = os.getenv("SMTP_TOKEN", "mock-smtp-token")
CALENDAR_TOKEN = os.getenv("CALENDAR_TOKEN", "mock-calendar-token")

# SMTP Configurations
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "hr@pes.edu")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "mock-password")
