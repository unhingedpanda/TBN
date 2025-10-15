"""
Main FastAPI application for the case management system.

Coordinates all components including database, email listener, Slack listener,
scheduler, and notification services.
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from .db import DATABASE_URL, get_db, init_db, SessionLocal
from .email_listener import EmailListener
from .slack_listener import process_slack_event
from .scheduler import EscalationScheduler
from .notifications import get_notifier
from .utils import verify_slack_signature, get_slack_headers

# Configure structured logging
import structlog

# Configure structlog for JSON logging in production, readable logs in development
if os.getenv("DEBUG", "").lower() == "true":
    # Development: Human-readable logs
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
else:
    # Production: JSON logs
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

logger = structlog.get_logger("app.main")

# Global instances for services
email_listener: EmailListener = None
escalation_scheduler: EscalationScheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting case management system", version="1.0.0")

    # Initialize database
    init_db()
    logger.info("Database initialized", db_type=DATABASE_URL.split("://")[0])

    # Initialize Slack notifier
    try:
        get_notifier()
        logger.info("Slack notifier initialized")
    except Exception as e:
        logger.error("Failed to initialize Slack notifier", error=str(e))

    # Start email listener if configured
    global email_listener
    if os.getenv("IMAP_SERVER") and os.getenv("IMAP_EMAIL"):
        email_listener = EmailListener()
        email_listener.start()
        logger.info("Email listener started", server=os.getenv("IMAP_SERVER"))
    else:
        logger.warning("Email configuration not found, email listener disabled")

    # Start escalation scheduler
    global escalation_scheduler
    escalation_scheduler = EscalationScheduler()
    escalation_scheduler.start()
    logger.info("Escalation scheduler started", check_interval=os.getenv("ESCALATION_CHECK_INTERVAL", "300"))

    yield

    # Shutdown
    logger.info("Shutting down case management system")

    if email_listener:
        email_listener.stop()
        logger.info("Email listener stopped")

    if escalation_scheduler:
        escalation_scheduler.stop()
        logger.info("Escalation scheduler stopped")


# Create FastAPI application
app = FastAPI(
    title="Case Management System",
    description="Self-hosted customer case management and escalation system",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """
    Comprehensive health check endpoint.

    Returns detailed health status including database connectivity,
    service status, and external service availability.

    Returns:
        dict: Health status information with detailed service checks
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {}
    }

    # Check database connectivity
    try:
        db = SessionLocal()
        # Simple query to test database connection
        db.execute(text("SELECT 1"))
        health_status["services"]["database"] = {
            "status": "healthy",
            "type": "postgresql" if not DATABASE_URL.startswith("sqlite") else "sqlite",
            "message": "Database connection successful"
        }
        db.close()
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["services"]["database"] = {
            "status": "unhealthy",
            "error": str(e),
            "message": "Database connection failed"
        }

    # Check Slack connectivity (if configured)
    slack_configured = bool(os.getenv("SLACK_BOT_TOKEN"))
    if slack_configured:
        try:
            notifier = get_notifier()
            # Test Slack API connectivity by getting bot info
            auth_test = notifier.client.auth_test()
            if auth_test.get("ok"):
                health_status["services"]["slack"] = {
                    "status": "healthy",
                    "bot_id": auth_test.get("bot_id"),
                    "message": "Slack API connection successful"
                }
            else:
                health_status["status"] = "unhealthy"
                health_status["services"]["slack"] = {
                    "status": "unhealthy",
                    "error": auth_test.get("error"),
                    "message": "Slack authentication failed"
                }
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["services"]["slack"] = {
                "status": "unhealthy",
                "error": str(e),
                "message": "Slack connection failed"
            }
    else:
        health_status["services"]["slack"] = {
            "status": "disabled",
            "message": "Slack not configured"
        }

    # Check email listener status
    if email_listener:
        try:
            # Check if email listener is still running
            if email_listener.listener_thread and email_listener.listener_thread.is_alive():
                health_status["services"]["email_listener"] = {
                    "status": "healthy",
                    "message": "Email listener running"
                }
            else:
                health_status["status"] = "unhealthy"
                health_status["services"]["email_listener"] = {
                    "status": "unhealthy",
                    "message": "Email listener not running"
                }
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["services"]["email_listener"] = {
                "status": "unhealthy",
                "error": str(e),
                "message": "Email listener check failed"
            }
    else:
        health_status["services"]["email_listener"] = {
            "status": "disabled",
            "message": "Email listener not configured"
        }

    # Check scheduler status
    if escalation_scheduler:
        try:
            scheduler_info = escalation_scheduler.get_scheduler_status()
            if scheduler_info.get("running"):
                health_status["services"]["scheduler"] = {
                    "status": "healthy",
                    "message": f"Scheduler running with {len(scheduler_info.get('jobs', []))} jobs"
                }
            else:
                health_status["status"] = "unhealthy"
                health_status["services"]["scheduler"] = {
                    "status": "unhealthy",
                    "message": "Scheduler not running"
                }
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["services"]["scheduler"] = {
                "status": "unhealthy",
                "error": str(e),
                "message": "Scheduler check failed"
            }
    else:
        health_status["services"]["scheduler"] = {
            "status": "disabled",
            "message": "Scheduler not initialized"
        }

    return health_status


@app.get("/ready")
async def readiness_check():
    """
    Kubernetes readiness probe endpoint.

    Returns 200 if the service is ready to accept traffic,
    503 if not ready.

    Returns:
        dict: Readiness status
    """
    try:
        # Quick database connectivity check
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()

        return {
            "status": "ready",
            "timestamp": datetime.utcnow().isoformat(),
            "message": "Service is ready to accept requests"
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail={
            "status": "not ready",
            "error": str(e),
            "message": "Service not ready"
        })


@app.get("/live")
async def liveness_check():
    """
    Kubernetes liveness probe endpoint.

    Always returns 200 if the service is running.

    Returns:
        dict: Liveness status
    """
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat(),
        "message": "Service is running"
    }


@app.post("/slack/events")
async def slack_webhook(request: Request):
    """
    Slack Events API webhook endpoint.

    Handles Event Subscriptions from Slack including:
    - url_verification challenge response
    - Signature verification for security
    - Event processing for case management

    Returns:
        dict or str: Response for successful processing or challenge response
    """
    # Handle URL verification challenge (required for Event Subscriptions setup)
    if request.headers.get("content-type") == "application/json":
        try:
            event_data = await request.json()
            if event_data.get("type") == "url_verification":
                challenge = event_data.get("challenge")
                if challenge:
                    logger.info("Responding to Slack URL verification challenge")
                    return challenge
        except:
            pass

    # Get raw body for signature verification
    raw_body = await request.body()

    # Get Slack signature headers
    timestamp, signature = get_slack_headers(request)

    if not timestamp or not signature:
        logger.warning("Missing Slack signature headers", client_ip=request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Missing signature headers")

    # Verify signature
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not verify_slack_signature(raw_body, timestamp, signature, signing_secret):
        logger.warning("Invalid Slack signature", client_ip=request.client.host if request.client else "unknown", timestamp=timestamp)
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        # Parse JSON body
        event_data = await request.json()

        # Process the Slack event
        result = await process_slack_event(event_data)

        if result:
            return {"status": "success", "message": "Event processed"}
        else:
            return {"status": "ignored", "message": "Event type not handled"}

    except Exception as e:
        logger.error("Error processing Slack event", error=str(e), event_type=event_data.get("type", "unknown") if 'event_data' in locals() else "unknown")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/cases")
async def list_cases(db: Session = Depends(get_db)):
    """
    List all cases (development/debugging endpoint).

    Args:
        db: Database session

    Returns:
        dict: List of cases with basic information
    """
    # In production, this would require authentication
    if not os.getenv("DEBUG", "").lower() == "true":
        raise HTTPException(status_code=403, detail="Debug endpoint disabled")

    from .db import Case

    cases = db.query(Case).all()

    return {
        "cases": [
            {
                "case_id": case.case_id,
                "customer_identifier": case.customer_identifier,
                "status": case.status,
                "created_at": case.created_at.isoformat(),
                "last_message_at": case.last_message_at.isoformat(),
                "message_count": case.message_count,
                "escalated": case.escalated,
                "closed_at": case.closed_at.isoformat() if case.closed_at else None
            }
            for case in cases
        ]
    }


@app.get("/")
async def root():
    """
    Root endpoint with basic information.

    Returns:
        dict: Basic system information
    """
    return {
        "name": "Case Management System",
        "version": "1.0.0",
        "description": "Self-hosted customer case management and escalation system",
        "endpoints": {
            "health": "/health",
            "slack_webhook": "/slack/events",
            "cases": "/cases (debug only)"
        }
    }


# Optional: Add request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    """
    Middleware to log HTTP requests.

    Args:
        request: The incoming request
        call_next: The next middleware function

    Returns:
        The response from the next middleware
    """
    start_time = __import__("time").time()

    response = await call_next(request)

    process_time = __import__("time").time() - start_time

    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s"
    )

    return response


if __name__ == "__main__":
    import uvicorn

    # Get configuration from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    debug = os.getenv("DEBUG", "false").lower() == "true"

    logger.info(f"Starting server on {host}:{port}")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )
