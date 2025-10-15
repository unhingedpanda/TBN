"""
Unit tests for case management logic.

Tests case creation, escalation detection, closure detection,
and other core business logic.
"""

import pytest
from datetime import datetime, timedelta
from freezegun import freeze_time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, Case, Message, SessionLocal, get_or_create_case, add_message_to_case, close_case, escalate_case
from app.utils import (
    detect_urgent_keywords,
    is_admin_message,
    detect_closure_phrase,
    check_time_escalation,
    check_followup_escalation,
    should_escalate_case,
    format_slack_message,
    format_escalation_alert,
    format_closure_log
)


# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_case_management.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    """Create test database session."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_case(db):
    """Create a sample case for testing."""
    case = Case(
        case_id="TEST_001",
        customer_identifier="test@example.com",
        status="open"
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@pytest.fixture
def admin_identifiers():
    """Sample admin identifiers for testing."""
    return ["admin@example.com", "manager@company.com"]


class TestCaseCreation:
    """Test case creation and management."""

    def test_get_or_create_case_new(self, db):
        """Test creating a new case for a customer."""
        case = get_or_create_case(db, "newcustomer@example.com")

        assert case.customer_identifier == "newcustomer@example.com"
        assert case.status == "open"
        assert case.message_count == 1  # Should start with 1 for the creation message
        assert not case.escalated

    def test_get_or_create_case_existing(self, db, sample_case):
        """Test getting existing open case."""
        case = get_or_create_case(db, "test@example.com")

        assert case.case_id == sample_case.case_id
        assert case.status == "open"

    def test_add_message_to_case(self, db, sample_case):
        """Test adding a message to an existing case."""
        message = add_message_to_case(
            db=db,
            case=sample_case,
            sender="customer@example.com",
            body="Test message",
            source="email",
            is_admin=False
        )

        assert message.body == "Test message"
        assert message.source == "email"
        assert not message.is_admin
        assert sample_case.message_count == 2  # Started with 1, now 2


class TestEscalationDetection:
    """Test escalation rule detection."""

    def test_detect_urgent_keywords(self):
        """Test urgent keyword detection."""
        assert detect_urgent_keywords("This is urgent and needs immediate attention")
        assert detect_urgent_keywords("Please handle this immediately")
        assert detect_urgent_keywords("EMERGENCY: System down")
        assert not detect_urgent_keywords("Normal message")
        assert not detect_urgent_keywords("Please check this when you have time")

    def test_is_admin_message(self, admin_identifiers):
        """Test admin message detection."""
        assert is_admin_message("admin@example.com", admin_identifiers)
        assert is_admin_message("ADMIN@EXAMPLE.COM", admin_identifiers)  # Case insensitive
        assert not is_admin_message("customer@example.com", admin_identifiers)

    def test_detect_closure_phrase(self):
        """Test case closure phrase detection."""
        assert detect_closure_phrase("I'm closing this case.")
        assert detect_closure_phrase("I am closing this case.")
        assert detect_closure_phrase("Case closed.")
        assert not detect_closure_phrase("This case needs to be closed")
        assert not detect_closure_phrase("Please close the case")

    def test_check_time_escalation(self, db, sample_case):
        """Test time-based escalation."""
        # Set last_message_at to more than 48 hours ago
        sample_case.last_message_at = datetime.utcnow() - timedelta(hours=50)
        db.commit()

        assert check_time_escalation(sample_case)

        # Test case that's not old enough
        sample_case.last_message_at = datetime.utcnow() - timedelta(hours=24)
        db.commit()

        assert not check_time_escalation(sample_case)

        # Test closed case (should not escalate)
        sample_case.status = "closed"
        db.commit()

        assert not check_time_escalation(sample_case)

    def test_time_escalation_with_freezegun(self, db, sample_case):
        """Test time-based escalation using freezegun for deterministic testing."""
        # Set case creation time to 50 hours ago
        sample_case.created_at = datetime.utcnow() - timedelta(hours=50)
        sample_case.last_message_at = datetime.utcnow() - timedelta(hours=50)
        db.commit()

        # With current time, case should be escalated
        assert check_time_escalation(sample_case)

        # Test with time frozen to 25 hours ago (should not escalate)
        with freeze_time(datetime.utcnow() - timedelta(hours=25)):
            assert not check_time_escalation(sample_case)

        # Test with time frozen to 49 hours ago (should escalate)
        with freeze_time(datetime.utcnow() - timedelta(hours=49)):
            assert check_time_escalation(sample_case)

    def test_check_followup_escalation(self, db, sample_case):
        """Test follow-up escalation detection."""
        # Add 4 consecutive customer messages
        for i in range(4):
            add_message_to_case(
                db=db,
                case=sample_case,
                sender="customer@example.com",
                body=f"Follow-up message {i+1}",
                source="email",
                is_admin=False
            )

        assert check_followup_escalation(sample_case)

        # Test with admin message in between (should reset counter)
        add_message_to_case(
            db=db,
            case=sample_case,
            sender="admin@example.com",
            body="Admin response",
            source="email",
            is_admin=True
        )

        # Add 2 more customer messages (should not escalate yet)
        for i in range(2):
            add_message_to_case(
                db=db,
                case=sample_case,
                sender="customer@example.com",
                body=f"Another follow-up {i+1}",
                source="email",
                is_admin=False
            )

        assert not check_followup_escalation(sample_case)

    def test_should_escalate_case(self, db, sample_case, admin_identifiers):
        """Test overall escalation logic."""
        # Test urgent keyword escalation
        should_escalate, reason = should_escalate_case(
            sample_case, "This is urgent", admin_identifiers
        )
        assert should_escalate
        assert "urgent" in reason.lower()

        # Test time escalation
        sample_case.last_message_at = datetime.utcnow() - timedelta(hours=50)
        should_escalate, reason = should_escalate_case(
            sample_case, "Normal message", admin_identifiers
        )
        assert should_escalate
        assert "48" in reason

        # Test no escalation needed
        sample_case.last_message_at = datetime.utcnow() - timedelta(hours=1)
        should_escalate, reason = should_escalate_case(
            sample_case, "Normal message", admin_identifiers
        )
        assert not should_escalate


class TestMessageHandling:
    """Test message processing and case updates."""

    def test_close_case(self, db, sample_case):
        """Test case closure."""
        admin_id = "admin@example.com"

        close_case(db, sample_case, admin_id)

        assert sample_case.status == "closed"
        assert sample_case.closed_at is not None

    def test_escalate_case(self, db, sample_case):
        """Test case escalation."""
        assert not sample_case.escalated

        escalate_case(db, sample_case)

        assert sample_case.escalated


class TestFormatting:
    """Test message formatting functions."""

    def test_format_slack_message(self):
        """Test Slack message formatting."""
        message = format_slack_message(
            "CASE_001",
            "This is a test message with some content",
            "John Doe"
        )

        assert "John Doe" in message
        assert "CASE_001" in message
        assert "This is a test message" in message

    def test_format_escalation_alert(self):
        """Test escalation alert formatting."""
        alert = format_escalation_alert(
            "CASE_001",
            "Urgent keywords detected",
            "customer@example.com"
        )

        assert "ESCALATION ALERT" in alert
        assert "CASE_001" in alert
        assert "customer@example.com" in alert
        assert "Urgent keywords" in alert

    def test_format_closure_log(self):
        """Test closure log formatting."""
        log = format_closure_log("CASE_001", "admin@example.com")

        assert "CASE_001" in log
        assert "closed at" in log
        assert "admin@example.com" in log


class TestIntegration:
    """Integration tests for complete workflows."""

    def test_customer_message_workflow(self, db, admin_identifiers):
        """Test complete customer message workflow."""
        # Create case with initial message
        case = get_or_create_case(db, "customer@example.com")

        # Add customer message
        message = add_message_to_case(
            db=db,
            case=case,
            sender="customer@example.com",
            body="Hello, I need help",
            source="email",
            is_admin=False
        )

        # Check no escalation yet
        should_escalate, _ = should_escalate_case(case, "Normal message", admin_identifiers)
        assert not should_escalate

        # Add urgent message (should escalate)
        urgent_message = add_message_to_case(
            db=db,
            case=case,
            sender="customer@example.com",
            body="This is urgent!",
            source="email",
            is_admin=False
        )

        should_escalate, reason = should_escalate_case(case, "This is urgent!", admin_identifiers)
        assert should_escalate
        assert "urgent" in reason.lower()

    def test_admin_closure_workflow(self, db, sample_case):
        """Test admin case closure workflow."""
        # Add some messages first
        for i in range(3):
            add_message_to_case(
                db=db,
                case=sample_case,
                sender="customer@example.com",
                body=f"Message {i+1}",
                source="email",
                is_admin=False
            )

        # Admin closes case
        close_case(db, sample_case, "admin@example.com")

        assert sample_case.status == "closed"
        assert sample_case.closed_at is not None

        # New message from same customer should create new case
        new_case = get_or_create_case(db, "customer@example.com")
        assert new_case.case_id != sample_case.case_id
        assert new_case.status == "open"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
