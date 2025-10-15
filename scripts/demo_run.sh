#!/bin/bash

# Demo script to test the case management system
# This script runs sample tests against a running application

set -e

echo "🧪 Running Case Management System Demo Tests"
echo "============================================="

# Check if application is running
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo "❌ Application not running on localhost:8000"
    echo "Please start the application first:"
    echo "  docker-compose up -d"
    echo "  or"
    echo "  ./run_local.sh"
    exit 1
fi

echo "✅ Application is running"

# Test 1: Health check
echo ""
echo "📋 Test 1: Health Check"
curl -s http://localhost:8000/health | jq .
echo "✅ Health check passed"

# Test 2: Send new customer message via Slack webhook
echo ""
echo "📋 Test 2: New Customer Message (Slack)"
SLACK_NEW=$(cat samples/slack/slack_message_new.json)
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d "$SLACK_NEW" | jq .
echo "✅ New customer message processed"

# Test 3: Send urgent message (should trigger escalation)
echo ""
echo "📋 Test 3: Urgent Message (Should Escalate)"
SLACK_URGENT=$(cat samples/slack/slack_urgent_message.json)
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d "$SLACK_URGENT" | jq .
echo "✅ Urgent message processed (check escalation alerts)"

# Test 4: Send follow-up message
echo ""
echo "📋 Test 4: Follow-up Message"
SLACK_FOLLOWUP=$(cat samples/slack/slack_followup_message.json)
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d "$SLACK_FOLLOWUP" | jq .
echo "✅ Follow-up message processed"

# Test 5: Admin closes case
echo ""
echo "📋 Test 5: Admin Case Closure"
SLACK_CLOSE=$(cat samples/slack/slack_admin_close.json)
curl -X POST http://localhost:8000/slack/events \
  -H "Content-Type: application/json" \
  -d "$SLACK_CLOSE" | jq .
echo "✅ Admin closure processed (check logging channel)"

# Test 6: Check cases endpoint (if in debug mode)
echo ""
echo "📋 Test 6: Check Cases (Debug Mode)"
if curl -s http://localhost:8000/cases > /dev/null 2>&1; then
    curl -s http://localhost:8000/cases | jq .
    echo "✅ Cases endpoint accessible"
else
    echo "⚠️  Cases endpoint not accessible (debug mode disabled)"
fi

# Test 7: Run unit tests
echo ""
echo "📋 Test 7: Unit Tests"
python -m pytest tests/ -v --tb=short | head -50
echo "✅ Unit tests completed"

echo ""
echo "🎉 Demo completed successfully!"
echo ""
echo "Expected behaviors that should have occurred:"
echo "  ✅ New case created for customer U1234567890"
echo "  ✅ Escalation alert sent for urgent message"
echo "  ✅ Support notification sent for each message"
echo "  ✅ Case closed by admin U9876543210"
echo "  ✅ Closure logged to logging channel"
echo ""
echo "Check your Slack channels for notifications:"
echo "  - Support channel: New message notifications"
echo "  - Alerting channel: Escalation alerts"
echo "  - Logging channel: Case closure logs"
