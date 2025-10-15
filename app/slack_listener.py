"""
Slack webhook listener for processing incoming Slack events.

Handles message events from Slack, creates/updates cases, detects closure commands,
and sends appropriate notifications.
"""

import asyncio
import os
import logging
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from .db import get_db, get_or_create_case, add_message_to_case, close_case, is_message_processed, record_processed_message, SessionLocal
from .utils import (
    extract_slack_user_id,
    extract_slack_message_text,
    is_slack_bot_message,
    is_admin_message,
    detect_closure_phrase,
    get_escalation_reasons,
    format_slack_message,
    sanitize_message_body
)
from .notifications import send_support_notification, send_closure_log

logger = logging.getLogger(__name__)


def get_admin_identifiers() -> list:
    """
    Get list of admin identifiers from environment.

    Returns:
        list: List of admin email addresses and Slack user IDs
    """
    admin_emails = os.getenv("ADMIN_EMAILS", "")
    admin_slack_ids = os.getenv("ADMIN_SLACK_IDS", "")

    admins = []

    # Add admin emails
    if admin_emails:
        admins.extend([email.strip() for email in admin_emails.split(",") if email.strip()])

    # Add admin Slack IDs
    if admin_slack_ids:
        admins.extend([slack_id.strip() for slack_id in admin_slack_ids.split(",") if slack_id.strip()])

    return admins


async def process_slack_event(event_data: dict) -> bool:
    """
    Process a Slack event and handle case management.

    Args:
        event_data: Slack event payload

    Returns:
        bool: True if event was processed, False if ignored or already processed
    """
    logger.info(f"Processing Slack event: {event_data.get('type', 'unknown')}")

    # Get event_id for deduplication
    event_id = event_data.get("event_id")
    if not event_id:
        logger.warning("Missing event_id in Slack event")
        return False

    # Check for duplicate processing
    db = SessionLocal()
    try:
        if is_message_processed(db, event_id, "slack"):
            logger.info(f"Slack event {event_id} already processed, skipping")
            return False
    finally:
        db.close()

    # Handle URL verification for Slack app setup
    if event_data.get("type") == "url_verification":
        return handle_url_verification(event_data)

    # Only process message events
    if not is_message_event(event_data):
        logger.debug("Ignoring non-message event")
        return False

    # Extract message data
    user_id, message_text, channel_id = extract_message_data(event_data)
    if not user_id or not message_text:
        logger.debug("Missing user_id or message_text")
        return False

    # Ignore bot messages
    if is_slack_bot_message(event_data):
        logger.debug("Ignoring bot message")
        return False

    # Process the message
    success = await process_customer_message(user_id, message_text, channel_id, event_id)

    if success:
        logger.info(f"Successfully processed Slack message from {user_id}")
    else:
        logger.error(f"Failed to process Slack message from {user_id}")

    return success


def handle_url_verification(event_data: dict) -> bool:
    """
    Handle Slack URL verification challenge.

    Args:
        event_data: Slack event with challenge

    Returns:
        bool: Always True for URL verification
    """
    # Slack expects the challenge token to be returned as-is
    challenge = event_data.get("challenge")
    if challenge:
        print(challenge)  # Print for FastAPI to return
        return True
    return False


def is_message_event(event_data: dict) -> bool:
    """
    Check if the event is a message event we should process.

    Args:
        event_data: Slack event payload

    Returns:
        bool: True if it's a processable message event
    """
    event = event_data.get("event", {})

    # Check if it's a message event
    if event.get("type") != "message":
        return False

    # Ignore message subtypes we don't want to process
    subtype = event.get("subtype")
    ignored_subtypes = ["channel_join", "channel_leave", "bot_message", "message_deleted"]
    if subtype in ignored_subtypes:
        return False

    return True


def extract_message_data(event_data: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract user ID, message text, and channel ID from Slack event.

    Args:
        event_data: Slack event payload

    Returns:
        Tuple[Optional[str], Optional[str], Optional[str]]: (user_id, message_text, channel_id)
    """
    event = event_data.get("event", {})

    # Extract user ID
    user_id = extract_slack_user_id(event_data)

    # Extract message text
    message_text = extract_slack_message_text(event_data)

    # Extract channel ID
    channel_id = event.get("channel")

    return user_id, message_text, channel_id


async def process_customer_message(user_id: str, message_text: str, channel_id: str, event_id: str) -> bool:
    """
    Process a customer message from Slack.

    This handles the core case management logic:
    1. Identify if sender is admin or customer
    2. Create or update case
    3. Check for escalation conditions
    4. Check for closure commands
    5. Send notifications

    Args:
        user_id: Slack user ID
        message_text: Message content
        channel_id: Slack channel ID
        event_id: Slack event ID for deduplication

    Returns:
        bool: True if processing succeeded
    """
    # Sanitize message text
    clean_message = sanitize_message_body(message_text)

    # Get admin identifiers
    admin_identifiers = get_admin_identifiers()

    # Determine if this is an admin message
    is_admin = is_admin_message(user_id, admin_identifiers)

    # Get database session
    db = SessionLocal()
    case_id = None

    try:
        if is_admin:
            # Handle admin message
            success = await process_admin_message(db, user_id, clean_message, admin_identifiers)
        else:
            # Handle customer message
            success = await process_customer_message_logic(db, user_id, clean_message, channel_id)
            if success:
                # Get the case ID that was created or updated
                from .db import Case
                case = db.query(Case).filter(Case.customer_identifier == user_id, Case.status == "open").first()
                case_id = case.case_id if case else None

        # Record that this message was processed (only if processing succeeded)
        if success:
            record_processed_message(db, event_id, "slack", case_id)

        return success

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        db.rollback()
        return False
    finally:
        db.close()


async def process_admin_message(db: Session, user_id: str, message_text: str, admin_identifiers: list) -> bool:
    """
    Process a message from an admin user.

    Args:
        db: Database session
        user_id: Slack user ID of admin
        message_text: Message content
        admin_identifiers: List of admin identifiers

    Returns:
        bool: True if processing succeeded
    """
    # Check if this is a case closure command
    if detect_closure_phrase(message_text):
        # Find open cases for this admin (if they have any active cases)
        # For now, we'll close the most recent case (this could be improved)
        from .db import Case

        # Get the most recent open case
        case = db.query(Case).filter(Case.status == "open").order_by(Case.last_message_at.desc()).first()

        if case:
            # Close the case
            close_case(db, case, user_id)

            # Send closure log (non-blocking)
            try:
                from .notifications import send_closure_log
                # Don't wait for notification to complete - fire and forget
                import asyncio
                asyncio.create_task(send_closure_log_async(case.case_id, user_id))
            except Exception as e:
                logger.warning(f"Failed to send closure log: {e}")

            logger.info(f"Case {case.case_id} closed by admin {user_id}")
            return True
        else:
            logger.warning(f"Admin {user_id} tried to close case but no open cases found")
            return False

    # For other admin messages, we don't need to do anything special
    # They might be responding to customers in the support channel
    logger.debug(f"Admin message received from {user_id}: {message_text[:50]}...")
    return True


async def process_customer_message_logic(db: Session, user_id: str, message_text: str, channel_id: str) -> bool:
    """
    Process a customer message and handle case creation/updates.

    Args:
        db: Database session
        user_id: Slack user ID
        message_text: Message content
        channel_id: Slack channel ID

    Returns:
        bool: True if processing succeeded
    """
    # Get or create case for this customer
    case = get_or_create_case(db, user_id)

    # Get admin identifiers for escalation checking
    admin_identifiers = get_admin_identifiers()

    # Collect all reasons that apply at this moment
    reasons = get_escalation_reasons(case, message_text)

    # Add message to case
    from .db import add_message_to_case
    message = add_message_to_case(
        db=db,
        case=case,
        sender=user_id,
        body=message_text,
        source="slack",
        is_admin=False
    )

    # Handle escalation if needed: single combined alert and skip if already escalated
    if reasons and not case.escalated:
        from .db import escalate_case
        escalate_case(db, case)

        combined_reason = "; ".join(reasons)

        # Send escalation alert (non-blocking)
        try:
            # Don't wait for notification to complete - fire and forget
            import asyncio
            asyncio.create_task(send_escalation_alert_async(case.case_id, combined_reason, user_id))
        except Exception as e:
            logger.warning(f"Failed to send escalation alert: {e}")

        logger.info(f"Case {case.case_id} escalated", reason=combined_reason)

    # Send support notification (non-blocking)
    customer_name = f"User {user_id}"
    notification_message = format_slack_message(case.case_id, message_text, customer_name)
    try:
        from .notifications import send_support_notification
        # Don't wait for notification to complete - fire and forget
        import asyncio
        asyncio.create_task(send_support_notification_async(case.case_id, notification_message))
    except Exception as e:
        logger.warning(f"Failed to send support notification: {e}")

    # Try to reply in thread if this is a threaded conversation
    # Note: This is a simplified implementation - in practice you'd need
    # to track thread timestamps from the original message
    # For now, we'll just send to the support channel

    logger.info(f"Processed customer message for case {case.case_id}")
    return True


# Async wrapper functions for non-blocking notifications
async def send_escalation_alert_async(case_id: str, reason: str, customer_identifier: str):
    """Async wrapper for escalation alert sending."""
    try:
        from .notifications import send_escalation_alert
        await asyncio.get_event_loop().run_in_executor(None, send_escalation_alert, case_id, reason, customer_identifier)
    except Exception as e:
        logger.error(f"Async escalation alert failed: {e}")

async def send_support_notification_async(case_id: str, message: str):
    """Async wrapper for support notification sending."""
    try:
        from .notifications import send_support_notification
        await asyncio.get_event_loop().run_in_executor(None, send_support_notification, case_id, message)
    except Exception as e:
        logger.error(f"Async support notification failed: {e}")

async def send_closure_log_async(case_id: str, admin_identifier: str):
    """Async wrapper for closure log sending."""
    try:
        from .notifications import send_closure_log
        await asyncio.get_event_loop().run_in_executor(None, send_closure_log, case_id, admin_identifier)
    except Exception as e:
        logger.error(f"Async closure log failed: {e}")

# Optional: Function to handle Slack thread replies
def get_thread_ts_from_event(event_data: dict) -> Optional[str]:
    """
    Extract thread timestamp from Slack event for threaded replies.

    Args:
        event_data: Slack event payload

    Returns:
        Optional[str]: Thread timestamp or None
    """
    event = event_data.get("event", {})
    return event.get("thread_ts") or event.get("ts")


# Example of how to handle threaded conversations (for future enhancement)
async def handle_threaded_reply(event_data: dict, case_id: str, response_text: str) -> bool:
    """
    Handle replying in a Slack thread (future enhancement).

    Args:
        event_data: Original Slack event
        case_id: Case ID
        response_text: Response text

    Returns:
        bool: True if reply sent successfully
    """
    thread_ts = get_thread_ts_from_event(event_data)
    if thread_ts:
        channel_id = event_data.get("event", {}).get("channel")
        if channel_id:
            from .notifications import reply_in_thread
            return reply_in_thread(channel_id, thread_ts, response_text)

    return False
