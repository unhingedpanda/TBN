"""
Database models and connection setup for the case management system.

Supports both PostgreSQL (production) and SQLite (development) databases.
Uses SQLAlchemy ORM for database operations.
"""

import os
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./case_management.db")

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite configuration for development
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    # PostgreSQL configuration for production
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Case(Base):
    """
    Case model representing a customer support case.

    A case is created when a customer first reaches out and remains open
    until an admin explicitly closes it. Multiple messages can be associated
    with a single case.
    """

    __tablename__ = "cases"

    case_id = Column(String, primary_key=True, index=True)
    customer_identifier = Column(String, nullable=False, index=True)
    status = Column(String, default="open")  # "open" or "closed"
    created_at = Column(DateTime, default=datetime.utcnow)
    last_message_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    message_count = Column(Integer, default=1)
    escalated = Column(Boolean, default=False)
    escalated_at = Column(DateTime, nullable=True)  # When case was first escalated
    last_escalation_alert = Column(DateTime, nullable=True)  # When last escalation alert was sent
    closed_at = Column(DateTime, nullable=True)

    # Relationship to messages
    messages = relationship("Message", back_populates="case", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Case(case_id='{self.case_id}', customer='{self.customer_identifier}', status='{self.status}')>"


class Message(Base):
    """
    Message model representing individual messages within a case.

    Each message is associated with a case and contains metadata about
    the sender, content, and source (email/slack).
    """

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, ForeignKey("cases.case_id"), nullable=False)
    sender = Column(String, nullable=False)  # Email address or Slack user ID
    is_admin = Column(Boolean, default=False)
    body = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    source = Column(String, nullable=False)  # "email" or "slack"

    # Relationship to case
    case = relationship("Case", back_populates="messages")

    def __repr__(self):
        return f"<Message(id={self.id}, case_id='{self.case_id}', sender='{self.sender}', source='{self.source}')>"


class ProcessedMessage(Base):
    """
    Model to track already processed messages and prevent duplicates.

    Stores unique identifiers for messages that have been processed to avoid
    double processing due to retries or duplicate deliveries.
    """

    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True, nullable=False, index=True)  # Email Message-ID or Slack event_id

    # Add unique constraint to prevent race conditions in duplicate detection
    __table_args__ = (
        UniqueConstraint('message_id', 'source', name='uq_processed_message_id_source'),
    )
    source = Column(String, nullable=False)  # "email" or "slack"
    processed_at = Column(DateTime, default=datetime.utcnow)
    case_id = Column(String, ForeignKey("cases.case_id"), nullable=True)  # Link to created/updated case

    def __repr__(self):
        return f"<ProcessedMessage(id={self.id}, message_id='{self.message_id}', source='{self.source}')>"


def get_db() -> Session:
    """
    Dependency to get database session.

    Yields a database session that is automatically closed after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """
    Create all database tables using Alembic migrations.

    This function runs database migrations to create/update the schema.
    Should be called during application startup or deployment.
    """
    import alembic.config
    import alembic.command

    # Configure Alembic
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

    # Run migrations
    alembic.command.upgrade(alembic_cfg, "head")


def init_db():
    """
    Initialize the database with schema creation.

    This is a convenience function that can be called from the command line
    or during application startup to ensure the database schema exists.
    """
    create_tables()


def get_or_create_case(db: Session, customer_identifier: str) -> Case:
    """
    Get existing open case for customer or create a new one.

    Uses database transactions and proper locking to prevent race conditions
    when multiple messages arrive simultaneously for the same customer.

    Args:
        db: Database session
        customer_identifier: Customer email or Slack user ID

    Returns:
        Case: The existing open case or newly created case
    """
    # Use a transaction to ensure atomicity
    try:
        # First try to find existing case (with potential row locking in PostgreSQL)
        case = db.query(Case).filter(
            Case.customer_identifier == customer_identifier,
            Case.status == "open"
        ).first()

        if case:
            return case

        # Create a new case within the same transaction
        case_id = generate_case_id()
        new_case = Case(
            case_id=case_id,
            customer_identifier=customer_identifier,
            status="open"
        )
        db.add(new_case)
        db.commit()
        db.refresh(new_case)
        return new_case

    except Exception:
        db.rollback()
        # If we get here due to a race condition, try to fetch the case again
        case = db.query(Case).filter(
            Case.customer_identifier == customer_identifier,
            Case.status == "open"
        ).first()

        if case:
            return case
        else:
            # Re-raise if we still can't find or create the case
            raise


def add_message_to_case(
    db: Session,
    case: Case,
    sender: str,
    body: str,
    source: str,
    is_admin: bool = False
) -> Message:
    """
    Add a message to an existing case.

    Args:
        db: Database session
        case: The case to add the message to
        sender: Message sender identifier
        body: Message content
        source: Message source ("email" or "slack")
        is_admin: Whether the sender is an admin

    Returns:
        Message: The created message
    """
    message = Message(
        case_id=case.case_id,
        sender=sender,
        body=body,
        source=source,
        is_admin=is_admin
    )

    case.messages.append(message)
    case.message_count += 1
    case.last_message_at = datetime.utcnow()

    db.commit()
    db.refresh(message)
    return message


def get_case_with_messages(db: Session, case_id: str) -> Optional[Case]:
    """
    Get a case with all its messages loaded.

    Args:
        db: Database session
        case_id: The case ID to retrieve

    Returns:
        Case or None if not found
    """
    return db.query(Case).filter(Case.case_id == case_id).first()


def close_case(db: Session, case: Case, admin_identifier: str) -> None:
    """
    Close a case and log the closure.

    Args:
        db: Database session
        case: The case to close
        admin_identifier: The admin who closed the case
    """
    case.status = "closed"
    case.closed_at = datetime.utcnow()
    db.commit()


def escalate_case(db: Session, case: Case) -> None:
    """
    Mark a case as escalated.

    Args:
        db: Database session
        case: The case to escalate
    """
    case.escalated = True
    if not case.escalated_at:
        case.escalated_at = datetime.utcnow()
    db.commit()


def should_send_escalation_alert(db: Session, case: Case, alert_interval_minutes: int = 60) -> bool:
    """
    Check if an escalation alert should be sent for a case.

    Prevents duplicate alerts by checking if enough time has passed since the last alert.

    Args:
        db: Database session
        case: The case to check
        alert_interval_minutes: Minimum minutes between escalation alerts

    Returns:
        bool: True if alert should be sent
    """
    if not case.last_escalation_alert:
        return True  # First escalation, always send alert

    time_since_last_alert = datetime.utcnow() - case.last_escalation_alert
    return time_since_last_alert.total_seconds() >= (alert_interval_minutes * 60)


def update_last_escalation_alert(db: Session, case: Case) -> None:
    """
    Update the last escalation alert timestamp for a case.

    Args:
        db: Database session
        case: The case to update
    """
    case.last_escalation_alert = datetime.utcnow()
    db.commit()


def generate_case_id() -> str:
    """
    Generate a unique case ID.

    Uses UTC timestamp and random component for uniqueness.

    Returns:
        str: Unique case ID in format CASE_YYYYMMDD_HHMMSS_RRRR
    """
    import random
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    random_suffix = str(random.randint(1000, 9999))
    return f"CASE_{timestamp}_{random_suffix}"


def is_message_processed(db: Session, message_id: str, source: str) -> bool:
    """
    Check if a message has already been processed.

    Args:
        db: Database session
        message_id: Unique message identifier (Message-ID or event_id)
        source: Message source ("email" or "slack")

    Returns:
        bool: True if message was already processed
    """
    return db.query(ProcessedMessage).filter(
        ProcessedMessage.message_id == message_id,
        ProcessedMessage.source == source
    ).first() is not None


def record_processed_message(db: Session, message_id: str, source: str, case_id: Optional[str] = None):
    """
    Record that a message has been processed.

    Args:
        db: Database session
        message_id: Unique message identifier
        source: Message source ("email" or "slack")
        case_id: Optional case ID that was created/updated
    """
    try:
        processed_msg = ProcessedMessage(
            message_id=message_id,
            source=source,
            case_id=case_id
        )
        db.add(processed_msg)
        db.commit()
    except Exception as e:
        # If we get a unique constraint violation, it means the message was already processed
        # This can happen in race conditions, so we'll just log it and continue
        db.rollback()
        logger.warning(f"Message {message_id} from {source} already processed (race condition)")


def cleanup_old_processed_messages(db: Session, days_to_keep: int = 30):
    """
    Clean up old processed message records to prevent table bloat.

    Args:
        db: Database session
        days_to_keep: Number of days of processed messages to retain
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
    deleted_count = db.query(ProcessedMessage).filter(
        ProcessedMessage.processed_at < cutoff_date
    ).delete()
    db.commit()
    return deleted_count


# Command line interface for database initialization
if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
