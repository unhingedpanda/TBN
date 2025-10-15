"""
Slack notification helpers for the case management system.

Handles sending messages to different Slack channels for notifications,
escalation alerts, and logging.
"""

import os
import logging
import time
import random
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


class SlackNotifier:
    """
    Slack notification service for sending messages to different channels.

    Includes retry logic with exponential backoff for handling rate limits
    and transient failures.
    """

    def __init__(self, bot_token: str):
        """
        Initialize Slack notifier with bot token.

        Args:
            bot_token: Slack bot token (xoxb-...)
        """
        self.client = WebClient(token=bot_token)
        self.bot_token = bot_token
        self.max_retries = int(os.getenv("SLACK_MAX_RETRIES", "3"))
        self.base_delay = float(os.getenv("SLACK_RETRY_BASE_DELAY", "1.0"))  # seconds
        self.max_delay = float(os.getenv("SLACK_RETRY_MAX_DELAY", "60.0"))  # seconds

    def _send_with_retry(self, operation_name: str, slack_operation):
        """
        Execute a Slack API operation with retry logic and exponential backoff.

        Args:
            operation_name: Name of the operation for logging
            slack_operation: Function that performs the Slack API call

        Returns:
            bool: True if operation succeeded
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                result = slack_operation()
                if attempt > 0:
                    logger.info(f"Slack {operation_name} succeeded after {attempt} retries")
                return True

            except SlackApiError as e:
                last_exception = e

                # Check if this is a rate limit error (429)
                if e.response.status_code == 429:
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        delay = min(int(retry_after), self.max_delay)
                    else:
                        # Exponential backoff for rate limits
                        delay = min(self.base_delay * (2 ** attempt) + random.uniform(0, 1), self.max_delay)

                    logger.warning(f"Slack rate limit hit for {operation_name}, retrying in {delay}s (attempt {attempt + 1}/{self.max_retries + 1})")
                    time.sleep(delay)
                    continue

                # For other errors, check if we should retry
                if attempt < self.max_retries and self._should_retry_error(e):
                    delay = min(self.base_delay * (2 ** attempt) + random.uniform(0, 1), self.max_delay)
                    logger.warning(f"Slack {operation_name} failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. Retrying in {delay}s")
                    time.sleep(delay)
                    continue

                # Don't retry for this error or max retries reached
                logger.error(f"Slack {operation_name} failed after {attempt + 1} attempts: {e}")
                break

            except Exception as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = min(self.base_delay * (2 ** attempt) + random.uniform(0, 1), self.max_delay)
                    logger.warning(f"Slack {operation_name} failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. Retrying in {delay}s")
                    time.sleep(delay)
                    continue

                logger.error(f"Slack {operation_name} failed after {attempt + 1} attempts: {e}")
                break

        return False

    def _should_retry_error(self, error: SlackApiError) -> bool:
        """
        Determine if a Slack API error should be retried.

        Args:
            error: The Slack API error

        Returns:
            bool: True if the error should be retried
        """
        # Retry on network errors, server errors, and some client errors
        retryable_status_codes = {429, 500, 502, 503, 504}
        return error.response.status_code in retryable_status_codes

    def send_support_notification(self, case_id: str, message: str) -> bool:
        """
        Send a notification to the support channel.

        Args:
            case_id: The case ID
            message: The message to send

        Returns:
            bool: True if message sent successfully
        """
        channel = os.getenv("SUPPORT_SLACK_CHANNEL")
        if not channel:
            logger.error("SUPPORT_SLACK_CHANNEL not configured")
            return False

        def _slack_operation():
            response = self.client.chat_postMessage(
                channel=self.get_channel_id(channel),
                text=message
            )
            return response

        return self._send_with_retry("support notification", _slack_operation)

    def send_escalation_alert(self, case_id: str, reason: str, customer_identifier: str) -> bool:
        """
        Send an escalation alert to the alerting channel.

        Args:
            case_id: The case ID
            reason: Reason for escalation
            customer_identifier: Customer identifier

        Returns:
            bool: True if alert sent successfully
        """
        channel = os.getenv("ALERTING_SLACK_CHANNEL")
        if not channel:
            logger.error("ALERTING_SLACK_CHANNEL not configured")
            return False

        from .utils import format_escalation_alert
        alert_message = format_escalation_alert(case_id, reason, customer_identifier)

        def _slack_operation():
            response = self.client.chat_postMessage(
                channel=self.get_channel_id(channel),
                text=alert_message
            )
            return response

        return self._send_with_retry("escalation alert", _slack_operation)

    def send_closure_log(self, case_id: str, admin_identifier: str) -> bool:
        """
        Send a case closure log to the logging channel.

        Args:
            case_id: The case ID
            admin_identifier: The admin who closed the case

        Returns:
            bool: True if log sent successfully
        """
        channel = os.getenv("LOGGING_SLACK_CHANNEL")
        if not channel:
            logger.error("LOGGING_SLACK_CHANNEL not configured")
            return False

        from .utils import format_closure_log
        log_message = format_closure_log(case_id, admin_identifier)

        def _slack_operation():
            response = self.client.chat_postMessage(
                channel=self.get_channel_id(channel),
                text=log_message
            )
            return response

        return self._send_with_retry("closure log", _slack_operation)

    def reply_in_thread(self, channel: str, thread_ts: str, message: str) -> bool:
        """
        Reply to a message in a Slack thread.

        Args:
            channel: Channel ID
            thread_ts: Thread timestamp to reply to
            message: Message to send

        Returns:
            bool: True if reply sent successfully
        """
        def _slack_operation():
            response = self.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=message
            )
            return response

        success = self._send_with_retry("thread reply", _slack_operation)
        if success:
            logger.info(f"Replied in thread {thread_ts} in channel {channel}")
        return success

    def get_channel_id(self, channel_name: str) -> Optional[str]:
        """
        Get channel ID from channel name or return ID if already provided.

        Args:
            channel_name: Channel name (with or without #) or channel ID (starting with C)

        Returns:
            Optional[str]: Channel ID or None if not found
        """
        # If it's already a channel ID (starts with C), return it directly
        if channel_name.startswith('C'):
            return channel_name

        # Otherwise, look up by name
        try:
            clean_name = channel_name.lstrip('#')

            # Get list of channels
            response = self.client.conversations_list()
            if not response.get("ok"):
                logger.error(f"Failed to list channels: {response.get('error')}")
                return None

            channels = response.get("channels", [])

            # Find channel by name
            for channel in channels:
                if channel["name"] == clean_name:
                    return channel["id"]

            logger.warning(f"Channel '{channel_name}' not found")
            return None

        except Exception as e:
            logger.error(f"Error getting channel ID: {e}")
            return None

    def _send_message(self, channel: str, message: str) -> bool:
        """
        Internal method to send a message to a Slack channel.

        Args:
            channel: Channel ID or name
            message: Message to send

        Returns:
            bool: True if message sent successfully
        """
        def _slack_operation():
            # If channel is a name (starts with # or no #), get the ID
            if channel.startswith('#') or not channel.startswith('C'):
                channel_id = self.get_channel_id(channel)
                if not channel_id:
                    raise ValueError(f"Could not find channel: {channel}")
            else:
                channel_id = channel

            response = self.client.chat_postMessage(
                channel=channel_id,
                text=message
            )
            return response

        success = self._send_with_retry("message send", _slack_operation)
        if success:
            logger.info(f"Message sent to channel {channel}")
        return success


# Global notifier instance
_notifier: Optional[SlackNotifier] = None


def get_notifier() -> SlackNotifier:
    """
    Get or create the global Slack notifier instance.

    Returns:
        SlackNotifier: The notifier instance
    """
    global _notifier

    if _notifier is None:
        bot_token = os.getenv("SLACK_BOT_TOKEN")
        if not bot_token:
            raise ValueError("SLACK_BOT_TOKEN environment variable not set")

        _notifier = SlackNotifier(bot_token)

    return _notifier


def send_support_notification(case_id: str, message: str) -> bool:
    """
    Convenience function to send support notification.

    Args:
        case_id: The case ID
        message: The message to send

    Returns:
        bool: True if sent successfully
    """
    try:
        notifier = get_notifier()
        return notifier.send_support_notification(case_id, message)
    except Exception as e:
        logger.error(f"Failed to send support notification: {e}")
        return False


def send_escalation_alert(case_id: str, reason: str, customer_identifier: str) -> bool:
    """
    Convenience function to send escalation alert.

    Args:
        case_id: The case ID
        reason: Reason for escalation
        customer_identifier: Customer identifier

    Returns:
        bool: True if sent successfully
    """
    try:
        notifier = get_notifier()
        return notifier.send_escalation_alert(case_id, reason, customer_identifier)
    except Exception as e:
        logger.error(f"Failed to send escalation alert: {e}")
        return False


def send_closure_log(case_id: str, admin_identifier: str) -> bool:
    """
    Convenience function to send case closure log.

    Args:
        case_id: The case ID
        admin_identifier: The admin who closed the case

    Returns:
        bool: True if sent successfully
    """
    try:
        notifier = get_notifier()
        return notifier.send_closure_log(case_id, admin_identifier)
    except Exception as e:
        logger.error(f"Failed to send closure log: {e}")
        return False


def reply_in_thread(channel: str, thread_ts: str, message: str) -> bool:
    """
    Convenience function to reply in thread.

    Args:
        channel: Channel ID
        thread_ts: Thread timestamp
        message: Message to send

    Returns:
        bool: True if sent successfully
    """
    try:
        notifier = get_notifier()
        return notifier.reply_in_thread(channel, thread_ts, message)
    except Exception as e:
        logger.error(f"Failed to reply in thread: {e}")
        return False
