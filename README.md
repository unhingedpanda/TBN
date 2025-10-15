# Customer Case Management & Escalation System

A self-hosted Python-based case management system that ingests customer messages via email and Slack, creates/tracks cases, handles escalation rules, and manages case closure with comprehensive logging.

## Features

- **Multi-channel ingestion**: Email (IMAP) and Slack webhook support
- **Intelligent case management**: Automatic case creation and message threading
- **Smart escalation**: Time-based (48h), follow-up count (3+), and keyword-based ("urgent", "immediately")
- **Admin closure detection**: Automated case closure when admin sends "I'm closing this case."
- **Slack notifications**: Support channel notifications, escalation alerts, and logging
- **Persistent storage**: PostgreSQL database with SQLite fallback for development
- **Comprehensive logging**: Structured logging for debugging and monitoring

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.11+
- Slack workspace with bot token
- Email account with IMAP access

### 1. Clone and Setup

```bash
git clone <your-repo-url>
cd tbnAssignment
cp .env.example .env
# Edit .env with your configuration
```

### 2. Start with Docker (Recommended)

```bash
docker-compose up -d
```

The system will be available at:
- API: http://localhost:8000
- Slack webhook endpoint: http://localhost:8000/slack/events

### 3. Manual Setup (Development)

```bash
# Setup virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run database migrations
python -m app.db

# Start the application
python -m app.main
```

## Configuration

See `.env.example` for all required environment variables:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/case_management
# Fallback to SQLite for development
# DATABASE_URL=sqlite:///./case_management.db

# Email Configuration
IMAP_SERVER=imap.gmail.com
IMAP_EMAIL=your-support@example.com
IMAP_PASSWORD=your-app-password
ADMIN_EMAILS=admin1@example.com,admin2@example.com

# Slack Configuration
SUPPORT_SLACK_CHANNEL=C-support-channel
ALERTING_SLACK_CHANNEL=C-alerts
LOGGING_SLACK_CHANNEL=C-logs
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token

# Application
DEBUG=True
LOG_LEVEL=INFO
```

## Testing

### Run Unit Tests

```bash
# With pytest
pytest tests/

# With coverage
pytest --cov=app tests/
```

### Manual Testing

Use the provided sample data:

```bash
# Test email ingestion
python scripts/test_email_ingestion.py

# Test Slack webhook
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d @samples/slack_message_new.json

# Run full demo
./demo_run.sh
```

## API Endpoints

- `POST /slack/events` - Slack webhook endpoint for message ingestion
- `GET /health` - Health check endpoint
- `GET /cases` - List all cases (development only)

## Architecture

The system consists of several key components:

- **Email Listener**: Polls IMAP server for new messages
- **Slack Listener**: Receives webhook events from Slack
- **Scheduler**: Checks for escalation conditions periodically
- **Database Layer**: Stores cases, messages, and metadata
- **Notification Service**: Sends messages to configured Slack channels

## Deployment

### Production Deployment

1. Set up a server with Docker
2. Configure environment variables
3. Run `docker-compose -f docker-compose.prod.yml up -d`
4. Set up SSL certificate (nginx reverse proxy recommended)

### Non-Docker Deployment

1. Install Python 3.11+ and PostgreSQL
2. Install dependencies: `pip install -r requirements.txt`
3. Configure environment variables
4. Run database migrations: `python -m app.db`
5. Start services: `python -m app.main`

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

MIT License - see LICENSE file for details.
