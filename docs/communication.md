# Server and Supervisor Communication Mechanism

## Architecture Design

### Communication Topology

```
┌─────────────────────┐
│   Taurus Server      │
│   (Django Backend)  │
│   Port: 8000        │
└──────────┬──────────┘
           │
           │ HTTP POST (heartbeat channel)
           │ ← Supervisor reports status
           │ → Server issues management commands
           │
┌──────────┴──────────┐
│  Taurus Supervisor   │  ← Does not expose any external ports
│  (Daemon Process)    │
│  Port: None          │
└──────────┬──────────┘
           │
           │ Process management (local IPC)
           │
┌──────────┴──────────┐
│  Taurus Executor     │  ← gRPC port (internal network only)
│  (Business Process)  │  Port: 50051
└─────────────────────┘
```

## Security Features

### 1. Zero Extra Ports

| Component | Exposed Port | Description |
|-----------|-------------|-------------|
| **Server** | 8000 (HTTP) | Only external port |
| **Supervisor** | **None** | No ports open |
| **Executor** | 50051 (gRPC) | Internal network access only |

**Security advantages:**
- ✅ No increase in attack surface
- ✅ Firewall only needs to open Server port 8000
- ✅ Supervisor cannot be directly accessed externally

### 2. Unidirectional Connection

```
Supervisor → Server  ← Active connection (outbound)
Server → Supervisor  ← Via heartbeat response (downstream commands)
```

**Security advantages:**
- ✅ Only need to configure outbound firewall rules
- ✅ No need to open inbound ports
- ✅ Prevents external active attacks on Supervisor

### 3. Reuse Authentication Mechanism

| Authentication Layer | Mechanism | Description |
|---------------------|-----------|-------------|
| **Transport Layer** | mTLS | Bidirectional TLS certificate authentication |
| **Application Layer** | host_id | Unique host identifier |
| **Command Layer** | Signature Verification | Anti-tampering (optional) |

**Security advantages:**
- ✅ Reuse existing mTLS infrastructure
- ✅ No additional authentication system required
- ✅ Certificate revocation takes effect immediately

### 4. Complete Audit Chain

```
Server issues command
    ↓
Command record (database)
    ↓
Supervisor receives
    ↓
Execution result report
    ↓
Audit log (database)
```

## Communication Protocol

### Heartbeat Request

**Endpoint**: `POST /api/taurus/supervisor/heartbeat/`

**Request body**:
```json
{
    "host_id": "uuid-here",
    "supervisor_version": "1.0.0",
    "programs": [
        {
            "name": "taurus-executor",
            "version": "1.1.0",
            "status": "running",
            "pid": 12345,
            "port": 50051,
            "restart_count": 0,
            "upgrade_in_progress": false
        },
        {
            "name": "taurus-monitor",
            "version": "1.0.0",
            "status": "running",
            "pid": 12346,
            "port": 50052,
            "restart_count": 0,
            "upgrade_in_progress": false
        }
    ],
    "timestamp": "2024-01-01T00:00:00Z",
    "metrics": {
        "cpu_usage": 10.5,
        "memory_usage": 25.3,
        "memory_available_mb": 2048,
        "disk_usage": 45.2,
        "load_average": "0.5 0.3 0.2",
        "network_rx_bytes": 1024000,
        "network_tx_bytes": 512000,
        "process_count": 150,
        "uptime_seconds": 86400
    }
}
```

**Response body**:
```json
{
    "code": 2000,
    "msg": "Supervisor heartbeat received successfully",
    "data": {
        "commands": [
            {
                "type": "certificate_revoked",
                "message": "Certificate has been revoked, please stop all programs",
                "revoked_at": "2024-01-01T00:00:00Z",
                "reason": "security_breach"
            },
            {
                "type": "install_program",
                "program_name": "taurus-monitor",
                "version": "1.0.0",
                "download_url": "http://server:8000/api/taurus/executor/download/",
                "sha256": "abc123...",
                "config": {
                    "env_vars": {
                        "GRPC_PORT": "50052",
                        "METRICS_PORT": "9091",
                        "METRICS_ENABLED": "false"
                    },
                    "auto_start": true
                }
            },
            {
                "type": "upgrade_program",
                "program_name": "taurus-executor",
                "target_version": "1.2.0",
                "download_url": "http://server:8000/api/taurus/executor/download/",
                "sha256": "def456..."
            }
        ],
        "server_time": "2024-01-01T00:00:00Z"
    }
}
```

### Management Command Types

| Command Type | Description | Parameters |
|-------------|-------------|-----------|
| `certificate_revoked` | Certificate revoked | message, revoked_at, reason |
| `install_program` | Install program | program_name, version, download_url, sha256, config, package_type |
| `upgrade_program` | Upgrade program | program_name, target_version, download_url, sha256, package_type |
| `start_program` | Start program | program_name |
| `stop_program` | Stop program | program_name |
| `restart_program` | Restart program | program_name |
| `remove_program` | Remove program | program_name |

## Implementation Details

### Supervisor Side

**File**: `/home/jwj/dev/taurus-ops/taurus-supervisor/taurus_supervisor/heartbeat.py`

**Core class**: `SupervisorHeartbeatManager`

**Features**:
1. Periodically send heartbeats (default 30 seconds)
2. Receive and parse Server commands
3. Execute commands (upgrade, restart, rollback, etc.)
4. Exponential backoff retries
5. Continuous failure alerts

### Server Side

**File**: `/home/jwj/dev/taurus-ops/taurus-backend/taurus/views.py`

**Method**: `SupervisorViewSet.heartbeat()`

**Features**:
1. Verify host identity (host_id + status)
2. Update host online status
3. Check certificate revocation status
4. Generate management commands
5. Return command list

## Fault Handling

### Heartbeat Failure

| Scenario | Handling Strategy |
|---------|------------------|
| Network timeout | Exponential backoff retry (5s → 10s → 20s → ... → 60s) |
| 5 consecutive failures | Log alert |
| Server down | Maintain current state, wait for recovery |
| Command execution failed | Log error, report on next heartbeat |

### Command Loss

- Heartbeat interval 30 seconds, maximum command delay 30 seconds
- Command unacknowledged mechanism (optional)
- Server-side command queue (optional)

## Extensibility

### Future Features to Add

1. **Command priority**: Immediate execution of urgent commands
2. **Command acknowledgment**: Supervisor confirms command execution
3. **Batch commands**: Multiple commands issued in one heartbeat
4. **Command timeout**: Automatic cancellation on command timeout
5. **Command audit**: Complete command lifecycle recording

### Performance Optimization

1. **Dynamic heartbeat interval**: Extended when idle, shortened when busy
2. **Batch reporting**: Multiple metrics combined in one report
3. **Compressed transmission**: gzip compress heartbeat data
4. **Connection reuse**: HTTP Keep-Alive

## Security Best Practices

### 1. Network Isolation

```
┌─────────────────┐
│  DMZ Zone       │
│  Server: 8000   │  ← Externally exposed
└────────┬────────┘
         │
         │ Firewall rules
         │ Allow: Supervisor → Server:8000
         │ Deny: All other inbound
         │
┌────────┴────────┐
│  Internal Zone  │
│  Supervisor     │  ← Not exposed
│  Client         │  ← Internal only
└─────────────────┘
```

### 2. Certificate Management

- Supervisor reuses Client mTLS certificate
- Certificate revocation takes effect immediately
- Automatic certificate renewal (optional)

### 3. Access Control

- Server side: Only administrators can issue commands
- Supervisor side: Only accept Server commands
- Audit logs: Record all operations

## Monitoring Metrics

### Supervisor Side

- Heartbeat success rate
- Command execution success rate
- Average response time
- Consecutive failure count

### Server Side

- Online host count
- Heartbeat latency
- Command issuance volume
- Command execution success rate

## Summary

| Feature | Solution |
|---------|----------|
| **Communication Method** | Reuse heartbeat channel |
| **Port Exposure** | Zero extra ports |
| **Authentication Mechanism** | mTLS + host_id |
| **Encrypted Transmission** | TLS 1.3 |
| **Audit Capability** | Complete audit chain |
| **Fault Recovery** | Exponential backoff + cache |
| **Security Level** | ⭐⭐⭐⭐⭐ |