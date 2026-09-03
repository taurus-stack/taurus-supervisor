#!/bin/bash
# Test Supervisor General Program Management Functionality
# Usage: ./test_program_management.sh <server_url> <token> <host_id>

set -e

SERVER_URL=${1:-"http://localhost:8000"}
TOKEN=${2:-""}
HOST_ID=${3:-""}

if [ -z "$TOKEN" ] || [ -z "$HOST_ID" ]; then
    echo "Usage: $0 <server_url> <token> <host_id>"
    echo "Example: $0 http://localhost:8000 your-token 1"
    exit 1
fi

echo "=========================================="
echo "Testing Supervisor General Program Management Functionality"
echo "=========================================="
echo "Server: $SERVER_URL"
echo "Host ID: $HOST_ID"
echo ""

# 1. Test installing custom program
echo "1. Testing installation of custom program (my-custom-app v1.0.0)"
echo "------------------------------------------"
INSTALL_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-install-config/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"my-custom-app\",
    \"version\": \"1.0.0\",
    \"config\": {
      \"port\": 8080,
      \"auto_start\": true,
      \"restart_on_crash\": true
    },
    \"auto_start\": true
  }")

echo "Response: $INSTALL_RESPONSE"
echo ""

# 2. Test installing second custom program
echo "2. Testing installation of second custom program (filebeat v7.17.0)"
echo "------------------------------------------"
INSTALL_RESPONSE2=$(curl -s -X POST "$SERVER_URL/api/taurus/program-install-config/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"filebeat\",
    \"version\": \"7.17.0\",
    \"config\": {
      \"port\": 5044,
      \"auto_start\": true
    },
    \"auto_start\": true
  }")

echo "Response: $INSTALL_RESPONSE2"
echo ""

# 3. Query configured program list
echo "3. Querying configured program list"
echo "------------------------------------------"
LIST_RESPONSE=$(curl -s -X GET "$SERVER_URL/api/taurus/program-install-config/?host=$HOST_ID" \
  -H "Authorization: Bearer $TOKEN")

echo "Response: $LIST_RESPONSE"
echo ""

# 4. Test upgrading program
echo "4. Testing program upgrade (my-custom-app -> v1.1.0)"
echo "------------------------------------------"
UPGRADE_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-command/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"my-custom-app\",
    \"action\": \"upgrade\",
    \"target_version\": \"1.1.0\"
  }")

echo "Response: $UPGRADE_RESPONSE"
echo ""

# 5. Test starting program
echo "5. Testing program start (filebeat)"
echo "------------------------------------------"
START_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-command/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"filebeat\",
    \"action\": \"start\"
  }")

echo "Response: $START_RESPONSE"
echo ""

# 6. Test stopping program
echo "6. Testing program stop (my-custom-app)"
echo "------------------------------------------"
STOP_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-command/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"my-custom-app\",
    \"action\": \"stop\"
  }")

echo "Response: $STOP_RESPONSE"
echo ""

# 7. Test restarting program
echo "7. Testing program restart (filebeat)"
echo "------------------------------------------"
RESTART_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-command/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"filebeat\",
    \"action\": \"restart\"
  }")

echo "Response: $RESTART_RESPONSE"
echo ""

# 8. Test removing program
echo "8. Testing program removal (my-custom-app)"
echo "------------------------------------------"
REMOVE_RESPONSE=$(curl -s -X POST "$SERVER_URL/api/taurus/program-command/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"host\": $HOST_ID,
    \"program_name\": \"my-custom-app\",
    \"action\": \"remove\"
  }")

echo "Response: $REMOVE_RESPONSE"
echo ""

echo "=========================================="
echo "Testing complete!"
echo "=========================================="
echo ""
echo "Please check Supervisor logs to confirm command execution:"
echo "  Root user: sudo journalctl -u taurus-supervisor -f"
echo "  Regular user: journalctl --user -u taurus-supervisor -f"