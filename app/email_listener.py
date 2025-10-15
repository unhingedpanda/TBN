"""
Email listener for IMAP ingestion.

Polls an IMAP server for new emails, processes customer inquiries and admin responses,
and manages case creation, escalation, and closure.
"""

import os
import time
import logging
import threading
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr
from typing import Optional, List

import imaplib
from sqlalchemy.orm import Session

from .db import SessionLocal, get_or_create_case, add_message_to_case, close_case, is_message_processed, record_processed_message
from .utils import (
    parse_customer_identifier,
    detect_urgent_keywords,
    is_admin_message,
    detect_closure_phrase,
    get_escalation_reasons,
    format_slack_message,
    sanitize_message_body
)
from .notifications import send_support_notification, send_escalation_alert, send_closure_log

logger = logging.getLogger(__name__)


class EmailListener:
    """
    Email listener that polls IMAP server for new messages.
    """

    def __init__(self):
        """
        Initialize email listener with configuration from environment.
        """
        self.imap_server = os.getenv("IMAP_SERVER", "imap.gmail.com")
        self.imap_port = int(os.getenv("IMAP_PORT", "993"))
        self.email_address = os.getenv("IMAP_EMAIL")
        self.email_password = os.getenv("IMAP_PASSWORD")

        if not all([self.email_address, self.email_password]):
            raise ValueError("IMAP_EMAIL and IMAP_PASSWORD must be configured")

        self.admin_emails = self._get_admin_emails()
        self.polling_interval = int(os.getenv("EMAIL_POLL_INTERVAL", "30"))  # seconds
        self.stop_event = threading.Event()
        self.listener_thread = None

    def _get_admin_emails(self) -> List[str]:
        """
        Get list of admin email addresses.

        Returns:
            List[str]: List of admin email addresses
        """
        admin_emails_str = os.getenv("ADMIN_EMAILS", "")
        if not admin_emails_str:
            return []

        return [email.strip().lower() for email in admin_emails_str.split(",") if email.strip()]

    def start(self):
        """
        Start the email listener in a background thread.
        """
        if self.listener_thread and self.listener_thread.is_alive():
            logger.warning("Email listener already running")
            return

        self.stop_event.clear()
        self.listener_thread = threading.Thread(target=self._poll_emails, daemon=True)
        self.listener_thread.start()
        logger.info(f"Email listener started for {self.email_address}")

    def stop(self):
        """
        Stop the email listener.
        """
        self.stop_event.set()
        if self.listener_thread:
            self.listener_thread.join(timeout=10)
        logger.info("Email listener stopped")

    def _poll_emails(self):
        """
        Main polling loop for checking new emails.
        """
        while not self.stop_event.is_set():
            try:
                self._check_emails()
            except Exception as e:
                logger.error(f"Error checking emails: {e}")

            # Wait before next poll
            time.sleep(self.polling_interval)

    def _check_emails(self):
        """
        Check for new emails and process them.
        """
        mail = None
        try:
            # Connect to IMAP server
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)

            # Login
            mail.login(self.email_address, self.email_password)
            logger.debug("Connected to IMAP server")

            # Select inbox
            mail.select('inbox')

            # Search for unseen messages
            status, messages = mail.search(None, 'UNSEEN')

            if status != 'OK':
                logger.error(f"Failed to search for emails: {status}")
                return

            # Process each new message
            for msg_id in messages[0].split():
                try:
                    self._process_email(mail, msg_id)
                except Exception as e:
                    logger.error(f"Error processing email {msg_id}: {e}")

        except Exception as e:
            logger.error(f"IMAP connection error: {e}")
        finally:
            if mail:
                try:
                    mail.close()
                    mail.logout()
                except:
                    pass

    def _process_email(self, mail: imaplib.IMAP4_SSL, msg_id: bytes):
        """
        Process a single email message.

        Args:
            mail: IMAP connection
            msg_id: Email message ID
        """
        # Fetch the email
        status, msg_data = mail.fetch(msg_id, '(RFC822)')

        if status != 'OK':
            logger.error(f"Failed to fetch email {msg_id}: {status}")
            return

        # Parse email message
        raw_email = msg_data[0][1]
        email_message = message_from_bytes(raw_email)

        # Extract Message-ID for deduplication
        message_id = email_message.get('Message-ID', '').strip('<>')
        if not message_id:
            logger.warning(f"Email {msg_id} missing Message-ID header")
            return

        # Check for duplicate processing
        db = SessionLocal()
        try:
            if is_message_processed(db, message_id, "email"):
                logger.info(f"Email {message_id} already processed, skipping")
                return
        finally:
            db.close()

        # Extract email data
        sender_email = self._extract_sender_email(email_message)
        subject = self._extract_subject(email_message)
        body = self._extract_body(email_message)

        if not sender_email or not body:
            logger.warning(f"Could not extract sender or body from email {msg_id}")
            return

        # Combine subject and body for full message
        full_body = f"{subject}\n\n{body}" if subject else body
        clean_body = sanitize_message_body(full_body)

        # Determine if this is an admin email
        is_admin = is_admin_message(sender_email, self.admin_emails)

        logger.info(f"Processing email from {sender_email} (admin: {is_admin}, message_id: {message_id})")

        # Process the email
        success = self._handle_email_message(sender_email, clean_body, is_admin, message_id)

        if success:
            # Mark email as seen if processing succeeded
            mail.store(msg_id, '+FLAGS', '\\Seen')
            logger.debug(f"Marked email {msg_id} as seen")

    def _extract_sender_email(self, email_message) -> Optional[str]:
        """
        Extract sender email address from email message.

        Args:
            email_message: Parsed email message

        Returns:
            Optional[str]: Sender email address
        """
        # Get from header
        from_header = email_message.get('From', '')

        # Parse email address
        _, email_address = parseaddr(from_header)

        return email_address.lower() if email_address else None

    def _extract_subject(self, email_message) -> Optional[str]:
        """
        Extract subject from email message.

        Args:
            email_message: Parsed email message

        Returns:
            Optional[str]: Email subject
        """
        subject_header = email_message.get('Subject', '')

        # Decode subject if needed
        decoded_parts = decode_header(subject_header)
        subject_parts = []

        for part, encoding in decoded_parts:
            if isinstance(part, bytes) and encoding:
                part = part.decode(encoding)
            subject_parts.append(str(part))

        return ' '.join(subject_parts) if subject_parts else None

    def _extract_body(self, email_message) -> Optional[str]:
        """
        Extract email body, handling both plain text and HTML.

        Args:
            email_message: Parsed email message

        Returns:
            Optional[str]: Email body text
        """
        # Try to get text content
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))

                # Skip attachments
                if 'attachment' in content_disposition:
                    continue

                # Get text content
                if content_type == 'text/plain':
                    try:
                        body = part.get_payload(decode=True)
                        if body:
                            return body.decode('utf-8', errors='ignore')
                    except:
                        continue

                elif content_type == 'text/html':
                    # For HTML, we'd need additional parsing (simplified here)
                    try:
                        html_body = part.get_payload(decode=True)
                        if html_body:
                            # Basic HTML stripping (in production, use a proper HTML parser)
                            text_body = html_body.decode('utf-8', errors='ignore')
                            # Simple HTML tag removal (very basic)
                            import re
                            text_body = re.sub(r'<[^>]+>', '', text_body)
                            return text_body
                    except:
                        continue
        else:
            # Single part message
            try:
                payload = email_message.get_payload(decode=True)
                if payload:
                    return payload.decode('utf-8', errors='ignore')
            except:
                pass

        return None

    def _handle_email_message(self, sender_email: str, message_body: str, is_admin: bool, message_id: str) -> bool:
        """
        Handle email message processing for case management.

        Args:
            sender_email: Sender email address
            message_body: Email content
            is_admin: Whether sender is an admin
            message_id: Email Message-ID for deduplication

        Returns:
            bool: True if processing succeeded
        """
        db = SessionLocal()
        case_id = None

        try:
            if is_admin:
                success = self._handle_admin_email(db, sender_email, message_body)
            else:
                success = self._handle_customer_email(db, sender_email, message_body)
                if success:
                    # Get the case ID that was created or updated
                    from .db import Case
                    case = db.query(Case).filter(Case.customer_identifier == sender_email, Case.status == "open").first()
                    case_id = case.case_id if case else None

            # Record that this message was processed (only if processing succeeded)
            if success:
                record_processed_message(db, message_id, "email", case_id)

            return success

        except Exception as e:
            logger.error(f"Error handling email message: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def _handle_admin_email(self, db: Session, sender_email: str, message_body: str) -> bool:
        """
        Handle email from admin user.

        Args:
            db: Database session
            sender_email: Admin email address
            message_body: Email content

        Returns:
            bool: True if processing succeeded
        """
        # Check if this is a case closure command
        if detect_closure_phrase(message_body):
            # Find open cases for this admin's domain/organization
            # For simplicity, we'll close the most recent case
            from .db import Case

            case = db.query(Case).filter(Case.status == "open").order_by(Case.last_message_at.desc()).first()

            if case:
                close_case(db, case, sender_email)
                send_closure_log(case.case_id, sender_email)
                logger.info(f"Case {case.case_id} closed by admin {sender_email}")
                return True
            else:
                logger.warning(f"Admin {sender_email} tried to close case but no open cases found")
                return False

        # For other admin emails, we don't need special processing
        logger.debug(f"Admin email received from {sender_email}")
        return True

    def _handle_customer_email(self, db: Session, sender_email: str, message_body: str) -> bool:
        """
        Handle email from customer.

        Args:
            db: Database session
            sender_email: Customer email address
            message_body: Email content

        Returns:
            bool: True if processing succeeded
        """
        # Get or create case for this customer
        case = get_or_create_case(db, sender_email)

        # Collect combined escalation reasons and handle single alert; skip if already escalated
        reasons = get_escalation_reasons(case, message_body)

        # Add message to case
        message = add_message_to_case(
            db=db,
            case=case,
            sender=sender_email,
            body=message_body,
            source="email",
            is_admin=False
        )

        # Handle escalation if needed
        if reasons and not case.escalated:
            from .db import escalate_case
            escalate_case(db, case)
            reason_text = "; ".join(reasons)
            send_escalation_alert(case.case_id, reason_text, sender_email)
            logger.info("Case escalated", case_id=case.case_id, reason=reason_text)

        # Send support notification
        customer_name = sender_email.split('@')[0]  # Simple name extraction
        notification_message = format_slack_message(case.case_id, message_body, customer_name)
        send_support_notification(case.case_id, notification_message)

        logger.info(f"Processed customer email for case {case.case_id}")
        return True


# Optional: Function to test email connection
def test_email_connection() -> bool:
    """
    Test email connection and credentials.

    Returns:
        bool: True if connection successful
    """
    try:
        listener = EmailListener()

        mail = imaplib.IMAP4_SSL(listener.imap_server, listener.imap_port)
        mail.login(listener.email_address, listener.email_password)
        mail.select('inbox')
        mail.close()
        mail.logout()

        logger.info("Email connection test successful")
        return True

    except Exception as e:
        logger.error(f"Email connection test failed: {e}")
        return False


if __name__ == "__main__":
    # Test email connection if run directly
    test_email_connection()
