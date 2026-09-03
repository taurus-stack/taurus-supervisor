#!/bin/bash
#
# Taurus Supervisor Uninstall Script
#
# Usage:
#   sudo bash uninstall.sh              # Full uninstall
#   sudo bash uninstall.sh --keep-data  # Keep data directory and configuration
#   bash uninstall.sh --force           # Skip confirmation prompt
#

set -euo pipefail

KEEP_DATA=false
FORCE=false

CURRENT_USER=$(whoami)
IS_ROOT=false
if [[ "$CURRENT_USER" == "root" ]]; then
    IS_ROOT=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        -h|--help)
            echo "Taurus Supervisor Uninstall Script"
            echo ""
            echo "Usage: bash uninstall.sh [options]"
            echo ""
            echo "Options:"
            echo "  --keep-data   Keep data directory and configuration files"
            echo "  --force       Skip confirmation prompt"
            echo "  -h, --help    Show help"
            echo ""
            echo "Examples:"
            echo "  sudo bash uninstall.sh              # Full uninstall"
            echo "  sudo bash uninstall.sh --keep-data  # Keep data and config"
            echo "  sudo bash uninstall.sh --force      # Skip confirmation"
            echo "  bash uninstall.sh                   # Regular user uninstall"
            exit 0
            ;;
        *)
            log_error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [[ "$FORCE" != "true" ]]; then
    echo ""
    echo -e "${RED}Warning: This operation will uninstall Taurus Supervisor${NC}"
    if [[ "$KEEP_DATA" != "true" ]]; then
        echo -e "${RED}All programs, configurations, and data files will be deleted${NC}"
    else
        echo -e "${YELLOW}Data directory and configuration files will be kept (--keep-data)${NC}"
    fi
    echo ""
    read -rp "Confirm uninstall? [y/N] " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        log_info "Uninstall cancelled"
        exit 0
    fi
fi

log_info "Starting Taurus Supervisor uninstall..."

# Read configuration
CONFIG_FILE=""
if [[ "$IS_ROOT" == "true" ]]; then
    CONFIG_FILE="/etc/taurus-supervisor/config.json"
else
    CONFIG_FILE="$HOME/.taurus-supervisor/config.json"
fi

HOST_ID=""
SERVER_URL=""
if [[ -f "$CONFIG_FILE" ]]; then
    HOST_ID=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('host_id',''))" "$CONFIG_FILE" 2>/dev/null || true)
    SERVER_URL=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('server_url',''))" "$CONFIG_FILE" 2>/dev/null || true)
    SIGNING_SECRET=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('request_signing_secret',''))" "$CONFIG_FILE" 2>/dev/null || true)
fi

if [[ "$IS_ROOT" == "true" ]]; then
    SUPERVISOR_DIR="/opt/taurus/supervisor"
else
    SUPERVISOR_DIR="$HOME/taurus/supervisor"
fi

# Stop managed programs
if [[ -x "$SUPERVISOR_DIR/bin/taurus-supervisor" ]]; then
    log_info "Attempting to stop all programs via Supervisor..."
    timeout 5 "$SUPERVISOR_DIR/bin/taurus-supervisor" --stop-all 2>/dev/null || true
fi

# Stop Supervisor service
log_info "Stopping Supervisor service..."

if [[ "$IS_ROOT" == "true" ]]; then
    if systemctl is-active --quiet taurus-supervisor 2>/dev/null; then
        systemctl stop taurus-supervisor
        log_info "  ✓ systemd service stopped"
    fi

    if systemctl is-enabled --quiet taurus-supervisor 2>/dev/null; then
        systemctl disable taurus-supervisor
        log_info "  ✓ systemd service disabled"
    fi

    if [[ -f /etc/systemd/system/taurus-supervisor.service ]]; then
        rm -f /etc/systemd/system/taurus-supervisor.service
        systemctl daemon-reload
        log_info "  ✓ systemd service file deleted"
    fi
else
    if command -v systemctl &> /dev/null && systemctl --user is-active --quiet taurus-supervisor 2>/dev/null; then
        systemctl --user stop taurus-supervisor
        systemctl --user disable taurus-supervisor
        rm -f "$HOME/.config/systemd/user/taurus-supervisor.service"
        systemctl --user daemon-reload
        log_info "  ✓ User systemd service stopped and deleted"
    fi

    if [[ -f "$HOME/taurus/supervisor.pid" ]]; then
        PID=$(cat "$HOME/taurus/supervisor.pid" 2>/dev/null || true)
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            log_info "  ✓ Background process stopped (PID: $PID)"
        fi
        rm -f "$HOME/taurus/supervisor.pid"
    fi
fi

# Deregister host from server
if [[ -n "$HOST_ID" ]] && [[ -n "$SERVER_URL" ]]; then
    log_info "Deregistering host from server ($HOST_ID)..."
    DEREGISTER_URL="${SERVER_URL}/api/taurus/supervisor/deregister/"

    DEREGISTER_BODY=$(python3 << PYEOF
import json, time, uuid, hashlib, hmac as hmac_mod
body = {'host_id': r'''${HOST_ID}''', 'signature': '', 'nonce': '', 'timestamp_int': 0}
signing_secret = r'''${SIGNING_SECRET:-}'''
if signing_secret:
    ts = int(time.time())
    nonce = str(uuid.uuid4())
    body['nonce'] = nonce
    body['timestamp_int'] = ts
    body_str = json.dumps(body, sort_keys=True)
    message = f"{r'''${HOST_ID}'''}:{ts}:{nonce}:{body_str}"
    body['signature'] = hmac_mod.new(signing_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
print(json.dumps(body))
PYEOF
)

    HTTP_CODE=$(curl -s -w "%{http_code}" -o /dev/null -X POST "$DEREGISTER_URL" \
        -H "Content-Type: application/json" \
        -d "$DEREGISTER_BODY" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        log_info "  ✓ Host deregistered from server"
    else
        log_warn "  Host deregistration failed (HTTP $HTTP_CODE), you can manually deregister later"
    fi
fi

# Delete files
if [[ "$KEEP_DATA" != "true" ]]; then
    log_info "Deleting files and directories..."

    if [[ "$IS_ROOT" == "true" ]]; then
        rm -rf /opt/taurus/supervisor
        rm -rf /opt/taurus/versions
        rm -rf /opt/taurus/data
        rm -rf /opt/taurus/logs
        rm -f /opt/taurus/start-supervisor.sh
        rm -f /opt/taurus/supervisor.log
        rmdir /opt/taurus 2>/dev/null || true

        rm -rf /etc/taurus-supervisor
        rm -rf /var/log/taurus-supervisor

        log_info "  ✓ System-level files deleted"
    else
        rm -rf "$HOME/taurus/supervisor"
        rm -rf "$HOME/taurus/versions"
        rm -rf "$HOME/taurus/data"
        rm -rf "$HOME/taurus/logs"
        rm -f "$HOME/taurus/start-supervisor.sh"
        rm -f "$HOME/taurus/supervisor.log"
        rm -f "$HOME/taurus/supervisor.pid"
        rmdir "$HOME/taurus" 2>/dev/null || true

        rm -rf "$HOME/.taurus-supervisor"

        log_info "  ✓ User-level files deleted"
    fi
else
    log_info "Keeping data directory and configuration (--keep-data)"
fi

log_info ""
log_info "=========================================="
log_info "  Taurus Supervisor uninstall complete"
log_info "=========================================="

if [[ "$KEEP_DATA" == "true" ]]; then
    log_info "Kept files:"
    if [[ "$IS_ROOT" == "true" ]]; then
        log_info "  Configuration: /etc/taurus-supervisor/"
        log_info "  Data: /opt/taurus/data/"
    else
        log_info "  Configuration: $HOME/.taurus-supervisor/"
        log_info "  Data: $HOME/taurus/data/"
    fi
    log_info ""
    log_info "To completely delete, re-run the uninstall script without --keep-data"
fi