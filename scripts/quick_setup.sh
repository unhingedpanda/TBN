#!/bin/bash

echo "🚀 Quick Setup for Case Management System"
echo "========================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "📋 Creating .env file..."
    cp .env.example .env
    echo "⚠️  Please edit .env with your actual values before running!"
    echo "   Required: DATABASE_URL, SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET"
    exit 1
fi

# Setup virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
fi

echo "🔧 Activating virtual environment..."
source venv/bin/activate

echo "📚 Installing dependencies..."
pip install -r requirements.txt

echo "🗄️  Initializing database..."
python -m app.db

echo "✅ Setup complete! You can now run:"
echo "   python -m app.main    # Start the server"
echo "   ./demo_run.sh         # Run tests when server is running"

