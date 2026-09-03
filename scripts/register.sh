#!/bin/bash
#
# ⚠️ This script is deprecated, please use install.sh downloaded from the server
# Usage: curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token <TOKEN>
#
# Taurus Supervisor Registration Script
# Usage: ./register.sh --server <SERVER_URL> --token <TOKEN> [--extra-info JSON]
#
# Examples:
#   ./register.sh --server https://taurus.example.com --token abc123
#   ./register.sh --server https://taurus.example.com --token abc123 --extra-info '{"cmdb_id":"CMDB-001","department":"ops"}'
#

set -euo pipefail

echo -e "\033[1;33m[WARN] This script is deprecated, it is recommended to use install.sh downloaded from the server\033[0m"
echo -e "\033[1;33m[WARN] Usage: curl -fsSL <SERVER>/api/taurus/supervisor/install_script/ | bash -s -- --token <TOKEN>\033[0m"
echo ""

SERVER_URL=""
TOKEN=""
EXTRA_INFO="{}"
SUPERVISOR_VERSION="1.0.0"
AUTO_INSTALL=false

# Detect current user type
CURRENT_USER=$(whoami)
IS_ROOT=false
if [[ "$CURRENT_USER" == "root" ]]; then
    IS_ROOT=true
    KEY_DIR="/etc/taurus-supervisor"
    BASE_DIR="/opt/taurus"
    SUPERVISOR_DIR="/opt/taurus/supervisor"
    VERSIONS_DIR="/opt/taurus/versions"
else
    KEY_DIR="$HOME/.taurus-supervisor"
    BASE_DIR="$HOME/taurus"
    SUPERVISOR_DIR="$HOME/taurus/supervisor"
    VERSIONS_DIR="$HOME/taurus/versions"
fi

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --server)
            SERVER_URL="$2"
            shift 2
            ;;
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --extra-info)
            EXTRA_INFO="$2"
            shift 2
            ;;
        --version)
            SUPERVISOR_VERSION="$2"
            shift 2
            ;;
        --install-dir)
            BASE_DIR="$2"
            SUPERVISOR_DIR="$2/supervisor"
            VERSIONS_DIR="$2/versions"
            shift 2
            ;;
        --auto-install)
            AUTO_INSTALL=true
            shift
            ;;
        -h|--help)
            echo "Taurus Supervisor Registration Script"
            echo ""
            echo "Usage: $0 --server <SERVER_URL> --token <TOKEN> [options]"
            echo ""
            echo "Options:"
            echo "  --server <URL>      Taurus server address (required)"
            echo "  --token <TOKEN>     Registration token (required)"
            echo "  --extra-info <JSON> Extra information, such as CMDB data (optional)"
            echo "  --version <VER>     Supervisor version number (default: 1.0.0)"
            echo "  --install-dir <DIR> Install directory (root default:/opt/taurus, regular user default:~/taurus)"
            echo "  --auto-install      Automatically download, install, configure and start Supervisor (one-click install)"
            echo "  -h, --help          Show help"
            echo ""
            echo "Examples:"
            echo "  ./register.sh --server https://taurus.example.com --token abc123"
            echo "  ./register.sh --server https://taurus.example.com --token abc123 --auto-install"
            echo "  ./register.sh --server https://taurus.example.com --token abc123 --extra-info '{\"cmdb_id\":\"CMDB-001\"}'"
            exit 0
            ;;
        *)
            log_error "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Check required arguments
if [[ -z "$SERVER_URL" ]]; then
    log_error "Please specify --server argument"
    exit 1
fi

if [[ -z "$TOKEN" ]]; then
    log_error "Please specify --token argument"
    exit 1
fi

# Check dependencies
if ! command -v curl &> /dev/null; then
    log_error "curl is not installed, please install curl first"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log_warn "jq is not installed, will use python to parse JSON"
    JSON_PARSER="python3"
else
    JSON_PARSER="jq"
fi

# Collect host information
log_info "Collecting host information..."

HOSTNAME=$(hostname)
IP_ADDR=$(hostname -I | awk '{print $1}' || hostname -i | awk '{print $1}')
OS_TYPE=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
OS_VERSION=$(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'"' -f2 || echo "unknown")

log_info "Hostname: $HOSTNAME"
log_info "IP address: $IP_ADDR"
log_info "Operating system: $OS_TYPE ($OS_VERSION)"
log_info "Architecture: $ARCH"

# Send registration request
log_info "Sending registration request to $SERVER_URL ..."

REGISTER_URL="${SERVER_URL}/api/taurus/supervisor/register/"

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$REGISTER_URL" \
    -H "Content-Type: application/json" \
    -d "{
        \"token\": \"$TOKEN\",
        \"host_info\": {
            \"hostname\": \"$HOSTNAME\",
            \"ip\": \"$IP_ADDR\",
            \"port\": 22,
            \"os\": \"$OS_TYPE\",
            \"arch\": \"$ARCH\",
            \"os_version\": \"$OS_VERSION\",
            \"extra_info\": $EXTRA_INFO
        },
        \"supervisor_version\": \"$SUPERVISOR_VERSION\"
    }" 2>/dev/null)

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" != "200" ]]; then
    log_error "Registration failed (HTTP $HTTP_CODE)"
    log_error "Response: $BODY"
    exit 1
fi

# Parse response
if [[ "$JSON_PARSER" == "jq" ]]; then
    HOST_ID=$(echo "$BODY" | jq -r '.data.host_id')
    STATUS=$(echo "$BODY" | jq -r '.data.status')
    STATUS_DISPLAY=$(echo "$BODY" | jq -r '.data.status_display')
    MESSAGE=$(echo "$BODY" | jq -r '.data.message')
    HB_SERVER_URL=$(echo "$BODY" | jq -r '.data.heartbeat.server_url // empty')
    HB_INTERVAL=$(echo "$BODY" | jq -r '.data.heartbeat.interval // 30')
    HB_TIMEOUT=$(echo "$BODY" | jq -r '.data.heartbeat.timeout // 10')
else
    HOST_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['host_id'])")
    STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['status'])")
    STATUS_DISPLAY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['status_display'])")
    MESSAGE=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['message'])")
    HB_SERVER_URL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('server_url',''))")
    HB_INTERVAL=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('interval',30))")
    HB_TIMEOUT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); hb=d['data'].get('heartbeat',{}); print(hb.get('timeout',10))")
fi

log_info "Registration successful!"
log_info "Host ID: $HOST_ID"
log_info "Status: $STATUS_DISPLAY"
log_info "Message: $MESSAGE"

# Save configuration (includes heartbeat config returned from Server)
log_info "Saving Supervisor configuration..."

# Use python3 to generate JSON, avoiding shell escaping issues
python3 -c "
import json, sys
config = {
    'host_id': '$HOST_ID',
    'server_url': '$SERVER_URL',
    'version': '$SUPERVISOR_VERSION',
    'registered_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
}
hb = {}
if '$HB_SERVER_URL':
    hb['server_url'] = '$HB_SERVER_URL'
if '$HB_INTERVAL':
    hb['interval'] = int('$HB_INTERVAL')
if '$HB_TIMEOUT':
    hb['timeout'] = int('$HB_TIMEOUT')
if hb:
    config['heartbeat'] = hb
print(json.dumps(config, indent=2))
" > "$KEY_DIR/config.json"

chmod 600 "$KEY_DIR/config.json"

# Display heartbeat configuration
if [[ -n "$HB_SERVER_URL" ]]; then
    log_info "Heartbeat configuration automatically saved:"
    log_info "  Server: $HB_SERVER_URL"
    log_info "  Interval: ${HB_INTERVAL}s"
    log_info "  Timeout: ${HB_TIMEOUT}s"
fi

# Prompt based on status
if [[ "$STATUS" == "0" ]]; then
    log_warn "Host is waiting for administrator approval, approval required before normal use"
    log_info "Please notify the administrator to approve the host in the Taurus backend: $HOST_ID"
elif [[ "$STATUS" == "1" ]]; then
    log_info "Host has been automatically approved, ready for use"
fi

log_info "Supervisor configuration file: $KEY_DIR/config.json"
log_info ""
log_info "Registration complete!"

# Auto-install feature
if [[ "$AUTO_INSTALL" == "true" ]]; then
    log_info "=========================================="
    log_info "  Starting automatic Taurus Supervisor installation"
    log_info "=========================================="
    log_info "Current user: $CURRENT_USER"
    log_info "Install mode: $([ "$IS_ROOT" = true ] && echo 'System-level (root)' || echo 'User-level (regular user)')"
    log_info "Base directory: $BASE_DIR"
    log_info "Supervisor directory: $SUPERVISOR_DIR"
    log_info "Versions directory: $VERSIONS_DIR"
    log_info "Configuration directory: $KEY_DIR"
    log_info "=========================================="
    
    # Detect platform and architecture
    PLATFORM="linux"
    ARCH=$(uname -m)
    
    # Create directory structure
    log_info "Creating directory structure..."
    mkdir -p "$BASE_DIR"
    mkdir -p "$SUPERVISOR_DIR/bin"
    mkdir -p "$VERSIONS_DIR"
    mkdir -p "$BASE_DIR/data"
    mkdir -p /var/log/taurus-supervisor
    
    # Download Supervisor
    log_info "Downloading and installing Taurus Supervisor..."
    SUPERVISOR_URL="${SERVER_URL}/api/taurus/supervisor/download-supervisor/?platform=${PLATFORM}&arch=${ARCH}"
    
    TMP_SUPERVISOR=$(mktemp -d)
    SUPERVISOR_FILE="${TMP_SUPERVISOR}/taurus-supervisor"
    
    HTTP_CODE=$(curl -s -w "%{http_code}" -o "$SUPERVISOR_FILE" "$SUPERVISOR_URL")
    
    if [[ "$HTTP_CODE" != "200" ]]; then
        log_error "Supervisor download failed (HTTP $HTTP_CODE)"
        rm -rf "$TMP_SUPERVISOR"
        exit 1
    fi
    
    log_info "Supervisor download successful"
    chmod +x "$SUPERVISOR_FILE"
    
    # Copy to install directory
    cp "$SUPERVISOR_FILE" "$SUPERVISOR_DIR/bin/taurus-supervisor"
    log_info "Supervisor installation complete: $SUPERVISOR_DIR/bin/taurus-supervisor"
    
    rm -rf "$TMP_SUPERVISOR"
    
    # Generate Supervisor state file
    log_info "Generating Supervisor state file..."
    
    cat > "$BASE_DIR/data/state.json" << EOF
{
  "current_version": "${SUPERVISOR_VERSION}",
  "previous_version": null,
  "upgrade_in_progress": false,
  "upgrade_target_version": null,
  "last_successful_version": "${SUPERVISOR_VERSION}",
  "upgrade_history": [],
  "programs": {}
}
EOF
    
    chmod 600 "$BASE_DIR/data/state.json"
    log_info "  ✓ Supervisor state file generated: $BASE_DIR/data/state.json"
    
    # Configure service based on user type
    if [[ "$IS_ROOT" == "true" ]]; then
        # ===== Root user deployment: use systemd system service =====
        log_info "Configuring system-level service (systemd)..."
        
        # Create Supervisor environment variable file
        mkdir -p /etc/taurus-supervisor
        cat > /etc/taurus-supervisor/supervisor.env << EOF
# Taurus Supervisor Configuration
BASE_DIR=${BASE_DIR}
SERVER_URL=${SERVER_URL}
HOST_ID=${HOST_ID}
HEALTH_CHECK_INTERVAL=30
SUPERVISOR_HEARTBEAT_INTERVAL=30
EOF
        
        chmod 600 /etc/taurus-supervisor/supervisor.env
        log_info "  ✓ Supervisor environment variables generated"
        
        # Create Supervisor systemd service file
        cat > /etc/systemd/system/taurus-supervisor.service << EOF
[Unit]
Description=Taurus Supervisor - Universal program management daemon
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${BASE_DIR}
ExecStart=${SUPERVISOR_DIR}/bin/taurus-supervisor
Restart=always
RestartSec=3
EnvironmentFile=/etc/taurus-supervisor/supervisor.env

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=taurus-supervisor

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BASE_DIR} /var/log/taurus-supervisor

[Install]
WantedBy=multi-user.target
EOF
        
        systemctl daemon-reload
        log_info "  ✓ Supervisor systemd service created"
        
        # Start Supervisor service
        log_info "Starting Taurus Supervisor system service..."
        systemctl enable taurus-supervisor
        systemctl start taurus-supervisor
        
        # Wait for service to start
        sleep 3
        
        # Check service status
        if systemctl is-active --quiet taurus-supervisor; then
            log_info "  ✓ Taurus Supervisor system service started and running normally"
            log_info "  ✓ Supervisor will automatically download and manage managed programs (such as taurus-executor, taurus-monitor or custom programs)"
        else
            log_warn "Supervisor service failed to start, please check logs: journalctl -u taurus-supervisor -n 50"
        fi
        
    else
        # ===== Regular user deployment: use systemd --user or background process =====
        log_info "Configuring user-level service..."
        
        # Check if systemd --user is supported
        if command -v systemctl &> /dev/null && systemctl --user is-system-running &> /dev/null 2>&1; then
            log_info "Detected systemd --user support, configuring user service..."
            
            # Create user service directory
            mkdir -p "$HOME/.config/systemd/user"
            
            # Create user service file
            cat > "$HOME/.config/systemd/user/taurus-supervisor.service" << EOF
[Unit]
Description=Taurus Supervisor - Universal program management daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=${BASE_DIR}
ExecStart=${SUPERVISOR_DIR}/bin/taurus-supervisor
Restart=always
RestartSec=3
Environment=BASE_DIR=${BASE_DIR}
Environment=SERVER_URL=${SERVER_URL}
Environment=HOST_ID=${HOST_ID}

[Install]
WantedBy=default.target
EOF
            
            # Reload user service
            systemctl --user daemon-reload
            
            # Enable and start user service
            systemctl --user enable taurus-supervisor
            systemctl --user start taurus-supervisor
            
            # Wait for service to start
            sleep 3
            
            # Check service status
            if systemctl --user is-active --quiet taurus-supervisor; then
                log_info "  ✓ Taurus Supervisor user service started and running normally"
                log_info "  ✓ Supervisor will automatically download and manage managed programs (such as taurus-executor, taurus-monitor or custom programs)"
            else
                log_warn "User service failed to start, falling back to background process mode"
                # Fall back to background process mode
                nohup "$SUPERVISOR_DIR/bin/taurus-supervisor" > "$BASE_DIR/supervisor.log" 2>&1 &
                echo $! > "$BASE_DIR/supervisor.pid"
                log_info "  ✓ Taurus Supervisor started in background process mode"
            fi
            
        else
            # systemd --user not supported, use background process
            log_info "systemd --user is not available, using background process mode..."
            
            # Create startup script
            cat > "$BASE_DIR/start-supervisor.sh" << EOF
#!/bin/bash
set -e

SUPERVISOR_DIR="${SUPERVISOR_DIR}"
BASE_DIR="${BASE_DIR}"

cd "\$BASE_DIR"

export BASE_DIR="\$BASE_DIR"
export SERVER_URL="${SERVER_URL}"
export HOST_ID="${HOST_ID}"

echo "[INFO] Starting Taurus Supervisor..."
exec "\$SUPERVISOR_DIR/bin/taurus-supervisor"
EOF
            chmod +x "$BASE_DIR/start-supervisor.sh"
            
            # Start background process
            nohup "$BASE_DIR/start-supervisor.sh" > "$BASE_DIR/supervisor.log" 2>&1 &
            PID=$!
            echo $PID > "$BASE_DIR/supervisor.pid"
            
            # Wait for process to start
            sleep 3
            
            # Check if process is running
            if kill -0 $PID 2>/dev/null; then
                log_info "  ✓ Taurus Supervisor started in background process mode (PID: $PID)"
            else
                log_error "Supervisor failed to start, please check logs: tail -f $BASE_DIR/supervisor.log"
                exit 1
            fi
        fi
    fi
    
    # Display completion information
    log_info ""
    log_info "=========================================="
    log_info "  Taurus Supervisor installation complete!"
    log_info "  Managed programs (such as taurus-executor) will be automatically managed by Supervisor"
    log_info "=========================================="
    log_info ""
    log_info "Base directory: $BASE_DIR"
    log_info "Supervisor directory: $SUPERVISOR_DIR"
    log_info "Configuration directory: $KEY_DIR/config.json"
    log_info ""
    
    if [[ "$IS_ROOT" == "true" ]]; then
        log_info "Service management (system-level):"
        log_info "  Check status: sudo systemctl status taurus-supervisor"
        log_info "  View logs: sudo journalctl -u taurus-supervisor -f"
        log_info "  Restart service: sudo systemctl restart taurus-supervisor"
        log_info "  Stop service: sudo systemctl stop taurus-supervisor"
        log_info "  Disable service: sudo systemctl disable taurus-supervisor"
    else
        if command -v systemctl &> /dev/null && systemctl --user is-system-running &> /dev/null 2>&1; then
            log_info "Service management (user-level systemd):"
            log_info "  Check status: systemctl --user status taurus-supervisor"
            log_info "  View logs: journalctl --user -u taurus-supervisor -f"
            log_info "  Restart service: systemctl --user restart taurus-supervisor"
            log_info "  Stop service: systemctl --user stop taurus-supervisor"
            log_info "  Disable service: systemctl --user disable taurus-supervisor"
        else
            log_info "Process management (background process):"
            log_info "  Check status: ps aux | grep taurus-supervisor"
            log_info "  View logs: tail -f $BASE_DIR/supervisor.log"
            log_info "  Restart service: kill \$(cat $BASE_DIR/supervisor.pid) && $BASE_DIR/start-supervisor.sh"
            log_info "  Stop service: kill \$(cat $BASE_DIR/supervisor.pid)"
        fi
    fi
    
    log_info ""
    log_info "Version management:"
    log_info "  View version directory: ls -la $VERSIONS_DIR/"
    log_info "  Supervisor will automatically manage the lifecycle of managed programs"
fi