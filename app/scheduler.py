"""
Escalation scheduler for checking case escalation conditions.

Uses APScheduler to periodically check for cases that need escalation
based on time elapsed and follow-up patterns.
"""

import os
import logging
import structlog
from datetime import datetime, timedelta
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from .db import SessionLocal, Case, escalate_case, should_send_escalation_alert, update_last_escalation_alert
from .utils import check_time_escalation, check_followup_escalation, get_escalation_reasons
from .notifications import send_escalation_alert

logger = structlog.get_logger(__name__)


class EscalationScheduler:
    """
    Scheduler for checking and handling case escalations.
    """

    def __init__(self):
        """
        Initialize the escalation scheduler.
        """
        self.scheduler = BackgroundScheduler()
        self.check_interval = int(os.getenv("ESCALATION_CHECK_INTERVAL", "300"))  # 5 minutes default
        self.admin_identifiers = self._get_admin_identifiers()

    def _get_admin_identifiers(self) -> List[str]:
        """
        Get list of admin identifiers for escalation checking.

        Returns:
            List[str]: List of admin identifiers
        """
        admin_emails = os.getenv("ADMIN_EMAILS", "")
        admin_slack_ids = os.getenv("ADMIN_SLACK_IDS", "")

        admins = []

        if admin_emails:
            admins.extend([email.strip() for email in admin_emails.split(",") if email.strip()])

        if admin_slack_ids:
            admins.extend([slack_id.strip() for slack_id in admin_slack_ids.split(",") if slack_id.strip()])

        return admins

    def start(self):
        """
        Start the escalation scheduler.
        """
        if self.scheduler.running:
            logger.warning("Escalation scheduler already running")
            return

        # Schedule escalation checks
        trigger = IntervalTrigger(seconds=self.check_interval)

        self.scheduler.add_job(
            func=self._check_escalations,
            trigger=trigger,
            id='escalation_check',
            name='Check Case Escalations',
            replace_existing=True
        )

        self.scheduler.start()
        logger.info("Escalation scheduler started", check_interval=self.check_interval)

    def stop(self):
        """
        Stop the escalation scheduler.
        """
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Escalation scheduler stopped")

    def _check_escalations(self):
        """
        Check all open cases for escalation conditions.

        This method is called periodically by the scheduler to:
        1. Check time-based escalation (48+ hours)
        2. Check follow-up escalation (>3 consecutive customer messages)
        3. Send escalation alerts for cases that need escalation
        """
        db = SessionLocal()

        try:
            logger.debug("Checking cases for escalation...")

            # Get all open cases
            open_cases = db.query(Case).filter(Case.status == "open").all()

            escalated_count = 0

            for case in open_cases:
                # Skip if already escalated
                if case.escalated:
                    continue

                # Gather combined reasons
                reasons = []
                if check_time_escalation(case):
                    reasons.append(f"Inactive for more than {48} hours")
                if check_followup_escalation(case):
                    reasons.append(f"More than {3} follow-ups without admin reply")

                # Escalate case if needed
                if reasons:
                    escalate_case(db, case)

                    # Check if we should send an escalation alert (prevent duplicates)
                    if should_send_escalation_alert(db, case):
                        # Send escalation alert
                        reason_text = "; ".join(reasons)
                        send_escalation_alert(case.case_id, reason_text, case.customer_identifier)

                        # Update the last alert timestamp
                        update_last_escalation_alert(db, case)

                        escalated_count += 1
                        logger.info("Case escalated", case_id=case.case_id, reason=reason_text)
                    else:
                        logger.info("Case already escalated recently, skipping alert", case_id=case.case_id)

            if escalated_count > 0:
                logger.info("Escalation check completed", escalated_cases=escalated_count)
            else:
                logger.debug("No cases needed escalation")

        except Exception as e:
            logger.error("Error checking escalations", error=str(e))
        finally:
            db.close()

    def check_case_escalation(self, case_id: str) -> bool:
        """
        Manually check escalation for a specific case.

        Args:
            case_id: The case ID to check

        Returns:
            bool: True if case was escalated, False if not needed or already escalated
        """
        db = SessionLocal()

        try:
            case = db.query(Case).filter(Case.case_id == case_id).first()

            if not case:
                logger.warning("Case not found for escalation check", case_id=case_id)
                return False

            if case.status != "open":
                logger.debug("Case not open for escalation", case_id=case_id, status=case.status)
                return False

            if case.escalated:
                logger.debug("Case already escalated", case_id=case_id)
                return False

            escalated = False
            reasons = []

            # Check time-based escalation
            if check_time_escalation(case):
                escalated = True
                reasons.append(f"Case inactive for more than {48} hours")

            # Check follow-up escalation
            if check_followup_escalation(case):
                escalated = True
                reasons.append(f"More than {3} consecutive customer follow-ups")

            # Escalate if needed
            if escalated:
                escalate_case(db, case)

                reason_text = "; ".join(reasons)
                send_escalation_alert(case.case_id, reason_text, case.customer_identifier)

                logger.info("Case manually escalated", case_id=case_id, reason=reason_text)
                return True
            else:
                logger.debug("Case does not need escalation", case_id=case_id)
                return False

        except Exception as e:
            logger.error("Error checking case escalation", case_id=case_id, error=str(e))
            return False
        finally:
            db.close()

    def get_scheduler_status(self) -> dict:
        """
        Get scheduler status information.

        Returns:
            dict: Scheduler status and configuration
        """
        return {
            "running": self.scheduler.running,
            "check_interval": self.check_interval,
            "admin_identifiers_count": len(self.admin_identifiers),
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": str(job.next_run_time) if job.next_run_time else None
                }
                for job in self.scheduler.get_jobs()
            ]
        }


# Global scheduler instance
_scheduler: Optional[EscalationScheduler] = None


def get_scheduler() -> EscalationScheduler:
    """
    Get or create the global scheduler instance.

    Returns:
        EscalationScheduler: The scheduler instance
    """
    global _scheduler

    if _scheduler is None:
        _scheduler = EscalationScheduler()

    return _scheduler


# Optional: Manual escalation check function
def check_all_escalations() -> int:
    """
    Manually trigger escalation check for all cases.

    Returns:
        int: Number of cases escalated
    """
    scheduler = get_scheduler()
    scheduler._check_escalations()
    return 0  # This would need to be modified to return the actual count


if __name__ == "__main__":
    # For testing the scheduler
    scheduler = EscalationScheduler()
    scheduler.start()

    try:
        # Keep running for testing
        import time
        print("Scheduler started. Press Ctrl+C to stop.")
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        scheduler.stop()
        print("Scheduler stopped.")
