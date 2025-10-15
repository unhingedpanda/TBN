# Master Prompt - Case Management System Development

## Original Request (October 14, 2025)

You are an expert software engineer and technical writer. I want to build a **self-hosted Python case-management & escalation system** that exactly satisfies the submission guidelines for this assignment and produces a GitHub repository ready for evaluation.

**Project goal (brief):**
Ingest customer messages via **email** and **Slack**, create/track cases, escalate when rules are met, detect admin closure, send Slack notifications and logging, and persist all data. This will be implemented **only in Python** (no n8n or other no-code platforms). The final repo must include a complete PRD/implementation document (Markdown), runnable source code, tests, deployment instructions for self-hosting (Docker + docker-compose), and exported AI conversations used while developing (in `ai_conversations/`).

## Key Development Decisions

### Architecture Choices
1. **Framework Selection**: FastAPI for the web server due to its async capabilities, automatic OpenAPI documentation, and type safety
2. **Database**: SQLAlchemy ORM with PostgreSQL for production and SQLite for development
3. **Email Processing**: Python's built-in `imaplib` for IMAP polling
4. **Slack Integration**: `python-slack-sdk` for webhook handling and messaging
5. **Scheduling**: APScheduler for background escalation checks

### Component Structure
- `main.py`: FastAPI application server
- `db.py`: Database models and operations
- `email_listener.py`: IMAP email polling and processing
- `slack_listener.py`: Slack webhook event handling
- `scheduler.py`: Background job processing for escalations
- `utils.py`: Business logic utilities
- `notifications.py`: Slack messaging service

### Implementation Approach
1. **Database-First**: Started with SQLAlchemy models to establish data structure
2. **Component Isolation**: Each component implemented independently with clear interfaces
3. **Configuration-Driven**: Environment variables for all settings
4. **Error Handling**: Comprehensive error handling and logging throughout

## Technical Specifications

### Database Schema
- `cases` table: Case metadata and state management
- `messages` table: Individual messages linked to cases
- Proper indexing for performance on customer lookups

### Escalation Rules Implementation
1. **Time-based**: 48-hour threshold using timestamp comparison
2. **Follow-up counting**: Consecutive customer message tracking
3. **Keyword detection**: Case-insensitive pattern matching

### Message Processing Pipeline
1. **Ingestion**: Email IMAP polling or Slack webhook reception
2. **Parsing**: Extract sender, content, and metadata
3. **Case Logic**: Create new cases or append to existing ones
4. **Escalation Check**: Evaluate against all escalation rules
5. **Notification**: Send appropriate Slack messages

## Development Workflow

### Phase 1: Foundation (Completed)
- Project skeleton and directory structure
- Database models and schema design
- Core utility functions for business logic

### Phase 2: Core Components (Completed)
- FastAPI main application
- Email listener implementation
- Slack webhook handler
- Background scheduler for escalations

### Phase 3: Integration (Completed)
- Notification service for Slack messaging
- Comprehensive unit tests
- Sample data and demo scripts
- Docker containerization

### Phase 4: Documentation (Completed)
- Complete PRD with architecture diagrams
- Deployment instructions
- Testing procedures
- Assignment requirements mapping

## Key Technical Challenges Resolved

### 1. Multi-Channel Message Ingestion
**Challenge**: Handle both email (IMAP) and Slack (webhooks) with different data formats
**Solution**: Abstract message processing logic into common functions, handle format differences in listeners

### 2. Case Threading and State Management
**Challenge**: Maintain conversation context across multiple messages and channels
**Solution**: Customer identifier-based case lookup, message appending to existing open cases

### 3. Escalation Rule Complexity
**Challenge**: Implement three different escalation triggers with proper timing and counting
**Solution**: Modular escalation checking functions, background scheduler for periodic evaluation

### 4. Admin Detection and Closure
**Challenge**: Identify admin messages and detect closure commands across both channels
**Solution**: Configurable admin lists, pattern matching for closure phrases

### 5. Notification Management
**Challenge**: Send formatted messages to multiple Slack channels
**Solution**: Centralized notification service with channel-specific formatting

## Code Quality Standards

- **Type Hints**: Full type annotation throughout codebase
- **Docstrings**: Comprehensive documentation for all functions and classes
- **Error Handling**: Proper exception handling with logging
- **Configuration**: Environment-based configuration with validation
- **Testing**: Unit tests for all critical business logic
- **Logging**: Structured logging for debugging and monitoring

## Deployment Strategy

- **Docker**: Multi-stage builds for optimal image size
- **Docker Compose**: Complete stack with PostgreSQL
- **Production Ready**: Includes nginx reverse proxy configuration
- **Development Support**: SQLite fallback and debug endpoints

## Testing Approach

- **Unit Tests**: Business logic validation with pytest
- **Integration Tests**: Full workflow testing with sample data
- **Manual Testing**: Demo scripts for systematic validation
- **CI/CD Ready**: Test automation for future development

This master prompt guided the development of a complete, production-ready case management system that meets all assignment requirements while following software engineering best practices.
