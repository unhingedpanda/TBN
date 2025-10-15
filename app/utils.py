"""
Utility functions for the case management system.

Contains helper functions for parsing messages, detecting keywords,
identifying admins, and checking escalation conditions.
"""

import re
import hmac
import hashlib
import logging
import time
import structlog
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from .db import Case, Message

logger = structlog.get_logger(__name__)


def parse_customer_identifier(message_body: str, source: str, sender: str) -> str:
    """
    Extract customer identifier from message.

    For email: Use the sender email address
    For Slack: Use the user ID from the sender field

    Args:
        message_body: The message content
        source: Message source ("email" or "slack")
        sender: Original sender identifier

    Returns:
        str: Customer identifier (email or Slack user ID)
    """
    if source == "email":
        # For email, sender should already be the email address
        return sender
    elif source == "slack":
        # For Slack, sender should be the user ID
        return sender
    else:
        logger.warning("Unknown message source", source=source, sender=sender)
        return sender


def detect_urgent_keywords(message_body: str) -> bool:
    """
    Check if message contains urgent keywords.

    Args:
        message_body: The message content to check

    Returns:
        bool: True if urgent keywords are found
    """
    urgent_keywords = ["urgent", "immediately", "asap", "emergency", "critical"]
    message_lower = message_body.lower()

    return any(keyword in message_lower for keyword in urgent_keywords)


def is_admin_message(sender: str, admin_identifiers: List[str]) -> bool:
    """
    Check if a message is from an admin.

    Args:
        sender: Sender identifier (email or Slack user ID)
        admin_identifiers: List of admin identifiers

    Returns:
        bool: True if sender is an admin
    """
    return sender.lower() in [admin_id.lower() for admin_id in admin_identifiers]


def detect_closure_phrase(message_body: str) -> bool:
    """
    Check if message contains case closure phrase.

    Args:
        message_body: The message content to check

    Returns:
        bool: True if closure phrase is detected
    """
    # Case insensitive match for the exact phrase
    closure_patterns = [
        r"i'm closing this case\.",
        r"i am closing this case\.",
        r"closing this case\.",
        r"case closed\.",
        r"i'll close this case\.",
    ]

    message_lower = message_body.lower()

    return any(re.search(pattern, message_lower) for pattern in closure_patterns)


def check_time_escalation(case: Case, escalation_hours: int = 48) -> bool:
    """
    Check if case should be escalated due to time elapsed.

    Args:
        case: The case to check
        escalation_hours: Hours after which to escalate (default: 48)

    Returns:
        bool: True if case should be escalated
    """
    if case.status != "open":
        return False

    time_threshold = datetime.utcnow() - timedelta(hours=escalation_hours)
    return case.last_message_at < time_threshold


def check_followup_escalation(case: Case, max_followups: int = 3) -> bool:
    """
    Check if case should be escalated due to consecutive customer follow-ups (no admin replies in between).

    Args:
        case: The case to check
        max_followups: Maximum consecutive customer messages before escalation

    Returns:
        bool: True if case should be escalated
    """
    if case.status != "open":
        return False

    # Get recent messages in chronological order
    recent_messages = case.messages[-max_followups-1:]  # Get last N+1 messages

    if len(recent_messages) < max_followups:
        return False

    # Check if last max_followups messages are all from customer (non-admin)
    consecutive_customer_messages = 0
    for message in reversed(recent_messages):
        if not message.is_admin:
            consecutive_customer_messages += 1
        else:
            # Reset counter if we hit an admin message
            consecutive_customer_messages = 0

        if consecutive_customer_messages >= max_followups:
            return True

    return False


def get_escalation_reasons(case: Case, message_body: str) -> List[str]:
    """
    Return all escalation reasons that apply to the given case and message.

    Args:
        case: The case to evaluate
        message_body: The content of the new message

    Returns:
        List[str]: A list of human-readable reasons. Empty if none apply.
    """
    reasons: List[str] = []

    # Urgent keywords in the incoming message trigger immediate escalation
    if detect_urgent_keywords(message_body):
        reasons.append("Urgent keywords detected in message")

    # Time-based inactivity
    if check_time_escalation(case):
        reasons.append(f"Inactive for more than {48} hours")

    # Follow-up escalation: multiple customer messages without admin reply
    if check_followup_escalation(case):
        reasons.append(f"More than {3} follow-ups without admin reply")

    return reasons


def format_slack_message(case_id: str, message_body: str, customer_name: str = "Customer") -> str:
    """
    Format a message for Slack notification.

    Args:
        case_id: The case ID
        message_body: The message content
        customer_name: Display name for the customer

    Returns:
        str: Formatted message for Slack
    """
    # Truncate message if too long
    max_length = 200
    display_body = message_body[:max_length]
    if len(message_body) > max_length:
        display_body += "..."

    return f"*{customer_name}* (Case #{case_id[:12]}...):\n{display_body}"


def format_escalation_alert(case_id: str, reason: str, customer_identifier: str) -> str:
    """
    Format an escalation alert for Slack.

    Args:
        case_id: The case ID
        reason: Reason for escalation
        customer_identifier: Customer identifier

    Returns:
        str: Formatted escalation alert
    """
    return f"🚨 *ESCALATION ALERT*\nCase #{case_id} for {customer_identifier}\nReason: {reason}"


def format_closure_log(case_id: str, admin_identifier: str) -> str:
    """
    Format a case closure log message.

    Args:
        case_id: The case ID
        admin_identifier: The admin who closed the case

    Returns:
        str: Formatted closure log message
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"Case #{case_id} closed at {timestamp} by {admin_identifier}"


def extract_slack_user_id(slack_event: dict) -> Optional[str]:
    """
    Extract user ID from Slack event.

    Args:
        slack_event: Slack event payload

    Returns:
        Optional[str]: User ID or None if not found
    """
    # Try different possible locations for user ID in Slack events
    if slack_event.get("event", {}).get("user"):
        return slack_event["event"]["user"]
    elif slack_event.get("user", {}).get("id"):
        return slack_event["user"]["id"]
    elif slack_event.get("event", {}).get("bot_id"):
        return slack_event["event"]["bot_id"]

    return None


def extract_slack_message_text(slack_event: dict) -> Optional[str]:
    """
    Extract message text from Slack event.

    Args:
        slack_event: Slack event payload

    Returns:
        Optional[str]: Message text or None if not found
    """
    # Try different possible locations for message text
    event = slack_event.get("event", {})

    if event.get("text"):
        return event["text"]
    elif event.get("message", {}).get("text"):
        return event["message"]["text"]
    elif slack_event.get("text"):
        return slack_event["text"]

    return None


def is_slack_bot_message(slack_event: dict) -> bool:
    """
    Check if Slack event is from a bot.

    Args:
        slack_event: Slack event payload

    Returns:
        bool: True if message is from a bot
    """
    event = slack_event.get("event", {})
    return event.get("bot_id") is not None or event.get("subtype") == "bot_message"


def sanitize_message_body(message_body: str) -> str:
    """
    Sanitize message body for storage.

    Args:
        message_body: Raw message body

    Returns:
        str: Sanitized message body
    """
    if not message_body:
        return ""

    # Basic sanitization - remove null bytes and control characters
    sanitized = message_body.replace('\x00', '').strip()

    # Limit length to prevent database issues
    max_length = 10000
    if len(sanitized) > max_length:
        logger.warning("Message body truncated", original_length=len(sanitized), max_length=max_length)
        sanitized = sanitized[:max_length] + "... [truncated]"

    return sanitized


def verify_slack_signature(raw_body: bytes, timestamp: str, signature: str, signing_secret: str) -> bool:
    """
    Verify Slack webhook signature to prevent forged requests.

    Args:
        raw_body: Raw request body as bytes
        timestamp: Request timestamp from X-Slack-Request-Timestamp header
        signature: Request signature from X-Slack-Signature header
        signing_secret: Slack signing secret from environment

    Returns:
        bool: True if signature is valid

    Raises:
        ValueError: If timestamp is too old or signature format is invalid
    """
    if not signing_secret:
        logger.error("SLACK_SIGNING_SECRET not configured")
        return False

    # Check if timestamp is within 5 minutes to prevent replay attacks
    try:
        request_time = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - request_time) > 60 * 5:
            logger.warning("Request timestamp too old", timestamp=timestamp, current_time=current_time)
            return False
    except (ValueError, TypeError):
        logger.error("Invalid timestamp format", timestamp=timestamp)
        return False

    # Create the basestring (timestamp:body)
    basestring = f"v0:{timestamp}:".encode('utf-8') + raw_body

    # Create expected signature
    expected_signature = hmac.new(
        signing_secret.encode('utf-8'),
        basestring,
        hashlib.sha256
    ).hexdigest()
    expected_signature = f"v0={expected_signature}"

    # Use constant time comparison to prevent timing attacks
    return hmac.compare_digest(expected_signature, signature)


def get_slack_headers(request) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract Slack signature headers from request.

    Args:
        request: FastAPI request object

    Returns:
        Tuple[Optional[str], Optional[str]]: (timestamp, signature)
    """
    return (
        request.headers.get("X-Slack-Request-Timestamp"),
        request.headers.get("X-Slack-Signature")
    )
