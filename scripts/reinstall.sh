#!/bin/bash
#
# Taurus Supervisor Reinstall Script
#
# Stop service → Keep configuration and data → Reinstall binaries → Restart service
#
# Usage:
#   sudo bash reinstall.sh                           # Use server URL from current configuration
#   sudo bash reinstall.sh --server https://...      # Specify new server URL
#   sudo bash reinstall.sh --version 1.1.0           # Install specific version
#   bash reinstall.sh                                # Reinstall as regular user
#

set -euo pipefail

SERVER_URL=""
SUPERVISOR_VERSION=""
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
        --server)
            SERVER_URL="$2"
            shift 2
            ;;
        --version)
            SUPERVISOR_VERSION="$2"
            shift 2
            ;;
        --force)
            FORCE=true
            shift
            ;;
        -h|--help)
            echo "Taurus Supervisor Reinstall Script"
            echo ""
            echo "Usage: bash reinstall.sh [options]"
            echo ""
            echo "Options:"
            echo "  --server <URL>     Specify new server address (default: use current configuration)"
            echo "  --version <VER>    Install specific version (default: use current version)"
            echo "  --force            Skip confirmation prompt"
            echo "  -h, --help         Show help"
            echo ""
            echo "Examples:"
            echo "  sudo bash reinstall.sh                           # Reinstall using current config"
            echo "  sudo bash reinstall.sh --server https://...      # Specify new server address"
            echo "  sudo bash reinstall.sh --version 1.1.0           # Specify version"
            echo "  bash reinstall.sh                                # Reinstall as regular user"
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
    echo -e "${YELLOW}This operation will reinstall Taurus Supervisor (keeping configuration and data)${NC}"
    echo ""
    read -rp "Confirm reinstall? [y/N] " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        log_info "Reinstall cancelled"
        exit 0
    fi
fi

log_info "Starting Taurus Supervisor reinstall..."

# Read current configuration
CONFIG_FILE=""
if [[ "$IS_ROOT" == "true" ]]; then
    CONFIG_FILE="/etc/taurus-supervisor/config.json"
    SUPERVISOR_DIR="/opt/taurus/supervisor"
    BASE_DIR="/opt/taurus"
else
    CONFIG_FILE="$HOME/.taurus-supervisor/config.json"
    SUPERVISOR_DIR="$HOME/taurus/supervisor"
    BASE_DIR="$HOME/taurus"
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error "Configuration file does not exist: $CONFIG_FILE"
    log_error "Please install Taurus Supervisor first"
    exit 1
fi

HOST_ID=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('host_id',''))" "$CONFIG_FILE" 2>/dev/null || true)
CURRENT_SERVER_URL=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('server_url',''))" "$CONFIG_FILE" 2>/dev/null || true)
CURRENT_VERSION=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('version',''))" "$CONFIG_FILE" 2>/dev/null || true)

if [[ -z "$SERVER_URL" ]]; then
    SERVER_URL="$CURRENT_SERVER_URL"
fi

if [[ -z "$SUPERVISOR_VERSION" ]]; then
    SUPERVISOR_VERSION="$CURRENT_VERSION"
fi

if [[ -z "$HOST_ID" ]]; then
    log_error "host_id not found in configuration file, please install Taurus Supervisor first"
    exit 1
fi

if [[ -z "$SERVER_URL" ]]; then
    log_error "Server address not specified, please use --server argument or confirm server_url exists in configuration file"
    exit 1
fi

log_info "Current configuration:"
log_info "  Host ID: $HOST_ID"
log_info "  Server: $SERVER_URL"
log_info "  Current version: $CURRENT_VERSION"
log_info "  Target version: ${SUPERVISOR_VERSION:-latest}"

# Stop Supervisor service
log_info "Stopping Supervisor service..."

if [[ "$IS_ROOT" == "true" ]]; then
    if systemctl is-active --quiet taurus-supervisor 2>/dev/null; then
        systemctl stop taurus-supervisor
        log_info "  ✓ systemd service stopped"
    fi
else
    if command -v systemctl &> /dev/null && systemctl --user is-active --quiet taurus-supervisor 2>/dev/null; then
        systemctl --user stop taurus-supervisor
        log_info "  ✓ User systemd service stopped"
    fi

    if [[ -f "$BASE_DIR/supervisor.pid" ]]; then
        PID=$(cat "$BASE_DIR/supervisor.pid" 2>/dev/null || true)
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 3
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            log_info "  ✓ Background process stopped (PID: $PID)"
        fi
        rm -f "$BASE_DIR/supervisor.pid"
    fi
fi

# Backup current binary
if [[ -f "$SUPERVISOR_DIR/bin/taurus-supervisor" ]]; then
    BACKUP_DIR="${SUPERVISOR_DIR}/backup"
    mkdir -p "$BACKUP_DIR"
    cp "$SUPERVISOR_DIR/bin/taurus-supervisor" "$BACKUP_DIR/taurus-supervisor.bak.$(date +%Y%m%d%H%M%S)"
    log_info "  ✓ Current binary backed up to $BACKUP_DIR"
fi

# Download new version
log_info "Downloading Taurus Supervisor..."
PLATFORM="linux"
ARCH=$(uname -m)

SUPERVISOR_URL="${SERVER_URL}/api/taurus/supervisor/download/?package_type=supervisor&platform=${PLATFORM}&arch=${ARCH}"

TMP_DIR=$(mktemp -d)
TMP_FILE="${TMP_DIR}/taurus-supervisor"

HTTP_CODE=$(curl -s -w "%{http_code}" --retry 3 --retry-delay 2 -o "$TMP_FILE" "$SUPERVISOR_URL")

if [[ "$HTTP_CODE" != "200" ]]; then
    log_error "Supervisor download failed (HTTP $HTTP_CODE)"
    log_info "Restoring backup..."
    LATEST_BACKUP=$(ls -t "${SUPERVISOR_DIR}/backup/"taurus-supervisor.bak.* 2>/dev/null | head -1)
    if [[ -n "$LATEST_BACKUP" ]]; then
        cp "$LATEST_BACKUP" "$SUPERVISOR_DIR/bin/taurus-supervisor"
        chmod +x "$SUPERVISOR_DIR/bin/taurus-supervisor"
        log_info "  ✓ Backup restored"
    fi
    rm -rf "$TMP_DIR"
    exit 1
fi

chmod +x "$TMP_FILE"
cp "$TMP_FILE" "$SUPERVISOR_DIR/bin/taurus-supervisor"
log_info "  ✓ Supervisor updated: $SUPERVISOR_DIR/bin/taurus-supervisor"

rm -rf "$TMP_DIR"

# Update version number in configuration
if [[ -n "$SUPERVISOR_VERSION" ]]; then
    python3 -c "import json,sys; c=json.load(open(sys.argv[1])); c['version']=sys.argv[2]; json.dump(c,open(sys.argv[1],'w'),indent=2)" "$CONFIG_FILE" "$SUPERVISOR_VERSION" 2>/dev/null || true
    log_info "  ✓ Configuration version updated to $SUPERVISOR_VERSION"
fi

# Restart Supervisor service
log_info "Restarting Supervisor service..."

if [[ "$IS_ROOT" == "true" ]]; then
    if [[ -f /etc/systemd/system/taurus-supervisor.service ]]; then
        systemctl start taurus-supervisor
        sleep 3
        if systemctl is-active --quiet taurus-supervisor; then
            log_info "  ✓ Supervisor service restarted"
        else
            log_error "Supervisor service failed to start, please check logs: journalctl -u taurus-supervisor -n 50"
            exit 1
        fi
    else
        log_warn "systemd service file not found, please manually start Supervisor"
    fi
else
    if command -v systemctl &> /dev/null && systemctl --user is-system-running &> /dev/null 2>&1; then
        if [[ -f "$HOME/.config/systemd/user/taurus-supervisor.service" ]]; then
            systemctl --user start taurus-supervisor
            sleep 3
            if systemctl --user is-active --quiet taurus-supervisor; then
                log_info "  ✓ Supervisor user service restarted"
            else
                log_error "Supervisor service failed to start, please check logs: journalctl --user -u taurus-supervisor -n 50"
                exit 1
            fi
        else
            log_warn "User systemd service file not found, please manually start Supervisor"
        fi
    else
        if [[ -f "$BASE_DIR/start-supervisor.sh" ]]; then
            nohup "$BASE_DIR/start-supervisor.sh" > "$BASE_DIR/supervisor.log" 2>&1 &
            PID=$!
            echo $PID > "$BASE_DIR/supervisor.pid"
            sleep 3
            if kill -0 "$PID" 2>/dev/null; then
                log_info "  ✓ Supervisor restarted (PID: $PID)"
            else
                log_error "Supervisor failed to start, please check logs: tail -f $BASE_DIR/supervisor.log"
                exit 1
            fi
        else
            log_warn "Startup script not found, please manually start Supervisor"
        fi
    fi
fi

# Clean old backups (keep last 3)
BACKUP_DIR="${SUPERVISOR_DIR}/backup"
if [[ -d "$BACKUP_DIR" ]]; then
    BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}/"taurus-supervisor.bak.* 2>/dev/null | wc -l)
    if [[ "$BACKUP_COUNT" -gt 3 ]]; then
        ls -t "${BACKUP_DIR}/"taurus-supervisor.bak.* | tail -n +4 | xargs rm -f 2>/dev/null || true
        log_info "  ✓ Old backups cleaned (keeping last 3)"
    fi
fi

log_info ""
log_info "=========================================="
log_info "  Taurus Supervisor reinstall complete"
log_info "=========================================="
log_info "  Version: ${SUPERVISOR_VERSION:-latest}"
log_info "  Configuration: $CONFIG_FILE (kept)"
log_info "  Data: $BASE_DIR/data (kept)"