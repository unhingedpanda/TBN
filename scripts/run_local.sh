#!/bin/bash

# Script to run the case management system locally with SQLite for development

set -e

echo "🚀 Starting Case Management System (Local Development Mode)"

# Check if .env exists, create from example if not
if [ ! -f .env ]; then
    echo "📋 Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  Please edit .env file with your configuration before running!"
    echo ""
    echo "Required environment variables:"
    echo "  - IMAP_EMAIL: Your email address for ingestion"
    echo "  - IMAP_PASSWORD: Your email password/app password"
    echo "  - ADMIN_EMAILS: Comma-separated list of admin email addresses"
    echo "  - SLACK_BOT_TOKEN: Your Slack bot token"
    echo "  - SUPPORT_SLACK_CHANNEL: Support channel name"
    echo "  - ALERTING_SLACK_CHANNEL: Alerting channel name"
    echo "  - LOGGING_SLACK_CHANNEL: Logging channel name"
    echo ""
    echo "Press Ctrl+C to exit and edit .env, or press Enter to continue with defaults..."
    read -r
fi

# Load environment variables
if [ -f .env ]; then
    source .env
fi

# Set SQLite as database for local development
export DATABASE_URL="${DATABASE_URL:-sqlite:///./case_management.db}"

echo "🔧 Setting up local development environment..."
echo "📊 Database: SQLite (local file)"

# Create necessary directories
mkdir -p logs

# Initialize database if it doesn't exist
if [ ! -f case_management.db ]; then
    echo "🗄️  Initializing database..."
    python -m app.db
else
    echo "🗄️  Database already exists"
fi

# Install dependencies if needed
if [ ! -d ".venv" ]; then
    echo "📦 Installing dependencies..."
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

echo "🎯 Starting application..."

# Start the application
python -m app.main
