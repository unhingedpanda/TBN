# Product Requirements Document (PRD)
## Self-Hosted Customer Case Management & Escalation System

### Version 1.0.0

---

## Table of Contents
1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Data Model](#data-model)
4. [Functional Requirements](#functional-requirements)
5. [Implementation Details](#implementation-details)
6. [API Endpoints](#api-endpoints)
7. [Environment Configuration](#environment-configuration)
8. [Deployment Guide](#deployment-guide)
9. [Testing Strategy](#testing-strategy)
10. [Assignment Requirements Mapping](#assignment-requirements-mapping)

---

## Overview

This system provides a self-hosted solution for customer case management with automatic escalation capabilities. It ingests customer messages via email (IMAP) and Slack webhooks, creates and tracks cases, implements intelligent escalation rules, and manages case closure with comprehensive logging.

### Key Features
- **Multi-channel ingestion**: Email and Slack message processing
- **Intelligent case management**: Automatic case creation and message threading
- **Smart escalation**: Time-based (48h), follow-up count (3+), and keyword-based triggers
- **Admin closure detection**: Automated case closure on specific phrases
- **Slack notifications**: Support channel, escalation alerts, and logging
- **Persistent storage**: PostgreSQL with SQLite fallback for development
- **Comprehensive logging**: Structured logging for monitoring and debugging

### Business Rules
1. **Case Creation**: New case for new customers, append to existing open cases
2. **Escalation Triggers**:
   - 48+ hours since last message
   - 3+ consecutive customer follow-ups without admin response
   - Keywords: "urgent", "immediately", "emergency", "critical"
3. **Case Closure**: Admin message containing "I'm closing this case."
4. **Notifications**: Real-time Slack notifications for all activities

---

## High-Level Architecture

```mermaid
graph TB
    A[Email Client] --> B[IMAP Server]
    C[Slack Client] --> D[Slack Webhook]
    B --> E[Email Listener]
    D --> F[Slack Listener]
    E --> G[(Database)]
    F --> G
    H[Scheduler] --> G
    I[Notification Service] --> J[Slack Channels]
    G --> I

    subgraph "Case Management System"
        E
        F
        H
        I
    end

    subgraph "External Services"
        A
        C
        J
    end

    G --> K[PostgreSQL/SQLite]
```

### Component Overview

1. **Email Listener** (`email_listener.py`): Polls IMAP server for new messages
2. **Slack Listener** (`slack_listener.py`): Processes webhook events from Slack
3. **Database Layer** (`db.py`): SQLAlchemy models and session management
4. **Scheduler** (`scheduler.py`): Background job processing for escalations
5. **Notification Service** (`notifications.py`): Slack message sending
6. **Main Application** (`main.py`): FastAPI server coordinating all components

### Data Flow

1. **Email Ingestion**:
   ```
   Email Client → IMAP Server → Email Listener → Database → Notifications
   ```

2. **Slack Ingestion**:
   ```
   Slack Client → Webhook → Slack Listener → Database → Notifications
   ```

3. **Escalation Checking**:
   ```
   Scheduler → Database Query → Escalate Cases → Notifications
   ```

---

## Data Model

### Database Schema

#### Cases Table
```sql
CREATE TABLE cases (
    case_id VARCHAR PRIMARY KEY,
    customer_identifier VARCHAR NOT NULL,
    status VARCHAR DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER DEFAULT 1,
    escalated BOOLEAN DEFAULT FALSE,
    closed_at TIMESTAMP
);
```

#### Messages Table
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    case_id VARCHAR REFERENCES cases(case_id),
    sender VARCHAR NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    body TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR NOT NULL  -- 'email' or 'slack'
);
```

### Case States
- **Open**: Active case receiving messages
- **Closed**: Case resolved by admin

### Message Types
- **Customer**: From customer (is_admin=False)
- **Admin**: From support team (is_admin=True)

---

## Functional Requirements

### Case Creation Logic
When a message arrives:
1. Extract customer identifier (email address or Slack user ID)
2. Query for existing open case for this customer
3. If no open case exists → Create new case
4. If open case exists → Append message to existing case
5. Update case metadata (last_message_at, message_count)

### Escalation Rules
A case is escalated when ANY of these conditions are met:

1. **Time-based**: `now - last_message_at > 48 hours`
2. **Follow-up count**: `> 3 consecutive` customer messages without admin response
3. **Keywords**: Message contains "urgent", "immediately", "emergency", "critical"

### Closure Detection
A case is closed when:
1. Admin sends message containing "I'm closing this case." (case-insensitive)
2. Case status set to "closed"
3. closed_at timestamp recorded
4. Future messages from same customer create new case

### Notification Behavior
- **New messages**: Posted to support channel with case ID and preview
- **Escalations**: Alert sent to alerting channel with reason
- **Closures**: Log sent to logging channel with timestamp and admin

---

## Implementation Details

### Core Components

#### `main.py` - FastAPI Application
- **Purpose**: HTTP server and application coordination
- **Endpoints**:
  - `POST /slack/events` - Slack webhook handler
  - `GET /health` - Health check
  - `GET /cases` - Debug endpoint (development only)
- **Features**:
  - CORS middleware for development
  - Structured logging
  - Graceful shutdown handling

#### `db.py` - Database Layer
- **Purpose**: SQLAlchemy models and database operations
- **Models**:
  - `Case`: Customer case representation
  - `Message`: Individual message within a case
- **Functions**:
  - `get_or_create_case()` - Case creation/retrieval logic
  - `add_message_to_case()` - Message appending with metadata updates
  - `close_case()` - Case closure with logging
  - `escalate_case()` - Case escalation marking

#### `email_listener.py` - IMAP Polling
- **Purpose**: Email ingestion via IMAP protocol
- **Features**:
  - Configurable polling interval
  - Email parsing (subject + body)
  - Admin email detection
  - Error handling and reconnection
- **Configuration**:
  - IMAP_SERVER, IMAP_EMAIL, IMAP_PASSWORD
  - ADMIN_EMAILS for admin detection

#### `slack_listener.py` - Webhook Processing
- **Purpose**: Slack event processing
- **Features**:
  - Event type filtering (message events only)
  - User ID extraction from Slack events
  - Bot message filtering
  - Thread handling for conversation continuity
- **Slack Event Types**:
  - `message`: New messages
  - `url_verification`: App setup verification

#### `scheduler.py` - Background Jobs
- **Purpose**: Periodic escalation checking
- **Implementation**: APScheduler with configurable intervals
- **Jobs**:
  - Escalation check (default: every 5 minutes)
  - Manual escalation triggering available
- **Logic**:
  - Query all open cases
  - Check each against escalation rules
  - Send alerts for cases needing escalation

#### `utils.py` - Business Logic Utilities
- **Purpose**: Reusable business logic functions
- **Functions**:
  - `detect_urgent_keywords()` - Keyword matching
  - `is_admin_message()` - Admin identification
  - `detect_closure_phrase()` - Closure command detection
  - `check_time_escalation()` - Time-based rules
  - `check_followup_escalation()` - Follow-up counting
  - `should_escalate_case()` - Overall escalation logic

#### `notifications.py` - Slack Integration
- **Purpose**: Slack channel messaging
- **Implementation**: `python-slack-sdk` WebClient
- **Channels**:
  - SUPPORT_SLACK_CHANNEL: New message notifications
  - ALERTING_SLACK_CHANNEL: Escalation alerts
  - LOGGING_SLACK_CHANNEL: Case closure logs
- **Features**:
  - Message formatting
  - Thread reply support
  - Error handling

### Third-Party Services

#### Slack Integration
- **Library**: `python-slack-sdk==3.26.0`
- **Configuration**:
  - `SLACK_BOT_TOKEN`: Bot token for API access
  - Channel IDs or names for notifications
- **Usage**:
  ```python
  from slack_sdk import WebClient
  client = WebClient(token=bot_token)
  client.chat_postMessage(channel=channel_id, text=message)
  ```

#### Email Processing
- **Library**: Built-in `imaplib` and `email` modules
- **Configuration**:
  - IMAP server connection details
  - Email parsing for content extraction
- **Usage**:
  ```python
  import imaplib
  mail = imaplib.IMAP4_SSL(server, port)
  mail.login(email, password)
  ```

### Error Handling
- **Database errors**: Rollback transactions on failure
- **Network errors**: Retry logic for IMAP and Slack connections
- **Message parsing errors**: Graceful degradation with logging
- **Configuration errors**: Clear error messages for missing settings

---

## API Endpoints

### Slack Webhook
```http
POST /slack/events
Content-Type: application/json

{
  "token": "verification-token",
  "event": {
    "type": "message",
    "user": "U1234567890",
    "text": "Customer message",
    "channel": "C1234567890"
  }
}
```

**Response**: `{"status": "success"}` or `{"status": "ignored"}`

### Health Check
```http
GET /health
```

**Response**:
```json
{
  "status": "healthy",
  "services": {
    "database": "connected",
    "email_listener": "running",
    "scheduler": "running",
    "slack": "configured"
  }
}
```

### Debug Cases (Development Only)
```http
GET /cases
```

**Response**:
```json
{
  "cases": [
    {
      "case_id": "CASE_001",
      "customer_identifier": "user@example.com",
      "status": "open",
      "message_count": 3,
      "escalated": false
    }
  ]
}
```

---

## Environment Configuration

### Required Environment Variables

```bash
# Database Configuration
DATABASE_URL=postgresql://user:password@localhost:5432/case_management
# OR for development:
DATABASE_URL=sqlite:///./case_management.db

# Email Configuration (IMAP)
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
IMAP_EMAIL=your-support@company.com
IMAP_PASSWORD=your-app-password
ADMIN_EMAILS=admin1@company.com,admin2@company.com

# Slack Configuration
SUPPORT_SLACK_CHANNEL=#support
ALERTING_SLACK_CHANNEL=#alerts
LOGGING_SLACK_CHANNEL=#logs
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token

# Application Settings
HOST=0.0.0.0
PORT=8000
DEBUG=false
LOG_LEVEL=INFO
EMAIL_POLL_INTERVAL=30
ESCALATION_CHECK_INTERVAL=300
```

### Sample `.env` File
```bash
# Database
DATABASE_URL=sqlite:///./case_management.db

# Email
IMAP_SERVER=imap.gmail.com
IMAP_EMAIL=support@mycompany.com
# IMAP_PASSWORD=your-app-password-here (example format)
ADMIN_EMAILS=admin@mycompany.com,manager@mycompany.com

# Slack
SUPPORT_SLACK_CHANNEL=#customer-support
ALERTING_SLACK_CHANNEL=#escalations
LOGGING_SLACK_CHANNEL=#case-logs
SLACK_BOT_TOKEN=your_slack_bot_token_here

# App Settings
DEBUG=true
LOG_LEVEL=INFO
```

---

## Deployment Guide

### Docker Compose (Recommended)

#### Development Setup
```bash
# Clone repository
git clone <repository-url>
cd tbnAssignment

# Copy and edit environment file
cp .env.example .env
# Edit .env with your configuration

# Start with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f app
```

#### Production Setup
```bash
# Use production compose file
docker-compose -f docker-compose.prod.yml up -d

# Or build and deploy manually
docker build -t case-management .
docker run -d \
  --name case-management \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql://... \
  -e SLACK_BOT_TOKEN=... \
  case-management
```

### Manual Installation

#### System Requirements
- Python 3.11+
- PostgreSQL 13+ (or SQLite for development)
- IMAP email account
- Slack workspace with bot token

#### Installation Steps
```bash
# 1. Clone and setup virtual environment
git clone <repository-url>
cd tbnAssignment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your settings

# 4. Initialize database
python -m app.db

# 5. Start application
python -m app.main
```

#### Systemd Service (Production)
```ini
[Unit]
Description=Case Management System
After=network.target postgresql.service

[Service]
Type=simple
User=caseuser
WorkingDirectory=/opt/case-management
EnvironmentFile=/opt/case-management/.env
ExecStart=/opt/case-management/venv/bin/python -m app.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Testing Strategy

### Unit Tests
Run with pytest:
```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest --cov=app tests/

# Run specific test file
pytest tests/test_case_logic.py -v
```

**Test Coverage**:
- Case creation and management
- Escalation rule detection
- Message processing logic
- Database operations
- Utility function validation

### Integration Tests
```bash
# Start test environment
docker-compose up -d

# Run demo script
./demo_run.sh

# Test email processing
python scripts/test_email_ingestion.py
```

### Manual Testing Steps

#### 1. Test Email Ingestion
```bash
# Configure IMAP settings in .env
# Send test email to configured address
# Check application logs for processing
tail -f logs/app.log
```

#### 2. Test Slack Webhook
```bash
# Send test Slack event
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d @samples/slack/slack_message_new.json

# Check response and logs
```

#### 3. Test Escalation Rules
```bash
# Create case and wait 48+ hours (or modify test)
# Send urgent message
# Check escalation alerts in Slack
```

#### 4. Test Case Closure
```bash
# Send admin message with closure phrase
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d @samples/slack/slack_admin_close.json

# Verify case closure in database
```

### Sample Data
Located in `samples/` directory:
- **Email samples**: `samples/emails/`
- **Slack events**: `samples/slack/`
- **Test scripts**: `scripts/`

---

## Assignment Requirements Mapping

This implementation satisfies all assignment requirements:

### ✅ Case Creation
- **File**: `app/slack_listener.py`, `app/email_listener.py`
- **Logic**: `get_or_create_case()` in `db.py`
- **Implementation**: Extracts customer identifier, creates new case if none exists for open cases

### ✅ Case Escalation
- **File**: `app/scheduler.py`, `app/utils.py`
- **Rules**:
  1. **48h rule**: `check_time_escalation()` in `utils.py`
  2. **3+ follow-ups**: `check_followup_escalation()` in `utils.py`
  3. **Keywords**: `detect_urgent_keywords()` in `utils.py`
- **Alerts**: Sent to `ALERTING_SLACK_CHANNEL`

### ✅ Case Closure
- **File**: `app/slack_listener.py`, `app/email_listener.py`
- **Detection**: `detect_closure_phrase()` in `utils.py`
- **Logging**: Format: "Case #<CASE_ID> closed at <ISO_TIMESTAMP> by <admin>"
- **Future messages**: Create new cases for closed case customers

### ✅ Email & Slack Ingestion
- **Email**: `app/email_listener.py` - IMAP polling implementation
- **Slack**: `app/slack_listener.py` - Webhook event processing
- **Channels**: Support, alerting, and logging Slack channels

### ✅ Implementation Details
- **Python only**: No external no-code platforms
- **Self-contained**: All code included, no proprietary dependencies
- **Third-party**: Slack SDK and IMAP libraries documented
- **Configuration**: Environment variables and sample files provided

### ✅ Testing & Deployment
- **Unit tests**: `tests/test_case_logic.py` with pytest
- **Docker**: `Dockerfile` and `docker-compose.yml`
- **Demo script**: `demo_run.sh` for automated testing
- **Sample data**: Email and Slack event examples

### ✅ Documentation
- **Architecture**: Mermaid diagram and component descriptions
- **Data model**: Complete table definitions
- **API documentation**: Endpoint specifications
- **Deployment guide**: Docker and manual installation instructions

---

## Development Notes

### Assumptions and Limitations
1. **Email parsing**: Assumes standard email formats, may not handle complex HTML emails perfectly
2. **Slack threads**: Simplified thread handling, assumes linear conversation flow
3. **Admin identification**: Based on predefined lists, no dynamic role management
4. **Database**: Uses timestamp comparison for time-based escalation (timezone aware)
5. **Message size**: Limited to 10KB per message for performance

### Future Enhancements
1. **Message threading**: Full Slack thread support for complex conversations
2. **Rich formatting**: HTML email content parsing and formatting
3. **Admin interface**: Web dashboard for case management
4. **Metrics**: Prometheus metrics for monitoring
5. **Multi-tenancy**: Support for multiple organizations

### Performance Considerations
1. **Database indexing**: Indexes on customer_identifier and status for fast queries
2. **Connection pooling**: SQLAlchemy connection pooling for database efficiency
3. **Message queuing**: Potential Redis integration for high-volume scenarios
4. **Caching**: Case lookup caching for frequently accessed customers

---

## Support and Maintenance

### Monitoring
- Health check endpoint: `/health`
- Structured logging with configurable levels
- Docker health checks for container orchestration

### Troubleshooting
1. **Check logs**: `docker-compose logs app`
2. **Verify configuration**: Ensure all environment variables are set
3. **Test connections**: Use provided test scripts
4. **Database inspection**: Use pgAdmin (development) or direct queries

### Backup and Recovery
- Database backups: Use PostgreSQL `pg_dump`
- Configuration: Backup `.env` file
- Logs: Archive log files regularly

---

*This PRD serves as the complete specification and implementation guide for the case management system. All code components are included and documented for evaluation.*
