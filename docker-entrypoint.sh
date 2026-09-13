#!/bin/bash
set -euo pipefail

# Fix volume ownership (named volumes may be owned by root)
chown -R taurus:taurus /var/log/taurus-supervisor /opt/taurus 2>/dev/null || true

SERVER_URL="${SERVER_URL:-http://taurus-backend:8000}"
HOST_ID="${HOST_ID:-}"
REGISTER_TOKEN="${REGISTER_TOKEN:-}"
HOST_ID_FILE="/opt/taurus/.host_id"
SIGNING_SECRET_FILE="/opt/taurus/.signing_secret"
REQUEST_SIGNING_SECRET="${REQUEST_SIGNING_SECRET:-}"

is_valid_uuid() {
    local id="$1"
    if [[ "$id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        return 0
    fi
    return 1
}

# Try to load persisted credentials from previous registration
if ! is_valid_uuid "$HOST_ID" && [[ -f "$HOST_ID_FILE" ]]; then
    PERSISTED_ID="$(cat "$HOST_ID_FILE" 2>/dev/null | tr -d '[:space:]')"
    if is_valid_uuid "$PERSISTED_ID"; then
        HOST_ID="$PERSISTED_ID"
        echo "[entrypoint] Loaded persisted HOST_ID from $HOST_ID_FILE: $HOST_ID"
    fi
fi

if [[ -z "$REQUEST_SIGNING_SECRET" && -f "$SIGNING_SECRET_FILE" ]]; then
    PERSISTED_SECRET="$(cat "$SIGNING_SECRET_FILE" 2>/dev/null | tr -d '[:space:]')"
    if [[ -n "$PERSISTED_SECRET" ]]; then
        REQUEST_SIGNING_SECRET="$PERSISTED_SECRET"
        echo "[entrypoint] Loaded persisted signing secret from $SIGNING_SECRET_FILE"
    fi
fi

if is_valid_uuid "$HOST_ID"; then
    echo "[entrypoint] HOST_ID is a valid UUID: $HOST_ID, skipping registration"
else
    if [[ -z "$REGISTER_TOKEN" ]]; then
        echo "[entrypoint] WARNING: HOST_ID='$HOST_ID' is not a valid UUID and REGISTER_TOKEN is not set."
        echo "[entrypoint] The supervisor will start but heartbeat will fail until a valid UUID host_id is configured."
        echo "[entrypoint] To auto-register, set REGISTER_TOKEN in the environment."
    else
        echo "[entrypoint] HOST_ID='$HOST_ID' is not a valid UUID, attempting registration..."

        HOSTNAME_VAL="$(hostname)"
        if [[ -n "${TAURUS_HOST_IP:-}" ]]; then
            IP_ADDR="$TAURUS_HOST_IP"
            echo "[entrypoint] Using TAURUS_HOST_IP override: $IP_ADDR"
        else
            IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}' || hostname -i 2>/dev/null | awk '{print $1}' || echo '127.0.0.1')"
        fi
        OS_TYPE="$(uname -s | tr '[:upper:]' '[:lower:]')"
        ARCH="$(uname -m)"

        REGISTER_URL="${SERVER_URL}/api/taurus/supervisor/register/"

        MAX_RETRIES=10
        RETRY=0
        REGISTERED=false
        while [[ $RETRY -lt $MAX_RETRIES ]]; do
            RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$REGISTER_URL" \
                -H "Content-Type: application/json" \
                -d "{
                    \"token\": \"$REGISTER_TOKEN\",
                    \"host_info\": {
                        \"hostname\": \"$HOSTNAME_VAL\",
                        \"ip\": \"$IP_ADDR\",
                        \"port\": 22,
                        \"os\": \"$OS_TYPE\",
                        \"arch\": \"$ARCH\",
                        \"os_version\": \"docker\",
                        \"extra_info\": {}
                    },
                    \"supervisor_version\": \"1.0.0\"
                }" 2>/dev/null) || true

            HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
            BODY=$(echo "$RESPONSE" | sed '$d')

            if [[ "$HTTP_CODE" == "200" ]]; then
                PARSED=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    data = d.get('data', {}) or {}
    hid = data.get('host_id', '')
    secret = data.get('signing_secret') or ''
    print(hid + '|' + secret)
except Exception as e:
    print('|')
" 2>/dev/null || echo "|")
                NEW_HOST_ID="${PARSED%%|*}"
                NEW_SIGNING_SECRET="${PARSED#*|}"

                if [[ -n "$NEW_HOST_ID" ]] && is_valid_uuid "$NEW_HOST_ID"; then
                    HOST_ID="$NEW_HOST_ID"
                    echo "$HOST_ID" > "$HOST_ID_FILE"
                    chown taurus:taurus "$HOST_ID_FILE" 2>/dev/null || true

                    if [[ -n "$NEW_SIGNING_SECRET" ]]; then
                        REQUEST_SIGNING_SECRET="$NEW_SIGNING_SECRET"
                        echo "$REQUEST_SIGNING_SECRET" > "$SIGNING_SECRET_FILE"
                        chown taurus:taurus "$SIGNING_SECRET_FILE" 2>/dev/null || true
                        chmod 600 "$SIGNING_SECRET_FILE" 2>/dev/null || true
                        echo "[entrypoint] Registration successful, HOST_ID=$HOST_ID (signing secret persisted)"
                    else
                        echo "[entrypoint] Registration successful, HOST_ID=$HOST_ID (no signing secret in response)"
                    fi
                    REGISTERED=true
                    break
                else
                    echo "[entrypoint] Registration response did not contain a valid host_id: $BODY"
                fi
            else
                echo "[entrypoint] Registration attempt $((RETRY+1))/$MAX_RETRIES failed (HTTP $HTTP_CODE): $BODY"
            fi

            RETRY=$((RETRY + 1))
            sleep 3
        done

        if [[ "$REGISTERED" != "true" ]]; then
            echo "[entrypoint] WARNING: Registration failed after $MAX_RETRIES attempts. Starting with HOST_ID='$HOST_ID' (heartbeat may fail)."
        fi
    fi
fi

export HOST_ID
export REQUEST_SIGNING_SECRET
exec su taurus -c "cd /app && HOST_ID='$HOST_ID' REQUEST_SIGNING_SECRET='$REQUEST_SIGNING_SECRET' python -m taurus_supervisor.main"