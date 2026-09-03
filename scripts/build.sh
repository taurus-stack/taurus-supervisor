#!/bin/bash
set -e

# ==========================================
# Taurus Supervisor Build Script
# ==========================================
# Usage:
#   bash scripts/build.sh                    # Use default version 1.0.0
#   bash scripts/build.sh --version 1.1.0    # Specify version
#   VERSION=1.1.0 bash scripts/build.sh      # Specify version via env var
# ==========================================

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: bash scripts/build.sh -v VERSION [-h|--help]"
            echo ""
            echo "Options:"
            echo "  -v, --version VERSION    Specify version number (required)"
            echo "  -h, --help               Show help message"
            echo ""
            echo "Examples:"
            echo "  bash scripts/build.sh -v 1.1.0           # Specify version number"
            echo "  bash scripts/build.sh --version 1.1.0    # Specify version number"
            echo "  VERSION=1.1.0 bash scripts/build.sh      # Specify version via environment variable"
            echo ""
            echo "Outputs:"
            echo "  dist/taurus-supervisor    - Supervisor binary file"
            echo "  dist/taurus-pm            - Process management tool binary"
            echo "  dist/*.tar.gz             - Release archive"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Use -h or --help to view help information"
            exit 1
            ;;
    esac
done

# Check if version argument is specified
if [[ -z "$VERSION" ]]; then
    echo "Error: Version number must be specified"
    echo "Use -h or --help to view help information"
    exit 1
fi

# Get script directory and project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build"
DIST_DIR="${PROJECT_ROOT}/dist"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Print build info
echo ""
echo "========================================="
echo "  Taurus Supervisor Build"
echo "========================================="
echo "  Version: ${VERSION}"
echo "  Platform: $(uname -s)"
echo "  Architecture: $(uname -m)"
echo "  Python: $(python3 --version 2>&1 || echo 'N/A')"
echo "  Project directory: ${PROJECT_ROOT}"
echo "========================================="
echo ""

# Check if Poetry is installed
if ! command -v poetry &> /dev/null; then
    log_error "Poetry is not installed, please install Poetry first"
    exit 1
fi

# Check if PyInstaller is installed
if ! poetry run pyinstaller --version &> /dev/null; then
    log_warning "PyInstaller is not installed, installing now..."
    poetry install --with dev
fi

# Clean old build files
log_info "Cleaning old build files..."
rm -rf "${BUILD_DIR}"
rm -rf "${DIST_DIR}"
rm -f "${PROJECT_ROOT}/taurus-supervisor.spec"

# Install dependencies
log_info "Installing dependencies..."
cd "${PROJECT_ROOT}"
poetry install --no-root

# Build with PyInstaller
log_info "Starting build..."
poetry run pyinstaller \
    --name taurus-supervisor \
    --onefile \
    --clean \
    --noconfirm \
    --add-data "taurus_supervisor:taurus_supervisor" \
    --hidden-import aiohttp \
    --hidden-import aiohttp.web \
    --hidden-import aiohttp.client \
    --hidden-import psutil \
    --hidden-import yaml \
    --exclude-module tkinter \
    --exclude-module unittest \
    taurus_supervisor/main.py

# Check if build was successful
if [ ! -f "${DIST_DIR}/taurus-supervisor" ]; then
    log_error "Build failed, output file not found"
    exit 1
fi

# Display file size
BINARY_SIZE=$(du -h "${DIST_DIR}/taurus-supervisor" | cut -f1)
log_success "taurus-supervisor built successfully, file size: ${BINARY_SIZE}"

# Build taurus-pm
log_info "Building taurus-pm..."
poetry run pyinstaller \
    --name taurus-pm \
    --onefile \
    --clean \
    --noconfirm \
    --add-data "taurus_pm:taurus_pm" \
    --add-data "taurus_supervisor:taurus_supervisor" \
    --hidden-import yaml \
    --hidden-import psutil \
    --exclude-module tkinter \
    --exclude-module unittest \
    --exclude-module aiohttp \
    taurus_pm/main.py

if [ ! -f "${DIST_DIR}/taurus-pm" ]; then
    log_error "taurus-pm build failed, output file not found"
    exit 1
fi

PM_BINARY_SIZE=$(du -h "${DIST_DIR}/taurus-pm" | cut -f1)
log_success "taurus-pm built successfully, file size: ${PM_BINARY_SIZE}"

# Create release package
log_info "Creating release package..."
mkdir -p "${DIST_DIR}/release"

# Copy binary files
cp "${DIST_DIR}/taurus-supervisor" "${DIST_DIR}/release/"
cp "${DIST_DIR}/taurus-pm" "${DIST_DIR}/release/"

# Create version file
cat > "${DIST_DIR}/release/VERSION" << EOF
${VERSION}
EOF

# Create README
cat > "${DIST_DIR}/release/README.md" << EOF
# Taurus Supervisor v${VERSION}

## One-Click Install (Recommended)

Download the install script from the server and execute it automatically. The script will automatically inject the server address:

\`\`\`bash
# Root user (system-level install)
curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token <TOKEN> --auto-install

# Regular user (user-level install)
curl -fsSL https://taurus.example.com/api/taurus/supervisor/install_script/ | bash -s -- --token <TOKEN> --auto-install
\`\`\`

## Manual Installation

\`\`\`bash
# Extract release package
tar xzf taurus-supervisor-${VERSION}-linux-$(uname -m).tar.gz
cd taurus-supervisor-${VERSION}-linux-$(uname -m)

# Root user
sudo mkdir -p /opt/taurus/supervisor/bin /etc/taurus-supervisor /var/log/taurus-supervisor
sudo cp taurus-supervisor /opt/taurus/supervisor/bin/
sudo cp taurus-pm /opt/taurus/supervisor/bin/
sudo chmod +x /opt/taurus/supervisor/bin/*
sudo cp templates/supervisor.env.example /etc/taurus-supervisor/supervisor.env
sudo cp templates/systemd/taurus-supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable taurus-supervisor
sudo systemctl start taurus-supervisor

# Regular user
mkdir -p ~/taurus/supervisor/bin ~/.taurus-supervisor ~/taurus/logs
cp taurus-supervisor ~/taurus/supervisor/bin/
cp taurus-pm ~/taurus/supervisor/bin/
chmod +x ~/taurus/supervisor/bin/*
\`\`\`

## Check Status

\`\`\`bash
# Root user
sudo systemctl status taurus-supervisor
sudo journalctl -u taurus-supervisor -f

# Regular user
systemctl --user status taurus-supervisor
journalctl --user -u taurus-supervisor -f
\`\`\`

## Reinstall

\`\`\`bash
# Root user
sudo bash scripts/reinstall.sh
sudo bash scripts/reinstall.sh --version 1.1.0

# Regular user
bash scripts/reinstall.sh
\`\`\`

## taurus-pm Process Management Tool

\`\`\`bash
# List all configured programs
taurus-pm list

# Start program
taurus-pm start <program_name>

# Stop program
taurus-pm stop <program_name>

# Restart program
taurus-pm restart <program_name>

# Check status
taurus-pm status
taurus-pm status <program_name>

# Specify config file
taurus-pm -c /path/to/config.yaml start
\`\`\`

## Uninstall

\`\`\`bash
# Root user
sudo bash scripts/uninstall.sh              # Full uninstall
sudo bash scripts/uninstall.sh --keep-data  # Keep data

# Regular user
bash scripts/uninstall.sh              # Full uninstall
bash scripts/uninstall.sh --keep-data  # Keep data
\`\`\`
EOF

# Copy deployment files
mkdir -p "${DIST_DIR}/release/templates"
cp -r "${PROJECT_ROOT}/templates"/* "${DIST_DIR}/release/templates/" 2>/dev/null || true

# Copy management scripts
mkdir -p "${DIST_DIR}/release/scripts"
cp "${PROJECT_ROOT}/scripts/uninstall.sh" "${DIST_DIR}/release/scripts/"
cp "${PROJECT_ROOT}/scripts/reinstall.sh" "${DIST_DIR}/release/scripts/"
chmod +x "${DIST_DIR}/release/scripts/"*.sh

# Package
cd "${DIST_DIR}/release"
PACKAGE_NAME="taurus-supervisor-${VERSION}-linux-$(uname -m)"
tar czf "${DIST_DIR}/${PACKAGE_NAME}.tar.gz" .

# Return to project directory
cd "${PROJECT_ROOT}"

# Display build results
echo ""
echo "========================================="
log_success "Build complete!"
echo "========================================="
echo "  Binary file: ${DIST_DIR}/taurus-supervisor"
echo "  Binary file: ${DIST_DIR}/taurus-pm"
echo "  Release package: ${DIST_DIR}/${PACKAGE_NAME}.tar.gz"
echo "  Release directory: ${DIST_DIR}/release/"
echo "========================================="
echo ""

# List generated files
log_info "Generated files:"
ls -lh "${DIST_DIR}/${PACKAGE_NAME}.tar.gz"
echo ""