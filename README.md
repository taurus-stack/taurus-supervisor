# Taurus Supervisor

> 中文版见 [README.zh-CN.md](README.zh-CN.md)

Lightweight general-purpose process management daemon responsible for managing the lifecycle of arbitrary programs.

## Overview

Taurus Supervisor is a **general-purpose program management daemon** that runs on target hosts and can manage any number of programs (such as taurus-executor, taurus-monitor, custom business processes, etc.), responsible for:

- **Process Management**: Start, stop, restart any managed program
- **Upgrade Management**: Download, verify, install, switch to new versions (zero-downtime upgrade)
- **Health Checks**: Periodically check program health status
- **Fault Recovery**: Automatic crash recovery, version rollback
- **Version Management**: Maintain version history and state
- **Heartbeat Reporting**: Report program status and system metrics to Server
- **Command Execution**: Receive and execute management commands from Server

## Architecture

### Overall Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Taurus Server   │◄────────│  Taurus Supervisor│────────►│  Target Host    │
│  (Backend)      │Heartbeat│  (Daemon)         │Manages  │  (Remote Host)  │
│                 │/Commands│                   │Programs │                 │
└─────────────────┘         └────────┬─────────┘         └─────────────────┘
                                     │
                              ┌──────┴───────┐
                              │ Managed       │
                              │ Programs      │
                              │ - executor    │
                              │ - monitor     │
                              │ - agent       │
                              │ - custom-app  │
                              │ - ...         │
                              └──────────────┘
```

### Component Relationships

```
taurus-supervisor (daemon, runs continuously)
    ↓ Manages any number of programs
├── taurus-executor (business process, replaceable)
├── taurus-monitor (monitoring process)
├── taurus-agent (agent process)
├── custom-app (custom application)
└── ... (any other process)
```

## Core Features

### 1. General Program Management

Supervisor is a **general-purpose process manager** that can manage any program, not limited to the taurus series:

- **Any Program**: Can manage taurus-executor, taurus-monitor, custom business processes, third-party applications, etc.
- **Independent Configuration**: Each program has independent version, process control, health checks, upgrade/rollback
- **Dynamic Registration**: Dynamically register new programs through Server commands without modifying Supervisor code
- **Unified Management**: One Supervisor instance can manage multiple different types of programs

**Typical Use Cases**:

| Scenario | Example Programs |
|----------|-----------------|
| Remote Command Execution | taurus-executor |
| System Monitoring | taurus-monitor, prometheus-node-exporter |
| Log Collection | filebeat, fluentd |
| Security Agent | osquery, falco |
| Custom Business Process | Any executable |

### 2. Zero-Downtime Upgrade

Service remains uninterrupted during upgrade:

```
Timeline:
Download (30s - 5min)    → Old version running normally ✅
Install (1-2s)           → Old version running normally ✅
Start new process (3s)   → Old version running normally ✅
Health check (5s)        → Old version running normally ✅
Switch (< 1s)            → Instant switch ⚡
```

**Total downtime: < 1 second** (only during switch moment)

### 3. Automatic Fault Recovery

| Scenario | Recovery Strategy |
|----------|------------------|
| Program crash | Supervisor automatically restarts current version |
| Upgrade failure | Keep old version running, report failure |
| New version fails to start | Rollback to old version |
| Power loss restart | Recover to last stable version from state.json |

### 4. Heartbeat and Command System

- **Periodic Heartbeat**: Report program status, system metrics (CPU, memory, disk, network)
- **Command Distribution**: Receive install, upgrade, start/stop commands from Server
- **Command Persistence**: Recover unexecuted commands after process restart
- **Server Failover**: Support multiple Server nodes, automatic switching

### 5. TLS Certificate Management

- Automatic certificate application and renewal
- Immediate response to certificate revocation
- mTLS mutual authentication ensures communication security

## Directory Structure

### Root User Deployment

```
/opt/taurus/                          # Base directory
├── supervisor/                      # Supervisor daemon
│   ├── bin/
│   │   └── taurus-supervisor         # Supervisor binary
│   └── templates/
│       └── systemd/
│           └── taurus-supervisor.service
│
├── versions/                        # Program version directory
│   ├── v1.0.0/
│   │   ├── taurus-executor/          # Program v1.0.0
│   │   │   ├── taurus-executor       # Program binary
│   │   │   ├── .env                 # Program configuration
│   │   │   └── tls/                 # TLS certificates
│   │   └── taurus-monitor/           # Program v1.0.0
│   │       └── ...
│   ├── v1.1.0/
│   │   └── ...
│   └── current -> v1.1.0           # Symbolic link (current version)
│
└── data/                            # Data directory
    └── state.json                   # Supervisor state file
```

### Regular User Deployment

```
~/taurus/                             # Base directory
├── supervisor/                      # Supervisor daemon
│   └── bin/
│       └── taurus-supervisor
│
├── versions/                        # Program version directory
│   └── v1.0.0/
│       └── taurus-executor/
│           ├── taurus-executor
│           ├── .env
│           └── tls/
│
└── data/
    └── state.json
```

## Installation

### Method 1: One-Click Automatic Installation (Recommended)

Use registration script to automatically complete registration, download, installation, configuration, and startup:

#### Root User

```bash
sudo ./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token> \
  --auto-install
```

**Automatic Configuration**:
- ✅ Base directory: `/opt/taurus`
- ✅ Supervisor directory: `/opt/taurus/supervisor`
- ✅ Version directory: `/opt/taurus/versions/v{version}/`
- ✅ Configuration directory: `/etc/taurus-supervisor`
- ✅ Service type: systemd system service
- ✅ Boot auto-start: Yes

#### Regular User

```bash
./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token> \
  --auto-install
```

**Automatic Configuration**:
- ✅ Base directory: `~/taurus`
- ✅ Supervisor directory: `~/taurus/supervisor`
- ✅ Version directory: `~/taurus/versions/v{version}/`
- ✅ Configuration directory: `~/.taurus-supervisor`
- ✅ Service type: systemd --user or background process
- ✅ Boot auto-start: Depends on linger configuration

### Method 2: Manual Installation

#### 1. Build

```bash
cd taurus-supervisor
bash scripts/build.sh --version 1.0.0
```

#### 2. Register

```bash
./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token>
```

#### 3. Deploy

```bash
# Create directories
sudo mkdir -p /opt/taurus/supervisor
sudo mkdir -p /etc/taurus-supervisor
sudo mkdir -p /var/log/taurus-supervisor

# Install
sudo tar xzf dist/taurus-supervisor-1.0.0-linux-x86_64.tar.gz -C /opt/taurus/supervisor

# Configure
sudo cp templates/supervisor.env.example /etc/taurus-supervisor/supervisor.env
sudo vim /etc/taurus-supervisor/supervisor.env

# Install systemd service
sudo cp templates/systemd/taurus-supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable taurus-supervisor
sudo systemctl start taurus-supervisor
```

## Configuration

### Environment Variable File

**Root User**: `/etc/taurus-supervisor/supervisor.env`

**Regular User**: `~/.taurus-supervisor/supervisor.env`

```bash
# Base directory
BASE_DIR=/opt/taurus

# Server address
SERVER_URL=http://localhost:8000

# Host ID (auto-filled after registration)
HOST_ID=xxx-xxx-xxx

# Health check interval (seconds)
HEALTH_CHECK_INTERVAL=30

# Supervisor heartbeat interval (seconds)
SUPERVISOR_HEARTBEAT_INTERVAL=30

# TLS certificate directory
TLS_DIR=/etc/taurus-supervisor/tls

# Logging configuration
LOG_DIR=/var/log/taurus-supervisor
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

### State File

`/opt/taurus/data/state.json` or `~/taurus/data/state.json`:

```json
{
  "host_id": "xxx-xxx-xxx",
  "current_version": "1.1.0",
  "previous_version": "1.0.0",
  "upgrade_in_progress": false,
  "upgrade_target_version": null,
  "last_successful_version": "1.1.0",
  "upgrade_history": [
    {
      "from_version": "1.0.0",
      "to_version": "1.1.0",
      "timestamp": 1234567890,
      "status": "success"
    }
  ],
  "programs": {
    "taurus-executor": {
      "pid": 12345,
      "port": 50051,
      "status": "running",
      "version": "1.1.0"
    },
    "taurus-monitor": {
      "pid": 12346,
      "port": 9090,
      "status": "running",
      "version": "1.0.0"
    }
  }
}
```

## Usage

### Service Management

#### Root User (systemd system service)

```bash
# Check status
sudo systemctl status taurus-supervisor

# View logs
sudo journalctl -u taurus-supervisor -f

# Restart/stop/enable
sudo systemctl restart taurus-supervisor
sudo systemctl stop taurus-supervisor
sudo systemctl enable taurus-supervisor
sudo systemctl disable taurus-supervisor
```

#### Regular User (systemd --user)

```bash
# Check status
systemctl --user status taurus-supervisor

# View logs
journalctl --user -u taurus-supervisor -f

# Restart/stop/enable
systemctl --user restart taurus-supervisor
systemctl --user stop taurus-supervisor
systemctl --user enable taurus-supervisor
systemctl --user disable taurus-supervisor
```

#### Regular User (background process mode)

```bash
# View process
ps aux | grep taurus-supervisor

# View logs
tail -f ~/taurus/supervisor.log

# Restart service
kill $(cat ~/taurus/supervisor.pid) && ~/taurus/start-supervisor.sh

# Stop service
kill $(cat ~/taurus/supervisor.pid)
```

### Manual Upgrade

```bash
# Trigger via API
curl -X POST http://localhost:8000/api/taurus/host/{host_id}/trigger_update/ \
  -H "Authorization: Bearer <token>" \
  -d '{"target_version": "1.1.0"}'
```

### View Program Status

```bash
# View state file
cat /opt/taurus/data/state.json | jq .programs

# View processes
ps aux | grep taurus-

# View ports
ss -tlnp | grep 5005
```

## Program Management Workflow

### Program Installation Workflow (First Installation)

```
1. Server creates ProgramInstallConfig (specifies program name, version, configuration)
   ↓
2. Supervisor receives install command during heartbeat
   ↓
3. Download program package from Server to versions/v{version}/{program_name}/
   ↓
4. Verify SHA256 checksum
   ↓
5. Extract/install program to version directory
   ↓
6. Generate program configuration files (.env, tls/, etc.)
   ↓
7. Start program process
   ↓
8. Health check (wait 5 seconds)
   ↓
9. Health check passes
   ↓
10. Update state file (state.json)
   ↓
11. Report program running status to Server
   ↓
12. Installation complete ✅
```

### Program Upgrade Workflow (Zero-Downtime)

```
1. Server issues upgrade command (specifies target version)
   ↓
2. Supervisor downloads new version to versions/v{new_version}/{program_name}/
   ↓
3. Verify SHA256 checksum
   ↓
4. Start new version (using temporary port, e.g., original port +1)
   ↓
5. Health check (wait 5 seconds)
   ↓
6. Health check passes
   ↓
7. Stop old version process
   ↓
8. Update state file (switch current version)
   ↓
9. Report upgrade success to Server
   ↓
10. Upgrade complete ✅ (total downtime < 1 second)
```

### Program Start/Stop Workflow

**Start Program**:
```
1. Server issues start command
   ↓
2. Supervisor checks if program version directory exists
   ↓
3. If not exists, auto-download
   ↓
4. Start program process
   ↓
5. Report running status
```

**Stop Program**:
```
1. Server issues stop command
   ↓
2. Supervisor sends SIGTERM signal
   ↓
3. Wait for process exit (default 10 seconds)
   ↓
4. If not exited, send SIGKILL to force terminate
   ↓
5. Update state file
   ↓
6. Report stopped status
```

**Restart Program**:
```
1. Server issues restart command
   ↓
2. Execute stop workflow
   ↓
3. Wait 1 second
   ↓
4. Execute start workflow
```

### Program Uninstallation Workflow

```
1. Server issues remove command
   ↓
2. Supervisor stops program process
   ↓
3. Delete version directory (versions/v{version}/{program_name}/)
   ↓
4. Clean up program records in state file
   ↓
5. Report uninstalled status
   ↓
6. Uninstallation complete ✅
```

### Upgrade Failure Rollback

```
New version health check fails
    ↓
Stop new process
    ↓
Keep old version running
    ↓
Report upgrade failure
    ↓
Wait for next upgrade command
```

## Fault Recovery

### Scenario 1: Program Crash

```
taurus-supervisor detects process exit
    ↓
Attempt to restart current version
    ↓
If continuous failures (default 5 times), stop automatic restart
    ↓
Report program crash status
```

### Scenario 2: Upgrade Failure

```
New version health check fails
    ↓
Stop new process
    ↓
Rollback to old version
    ↓
Report upgrade failure
```

### Scenario 3: Power Loss Recovery

```
System restart
    ↓
systemd starts taurus-supervisor
    ↓
taurus-supervisor reads state.json
    ↓
Recover to last stable version
    ↓
Start all programs with auto_start=true
```

### Scenario 4: Server Unavailable

```
Heartbeat continuously fails (default 5 times)
    ↓
Switch to backup Server (if configured)
    ↓
If all Servers unavailable, continue running current programs
    ↓
Wait for Server recovery
```

## Heartbeat Mechanism

### Heartbeat Content

Supervisor periodically sends heartbeats to Server, including:

- Host information (host_id, host_name, host_username)
- Supervisor version
- All managed program status (name, version, status, PID, port)
- System metrics (CPU, memory, disk, network, load, uptime)
- Timestamp

### Heartbeat Response

Server response may include:

- **Certificate revocation command**: Notify client certificate has been revoked
- **Program installation command**: Install new program
- **Program upgrade command**: Upgrade existing program
- **Program start/stop command**: Start, stop, restart program
- **Program uninstallation command**: Remove program

### Command Persistence

- Commands saved to `pending_commands.json` file
- Automatically recover unexecuted commands after process restart
- Support retry mechanism (records retry count and error information)

## Logging

### Log Files

**Root User**: `/var/log/taurus-supervisor/supervisor.log`

**Regular User**: `~/taurus/supervisor.log`

### Log Format

```
2024-01-01 12:00:00 - taurus-supervisor - INFO - ==========================================
2024-01-01 12:00:00 - taurus-supervisor - INFO - Taurus Supervisor started
2024-01-01 12:00:00 - taurus-supervisor - INFO - ==========================================
2024-01-01 12:00:01 - taurus-supervisor - INFO - Recovered state: current version 1.1.0
2024-01-01 12:00:01 - taurus-supervisor - INFO - Registered program: taurus-executor v1.1.0
2024-01-01 12:00:01 - taurus-supervisor - INFO - Starting program: taurus-executor v1.1.0
2024-01-01 12:00:04 - taurus-supervisor - INFO - Program started: taurus-executor PID=12345 PORT=50051
2024-01-01 12:00:04 - taurus-supervisor - INFO - Program 1.1.0 started successfully
2024-01-01 12:00:30 - taurus-supervisor - INFO - Supervisor heartbeat started: interval=30s, ssl_verify=True
```

### Log Rotation

- Uses `RotatingFileHandler`
- Default maximum single file size: 10MB
- Default backup files kept: 5
- Configurable via environment variables

## Security

### TLS Certificates

- Uses mTLS mutual authentication
- Certificates uniformly issued by Server
- Supports certificate revocation and renewal
- Certificate directory permissions: 700

### Request Signing

- Supports HMAC-SHA256 request signing
- Prevents request tampering and replay attacks
- Signing keys distributed by Server

### Permission Control

- Root user: Can switch running user
- Regular user: Runs as current user
- Program binary permissions: 755
- Configuration file permissions: 600

## Multi-Instance Deployment

Deploy multiple Supervisor instances on the same host:

```bash
# Instance 1
sudo ./scripts/register.sh \
  --server http://<server>:8000 \
  --token <token1> \
  --auto-install \
  --install-dir /opt/taurus-1

# Instance 2
sudo ./scripts/register.sh \
  --server http://<server>:8000 \
  --token <token2> \
  --auto-install \
  --install-dir /opt/taurus-2
```

**Note**: Each instance requires different tokens and installation directories, ports are automatically assigned to avoid conflicts.

## FAQ

### Q1: Can Supervisor only manage taurus-executor?

**A**: No! Supervisor is a **general-purpose process manager** that can manage any program. As long as a program has an executable file, it can be registered to Supervisor through Server commands for management.

### Q2: How to add custom programs to Supervisor management?

**A**: Create a `ProgramInstallConfig` through Server API, specifying program name, version, configuration, etc. Supervisor will automatically receive and install during heartbeat. For example:

```bash
# Install custom program
curl -X POST http://localhost:8000/api/taurus/program-install-config/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "version": "1.0.0",
    "config": {
      "port": 8080,
      "auto_start": true,
      "restart_on_crash": true,
      "env_vars": {"NODE_ENV": "production"}
    },
    "auto_start": true
  }'

# Upgrade custom program
curl -X POST http://localhost:8000/api/taurus/program-command/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "action": "upgrade",
    "target_version": "1.1.0"
  }'

# Start/stop/restart custom program
curl -X POST http://localhost:8000/api/taurus/program-command/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "action": "start"  # or stop, restart, remove
  }'
```

### Q3: What is the relationship between Supervisor and Executor?

**A**: Supervisor is the daemon responsible for managing the lifecycle of arbitrary programs. Executor is just one of the managed business processes that executes specific commands and tasks.

### Q4: How to view program running status?

**A**: Check the state file `cat /opt/taurus/data/state.json` or query through Server API.

### Q5: What to do if upgrade fails?

**A**: Supervisor will automatically rollback to the old version, no manual intervention required.

### Q6: How to manually install a program?

**A**: Create a `ProgramInstallConfig` through Server API, Supervisor will automatically receive and install during heartbeat.

### Q7: Which operating systems are supported?

**A**: Currently supports Linux (x86_64, arm64), may support macOS and Windows in the future.

### Q8: How to configure backup Servers?

**A**: During registration, the Server will automatically return a list of backup nodes, saved in the configuration file.

## Related Documentation

- [Taurus Executor Deployment Guide](../taurus-executor/docs/deployment.md)
- [Supervisor Program Management Guide](../taurus-backend/docs/supervisor_program_management.md)
- [Supervisor Communication Protocol](docs/communication.md)
- [General Program Management Solution](../taurus-executor/docs/upgrade-scheme.md)

## Development

### Build

```bash
bash scripts/build.sh --version 1.0.0
```

### Local Testing

```bash
# Set environment variables
export BASE_DIR=/tmp/taurus-test
export SERVER_URL=http://localhost:8000
export HOST_ID=test-host-id

# Run Supervisor
python -m taurus_supervisor.main
```

### Dependencies

- Python 3.8+
- aiohttp
- psutil
- cryptography

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Contact

- Email: taurus-stack@outlook.com
- Issues: [GitHub Issues](https://github.com/taurus-ops/taurus-supervisor/issues)