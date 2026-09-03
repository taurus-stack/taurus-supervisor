#!/usr/bin/env python3
"""
Taurus Supervisor - General Program Management Daemon

Responsibilities:
1. Manage arbitrary program lifecycle (start, stop, restart)
2. Execute upgrade workflow (download, verify, install, switch)
3. Monitor process health status
4. Handle fault recovery (crash recovery, version rollback)
5. Maintain version history
"""

import asyncio
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

from taurus_supervisor.heartbeat import SupervisorHeartbeatManager
from taurus_supervisor.program_manager import ProgramManager, ProgramConfig, ProgramStatus
from taurus_supervisor.config_crypto import auto_decrypt
from taurus_supervisor.log_forwarder import LogForwarder

# ANSI color codes
COLORS = {
    "TIMESTAMP": "\033[90m",    # Gray (timestamp)
    "NAME": "\033[37m",         # White (logger name)
    "PATH": "\033[36m",         # Cyan (file path)
    "DEBUG": "\033[36m",        # Cyan
    "INFO": "\033[32m",         # Green
    "WARNING": "\033[33m",      # Yellow
    "ERROR": "\033[31m",        # Red
    "CRITICAL": "\033[35m",     # Purple
    "MESSAGE": "\033[97m",      # White (message content)
    "RESET": "\033[0m",         # Reset
}

class ColoredFormatter(logging.Formatter):
    """Log formatter with ANSI colors"""
    def format(self, record):
        timestamp = self.formatTime(record, self.datefmt)
        color = COLORS.get(record.levelname, COLORS["RESET"])
        reset = COLORS["RESET"]
        return (
            f"{COLORS['TIMESTAMP']}[{timestamp}]{reset} "
            f"{color}[{record.levelname}]{reset} "
            f"{COLORS['PATH']}[{record.pathname}:{record.lineno}]{reset} "
            f"{COLORS['MESSAGE']}[{record.getMessage()}]{reset}"
        )

# Configure logging
log_dir = os.environ.get('LOG_DIR', '/var/log/taurus-supervisor')
log_file = os.path.join(log_dir, 'supervisor.log')
log_max_bytes = int(os.environ.get('LOG_MAX_BYTES', 10 * 1024 * 1024))  # Default 10MB
log_backup_count = int(os.environ.get('LOG_BACKUP_COUNT', 5))  # Default keep 5 backups

# Ensure log directory exists
try:
    os.makedirs(log_dir, exist_ok=True)
except PermissionError:
    # If unable to create, use user home directory
    log_dir = os.path.join(os.path.expanduser('~'), 'taurus', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'supervisor.log')

# Use RotatingFileHandler to limit log file size
from logging.handlers import RotatingFileHandler

# Create colored stdout handler
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(ColoredFormatter(datefmt="%Y-%m-%d %H:%M:%S"))

# Create file handler without colors (no ANSI escape codes in log files)
file_handler = RotatingFileHandler(
    log_file,
    maxBytes=log_max_bytes,
    backupCount=log_backup_count,
    encoding='utf-8',
)
file_handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        stdout_handler,
        file_handler,
    ]
)

logger = logging.getLogger('taurus-supervisor')


class StateManager:
    """State manager, persists daemon state (supports dot-separated nested keys)"""
    
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """Load state"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        
        return {
            'current_version': None,
            'previous_version': None,
            'upgrade_in_progress': False,
            'upgrade_target_version': None,
            'last_successful_version': None,
            'upgrade_history': [],
            'programs': {},
        }
    
    def save_state(self):
        """Save state"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def get(self, key: str, default=None) -> Any:
        if '.' in key:
            parts = key.split('.')
            current = self._state
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return default
            return current
        return self._state.get(key, default)
    
    def set(self, key: str, value: Any):
        if '.' in key:
            parts = key.split('.')
            current = self._state
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        else:
            self._state[key] = value
        self.save_state()


class VersionManager:
    """Version manager, manages client version directories"""
    
    def __init__(self, versions_dir: Path):
        self.versions_dir = versions_dir
        self.versions_dir.mkdir(parents=True, exist_ok=True)
    
    def get_version_dir(self, version: str) -> Path:
        """Get version directory"""
        return self.versions_dir / f"v{version}"
    
    def version_exists(self, version: str) -> bool:
        """Check if version exists"""
        return self.get_version_dir(version).exists()
    
    def list_versions(self) -> list:
        """List all versions"""
        versions = []
        for d in self.versions_dir.iterdir():
            if d.is_dir() and d.name.startswith('v'):
                versions.append(d.name[1:])  # Remove 'v' prefix
        return sorted(versions)
    
    def create_version_dir(self, version: str) -> Path:
        """Create version directory"""
        version_dir = self.get_version_dir(version)
        version_dir.mkdir(parents=True, exist_ok=True)
        return version_dir
    
    def remove_version(self, version: str):
        """Remove version"""
        import shutil
        version_dir = self.get_version_dir(version)
        if version_dir.exists():
            shutil.rmtree(version_dir)
            logger.info(f"Version removed: {version}")


class HealthChecker:
    """Health checker"""
    
    @staticmethod
    async def check_port(host: str, port: int, timeout: int = 2) -> bool:
        """Check if port is reachable"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Port check error: {e}")
            return False
    
    @staticmethod
    async def check_process(pid: int) -> bool:
        """Check if process is running"""
        try:
            os.kill(pid, 0)  # Signal 0 checks if process exists
            return True
        except OSError:
            return False


class UpgradeManager:
    """Upgrade manager, responsible for downloading, verifying, and installing new versions"""
    
    def __init__(self, version_manager: VersionManager, server_url: str):
        self.version_manager = version_manager
        self.server_url = server_url
    
    async def download_version(self, version: str, platform: str = 'linux', arch: str = 'x86_64', package_type: str = 'executor', max_retries: int = 3) -> tuple[bytes, str]:
        """Download program binary for specified version
        
        Args:
            version: Version number
            platform: Platform (linux, darwin, windows)
            arch: Architecture (x86_64, arm64)
            package_type: Package type (executor, supervisor, or custom program name)
            max_retries: Maximum retry attempts
        
        Returns:
            (binary_data, checksum) tuple
        """
        import aiohttp
        
        url = f"{self.server_url}/api/taurus/supervisor/download/?package_type={package_type}&platform={platform}&arch={arch}"
        
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Downloading version {version} (attempt {attempt}/{max_retries}): {url}")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as response:
                        if response.status != 200:
                            error_body = await response.text()
                            raise Exception(f"Download failed: HTTP {response.status} - {error_body}")
                        
                        # Get checksum (required for integrity verification)
                        checksum = response.headers.get('X-Checksum-SHA256', '')
                        if not checksum:
                            raise Exception("Server did not return X-Checksum-SHA256 header, download rejected")
                        
                        data = await response.read()
                        logger.info(f"Download completed: {len(data)} bytes")
                        
                        # Verify checksum
                        if not await self.verify_checksum(data, checksum):
                            raise Exception(f"Checksum verification failed, file may have been tampered with")
                        logger.info("Checksum verification passed, file integrity confirmed")
                        
                        return data, checksum
            except Exception as e:
                last_error = e
                logger.warning(f"Download failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    import asyncio
                    await asyncio.sleep(2 * attempt)  # Incremental wait time
        
        raise Exception(f"Download failed after {max_retries} retries: {last_error}")
    
    async def verify_checksum(self, data: bytes, expected_sha256: str) -> bool:
        """Verify file checksum"""
        actual_sha256 = hashlib.sha256(data).hexdigest()
        
        if actual_sha256 != expected_sha256:
            logger.error("Checksum verification failed")
            logger.error(f"  Expected: {expected_sha256}")
            logger.error(f"  Actual: {actual_sha256}")
            return False
        
        logger.info("Checksum verification passed")
        return True
    
    async def install_version(self, version: str, binary_data: bytes, program_name: str = "taurus-executor", config_files: Dict[str, bytes] = None):
        """Install version to separate directory (supports tar.gz extraction)"""
        version_dir = self.version_manager.create_version_dir(version) / program_name
        version_dir.mkdir(parents=True, exist_ok=True)

        # Detect and extract tar.gz (same logic as ProgramManager._install_binary_data)
        if len(binary_data) >= 2 and binary_data[0] == 0x1f and binary_data[1] == 0x8b:
            logger.info(f"Detected tar.gz package, extracting: {program_name} -> {version_dir}")
            import tarfile
            import io
            tar_stream = io.BytesIO(binary_data)
            with tarfile.open(fileobj=tar_stream, mode='r:gz') as tar:
                tar.extractall(path=str(version_dir))
            binary_path = version_dir / program_name
            if not binary_path.exists():
                for f in version_dir.rglob('*'):
                    if f.is_file() and f.name == program_name:
                        binary_path = f
                        break
            os.chmod(str(binary_path), 0o755)
        else:
            binary_path = version_dir / program_name
            with open(binary_path, 'wb') as f:
                f.write(binary_data)
            os.chmod(binary_path, 0o755)
        
        # Copy configuration files
        if config_files:
            for filename, content in config_files.items():
                config_path = version_dir / filename
                config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(config_path, 'wb') as f:
                    f.write(content)
        
        logger.info(f"Version {version} installed to: {version_dir}")
        return version_dir


class TaurusSupervisor:
    """Taurus Supervisor main class - generic program manager"""
    
    def __init__(self, config: Dict[str, Any]):
        # Configuration
        self.config = config
        self.base_dir = Path(config.get('base_dir'))
        self.versions_dir = self.base_dir / 'versions'
        self.data_dir = self.base_dir / 'data'
        self.state_file = self.data_dir / 'state.json'
        
        # Components
        self.state_manager = StateManager(self.state_file)
        self.version_manager = VersionManager(self.versions_dir)
        self.health_checker = HealthChecker()
        self.upgrade_manager = UpgradeManager(
            self.version_manager,
            config.get('server_url', 'http://localhost:8000')
        )
        
        # Generic program manager (replaces old ClientProcessManager)
        self.program_manager = ProgramManager(self.base_dir, self.state_manager, log_callback=self._on_program_log)
        
        # Supervisor heartbeat manager
        self.heartbeat_manager: Optional[SupervisorHeartbeatManager] = None
        
        # Persistence manager (lazy initialization, set after heartbeat_manager creation)
        self.persistence_manager = None
        
        # Log forwarder
        self.log_forwarder: Optional[LogForwarder] = None
        
        # Running state
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Save host_id from config to state_manager
        host_id = config.get('host_id')
        if host_id:
            self.state_manager.set('host_id', host_id)
            logger.info(f"Host ID configured: {host_id}")
    
    async def run(self):
        """Main run loop"""
        logger.info("=" * 60)
        logger.info("Taurus Supervisor starting")
        logger.info("=" * 60)
        
        # Register signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown_event.set())
        
        self._running = True
        
        # Restore previous state
        await self._restore_state()
        
        # Ensure all program binaries are available
        await self._ensure_all_programs_available()
        
        # Start Supervisor heartbeat
        await self._start_supervisor_heartbeat()
        
        # Start log forwarder
        await self._start_log_forwarder()
        
        # Start all registered programs
        await self._start_all_programs()
        
        # Main loop
        while self._running:
            try:
                # Process pending commands (received via heartbeat)
                await self._process_pending_commands()
                
                # Health check
                await self._health_check()
                
                # Wait for next check
                check_interval = self.config.get('health_check_interval', 30)
                
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=check_interval
                    )
                    break
                except asyncio.TimeoutError:
                    pass
                    
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(5)
        
        # Shutdown
        await self._shutdown()
    
    async def _ensure_all_programs_available(self):
        """Ensure all program binaries are available"""
        import platform as platform_module
        
        for name, state in self.program_manager.get_all_programs().items():
            version = state.config.version
            version_dir = self.version_manager.get_version_dir(version) / name
            binary_path = version_dir / state.config.binary_name
            
            if binary_path.exists() and binary_path.stat().st_size > 0:
                logger.info(f"Program binary already exists: {name} v{version}")
                continue
            
            # Auto-download
            logger.info(f"Program not found, starting auto-download: {name} v{version}")
            try:
                plat = platform_module.system().lower()
                arch = platform_module.machine()
                
                binary_data, _ = await self.upgrade_manager.download_version(
                    version,
                    platform=plat,
                    arch=arch,
                    package_type=name  # Use program name as package_type
                )
                
                # Install (supports tar.gz extraction)
                self.program_manager._install_binary_data(
                    binary_data, version_dir, name, state.config.binary_name
                )
                
                logger.info(f"Program auto-downloaded and installed successfully: {name} v{version}")
            except Exception as e:
                logger.error(f"Failed to auto-download program: {name} - {e}", exc_info=True)
    
    async def _start_all_programs(self):
        """Start all registered programs (skip already running programs)"""
        from taurus_supervisor.program_manager import ProgramStatus
        for name, state in self.program_manager.get_all_programs().items():
            if not state.config.auto_start:
                continue
            # Skip already running programs (state.json restored RUNNING status after crash recovery)
            if state.status == ProgramStatus.RUNNING:
                logger.info(f"Program already running, skipping start: {name} (PID={state.pid})")
                continue
            await self.program_manager.start_program(name)
    
    async def _restore_state(self):
        """Restore previous state, recover program configuration from state.json"""
        programs_state = self.state_manager.get('programs')
        
        if programs_state:
            for name, pstate in programs_state.items():
                version = pstate.get('version')
                if not version:
                    continue
                
                env_vars = pstate.get('env_vars', {})
                
                config = ProgramConfig(
                    name=name,
                    binary_name=name,
                    version=version,
                    auto_start=pstate.get('auto_start', True),
                    restart_on_crash=pstate.get('restart_on_crash', True),
                    user=pstate.get('user'),
                    group=pstate.get('group'),
                    env_vars=env_vars,
                )
                self.program_manager.register_program(config)
                
                # Restore pid/status/port (prevent duplicate start after restart)
                state = self.program_manager.get_program(name)
                if state:
                    saved_pid = pstate.get('pid')
                    saved_status = pstate.get('status')
                    saved_port = pstate.get('port')
                    
                    # If pid is still running, mark as RUNNING, don't restart
                    if saved_pid:
                        try:
                            os.kill(saved_pid, 0)
                            # Process still running → reuse
                            state.pid = saved_pid
                            state.status = ProgramStatus.RUNNING
                            # If port not saved, detect from env_vars
                            if saved_port:
                                state.port = saved_port
                            else:
                                state.port = self.program_manager._detect_port(env_vars)
                            logger.info(
                                f"Program restored from state: {name} v{version} (found old process PID={saved_pid}, "
                                f"associated, not restarting)"
                            )
                            # Save completed state
                            self.program_manager._save_program_state(name, state)
                            continue
                        except OSError:
                            pass  # Process dead, continue to decide whether to start based on auto_start
                    elif saved_status == 'running':
                        # Marked as RUNNING but no pid → unreliable, reset to STOPPED
                        state.status = ProgramStatus.STOPPED
                    
                    logger.info(f"Program restored from state: {name} v{version}")
        else:
            logger.info("First startup, no history state, waiting for heartbeat configuration")
    
    async def _start_supervisor_heartbeat(self):
        """Start Supervisor heartbeat"""
        host_id = self.state_manager.get('host_id')
        server_url = self.config.get('server_url', 'http://localhost:8000')
        
        if not host_id:
            logger.warning("host_id not configured, Supervisor heartbeat disabled")
            return
        
        # Get backup server list
        backup_servers = self.config.get('backup_servers', [])
        
        self.heartbeat_manager = SupervisorHeartbeatManager(
            server_url=server_url,
            host_id=host_id,
            interval=self.config.get('supervisor_heartbeat_interval', 30),
            timeout=10,
            max_failures=5,
            data_dir=str(self.data_dir),
            backup_servers=backup_servers if backup_servers else None,
            request_signing_secret=self.config.get('request_signing_secret', ''),
            ssl_verify=self.config.get('ssl_verify', True),
            ssl_ca_path=self.config.get('ssl_ca_path', None) or None,
            host_username=self.config.get('host_username') or None,
        )
        
        # Set persistence manager reference
        self.persistence_manager = self.heartbeat_manager.persistence_manager
        
        # Update program status BEFORE starting heartbeat (prevent first heartbeat with empty list)
        programs_status = self.program_manager.get_programs_status_for_heartbeat()
        self.heartbeat_manager.update_programs_status(programs_status)
        logger.info(f"Initial programs status reported: {len(programs_status)} programs")
        
        await self.heartbeat_manager.start()
        
        # Print server configuration
        if self.heartbeat_manager.failover_manager:
            servers = self.heartbeat_manager.failover_manager.get_all_servers()
            logger.info(f"Heartbeat server configuration ({len(servers)} total):")
            for srv in servers:
                role = "Primary" if srv['is_primary'] else "Backup"
                logger.info(f"  [{role}] {srv['url']} (priority: {srv['priority']})")
        
        logger.info("Supervisor heartbeat started")
        
        # Report locally installed program status (handle abnormal recovery such as power loss)
        await self._report_installed_programs()
    
    async def _process_pending_commands(self):
        """Process pending commands received via heartbeat (supports persistence and retry)"""
        if not self.heartbeat_manager:
            return
        
        # Prioritize processing persisted commands (recovered after process restart)
        persistence_manager = getattr(self.heartbeat_manager, 'persistence_manager', None)
        if persistence_manager and persistence_manager.pending_count > 0:
            logger.info(f"Found {persistence_manager.pending_count} persisted commands, starting processing")
            await self._process_commands_with_persistence(persistence_manager)
        
        # Process in-memory commands (newly received)
        program_configs = self.heartbeat_manager.get_pending_program_configs()
        if program_configs:
            self.heartbeat_manager.clear_pending_program_configs()
            
            for cmd in program_configs:
                action = cmd.get('action')
                program_name = cmd.get('program_name')
                
                try:
                    # All operations use unified _handle_*_program (built-in state machine, idempotency check, result reporting)
                    if action == 'install':
                        await self._handle_install_program(cmd)
                    elif action == 'upgrade':
                        await self._handle_upgrade_program(cmd)
                    elif action == 'start':
                        await self._handle_start_program(cmd)
                    elif action == 'stop':
                        await self._handle_stop_program(cmd)
                    elif action == 'restart':
                        await self._handle_restart_program(cmd)
                    elif action == 'remove':
                        await self._handle_remove_program(cmd)
                    elif action == 'log_collect':
                        await self._handle_log_collect(cmd)
                    # Note: _handle_*_program internally handles result reporting and command cleanup
                    
                except Exception as e:
                    error_msg = f"Execution failed: {str(e)}"
                    logger.error(f"Failed to execute program management command [{action} {program_name}]: {e}")
                    
                    # _handle_*_program internally handles exception reporting, this is just a fallback
                    command_id = cmd.get('command_id')
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=cmd.get('command_type', 'program_command'),
                            success=False,
                            message=error_msg
                        )
                    
                    # If persistence is enabled, update error information for retry
                    if persistence_manager:
                        persistence_manager.update_command_error(cmd, str(e))
    
    async def _process_commands_with_persistence(self, persistence_manager):
        """Process persisted commands (supports retry)"""
        commands = persistence_manager.get_commands()
        
        for cmd in commands:
            action = cmd.get('action')
            program_name = cmd.get('program_name')
            retry_count = cmd.get('retry_count', 0)

            # Max retry cap: give up permanently after too many failures
            max_command_retries = 5
            if retry_count >= max_command_retries:
                error_msg = (
                    f"Command reached max retries ({max_command_retries}), "
                    f"marking as permanently failed: {cmd.get('last_error')}"
                )
                logger.error(
                    f"Persisted command exceeded max retries [{action} {program_name}], "
                    f"retry count: {retry_count}, giving up"
                )
                command_id = cmd.get('command_id')
                if command_id and self.heartbeat_manager:
                    await self.heartbeat_manager.report_command_result(
                        command_id=command_id,
                        command_type=cmd.get('command_type', 'program_command'),
                        success=False,
                        message=error_msg,
                    )
                persistence_manager.remove_command(cmd)
                continue

            # Exponential backoff: wait based on retry count
            if retry_count > 0:
                wait_time = min(2 ** retry_count * 10, 300)
                created_at = cmd.get('created_at', 0)
                elapsed = time.time() - created_at
                if elapsed < wait_time:
                    logger.debug(
                        f"Command retry waiting: [{action} {program_name}], "
                        f"retry count: {retry_count}, remaining wait: {wait_time - elapsed:.0f}s"
                    )
                    continue
            
            try:
                if action == 'log_collect':
                    await self._handle_log_collect(cmd)
                    self.persistence_manager.remove_command(cmd)
                    continue
                
                # All operations use unified _handle_*_program (built-in state machine, idempotency check, result reporting, command cleanup)
                if action == 'install':
                    await self._handle_install_program(cmd)
                elif action == 'upgrade':
                    await self._handle_upgrade_program(cmd)
                elif action == 'start':
                    await self._handle_start_program(cmd)
                elif action == 'stop':
                    await self._handle_stop_program(cmd)
                elif action == 'restart':
                    await self._handle_restart_program(cmd)
                elif action == 'remove':
                    await self._handle_remove_program(cmd)
                # Note: _handle_*_program internally handles result reporting and command cleanup
                
            except Exception as e:
                logger.error(
                    f"Persisted command execution failed [{action} {program_name}], "
                    f"retry count: {retry_count + 1}: {e}"
                )
                # _handle_*_program internally handles exception reporting, only update persisted error information here
                persistence_manager.update_command_error(cmd, str(e))
    
    async def _handle_install_program(self, cmd: dict):
        """Handle install program command (supports redeployment of same version, with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        version = cmd.get('version')
        sha256 = cmd.get('sha256')
        config = cmd.get('config', {})
        package_type = cmd.get('package_type', program_name)
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type')
        max_retries = 3
        retry_count = cmd.get('retry_count', 0)
        
        # Prevent infinite retries
        if retry_count >= max_retries:
            error_msg = f"Install command reached max retries {max_retries}: {cmd.get('last_error')}"
            logger.error(f"Install command failed, no more retries: {program_name} v{version}")
            
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=error_msg
                )
            
            self.persistence_manager.remove_command(cmd)
            return
        
        # Duplicate check: installation in progress
        existing = self.program_manager.get_program(program_name)
        if existing and existing.install_in_progress:
            logger.info(f"Program is being installed, skipping duplicate command: {program_name} v{version}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program is being installed, skipping duplicate command: {program_name} v{version}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        # Idempotency check: same version already installed (only applies to new PENDING commands, recovery scenarios continue execution)
        current_cmd_state = cmd.get('state', CommandState.PENDING)
        if existing and existing.config.version == version and current_cmd_state == CommandState.PENDING:
            logger.info(f"Program already has same version installed, skipping duplicate command: {program_name} v{version}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program already has same version installed, skipping: {program_name} v{version}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        logger.info(f"Installing program: {program_name} v{version}, initial state: {cmd.get('state', CommandState.PENDING)}")
        
        # Mark as installing
        if existing:
            existing.install_in_progress = True
        
        try:
            # If same version already installed, stop and clean up first
            if existing and existing.config.version == version:
                logger.info(f"Detected same version already installed, stopping and redeploying: {program_name} v{version}")
                if existing.status == ProgramStatus.RUNNING:
                    stop_success = await self.program_manager.stop_program(program_name)
                    if not stop_success:
                        error_msg = f"Program stop failed during reinstall: {program_name}"
                        logger.error(error_msg)
                        self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                        if command_id and self.heartbeat_manager:
                            await self.heartbeat_manager.report_command_result(
                                command_id=command_id,
                                command_type=command_type,
                                success=False,
                                message=error_msg
                            )
                        self.persistence_manager.remove_command(cmd)
                        return
            
            # State machine: loop until completion or failure
            max_iterations = 10
            for _ in range(max_iterations):
                state = cmd.get('state', CommandState.PENDING)
                
                if state == CommandState.COMPLETED:
                    break
                
                # Download phase
                if state in (CommandState.PENDING, CommandState.DOWNLOADING):
                    self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                    logger.info(f"Starting download: {program_name} v{version}")
                    
                    binary_data, _ = await self.upgrade_manager.download_version(
                        version,
                        package_type=package_type
                    )
                    
                    # Save binary data to memory (for subsequent steps)
                    cmd['_binary_data'] = binary_data
                    self.persistence_manager.update_command_state(cmd, CommandState.VERIFYING)
                    continue
                
                # Verification phase
                if state == CommandState.VERIFYING:
                    binary_data = cmd.get('_binary_data')
                    if binary_data is None:
                        logger.warning(f"Missing binary data, falling back to download phase: {program_name} v{version}")
                        self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                        continue
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.VERIFYING)
                    logger.info(f"Verifying checksum: {program_name} v{version}")
                    
                    if sha256:
                        if not await self.upgrade_manager.verify_checksum(binary_data, sha256):
                            raise Exception(f"Checksum verification failed: {program_name} v{version}")
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.INSTALLING)
                    continue
                
                # Installation phase
                if state == CommandState.INSTALLING:
                    binary_data = cmd.get('_binary_data')
                    if binary_data is None:
                        logger.warning(f"Missing binary data, falling back to download phase: {program_name} v{version}")
                        self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                        continue
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.INSTALLING)
                    logger.info(f"Starting installation: {program_name} v{version}")
                    
                    # Register program
                    env_vars = config.get('env_vars', {}).copy()
                    self.program_manager.register_program(ProgramConfig(
                        name=program_name,
                        binary_name=program_name,
                        version=version,
                        auto_start=config.get('auto_start', True),
                        restart_on_crash=config.get('restart_on_crash', True),
                        env_vars=env_vars,
                        user=config.get('user'),
                        group=config.get('group'),
                    ))
                    
                    # Install binary
                    version_dir = self.version_manager.get_version_dir(version) / program_name
                    self.program_manager._install_binary_data(
                        binary_data, version_dir, program_name, program_name
                    )
                    
                    # Clean up temporary data
                    cmd.pop('_binary_data', None)
                    
                    # user/group already saved via register_program → _save_program_state
                    self.persistence_manager.update_command_state(cmd, CommandState.STARTING)
                    continue
                
                # Start phase
                if state == CommandState.STARTING:
                    # Check if program is registered
                    installed = self.program_manager.get_program(program_name)
                    if not installed:
                        logger.warning(f"Program not registered, falling back to installation phase: {program_name} v{version}")
                        self.persistence_manager.update_command_state(cmd, CommandState.INSTALLING)
                        continue
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.STARTING)
                    logger.info(f"Starting program: {program_name} v{version}")
                    
                    if config.get('auto_start', True):
                        started = await self.program_manager.start_program(program_name)
                        if not started:
                            raise Exception(f"Program failed to start: {program_name} v{version}")
                    
                    # Complete
                    self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                    logger.info(f"Program installed successfully: {program_name} v{version}")
                    
                    # Report success result
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=command_type,
                            success=True,
                            message=f"Program installed successfully: {program_name} v{version}"
                        )
                    
                    # Remove command
                    self.persistence_manager.remove_command(cmd)
                    break
                
                # FAILED state: reset to PENDING for retry
                if state == CommandState.FAILED:
                    logger.info(f"Recovering from failed state, restarting: {program_name} v{version}")
                    self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                    continue
                
                # Unknown state
                logger.warning(f"Unknown state, resetting to PENDING: {state}")
                self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                
        except Exception as e:
            logger.error(f"Program installation failed: {program_name} v{version}, error={e}")
            
            # Clean up failed installation files
            version_dir = self.version_manager.get_version_dir(version) / program_name
            if version_dir.exists():
                import shutil
                try:
                    shutil.rmtree(version_dir)
                    logger.info(f"Cleaned up failed installation files: {version_dir}")
                except Exception as cleanup_e:
                    logger.warning(f"Failed to clean up files: {cleanup_e}")
            
            # Update state to failed
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            
            # Report failure result
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program installation failed: {e}"
                )
            
            # Remove command to prevent infinite retry
            self.persistence_manager.remove_command(cmd)
            return
        finally:
            # Clear installation in progress flag
            if existing:
                existing.install_in_progress = False
    
    async def _handle_upgrade_program(self, cmd: dict):
        """Handle upgrade program command (idempotent: skip if already at target version, with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        target_version = cmd.get('target_version')
        sha256 = cmd.get('sha256')
        package_type = cmd.get('package_type', program_name)
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type', 'program_command')
        
        # Idempotency check: already at target version (only applies to new PENDING commands, recovery scenarios continue execution)
        existing = self.program_manager.get_program(program_name)
        current_cmd_state = cmd.get('state', CommandState.PENDING)
        if existing and existing.config.version == target_version and current_cmd_state == CommandState.PENDING:
            logger.info(f"Program already at target version, skipping upgrade: {program_name} v{target_version}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program already at target version, skipping upgrade: {program_name} v{target_version}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        # Duplicate check: upgrade in progress
        if existing and existing.upgrade_in_progress:
            logger.info(f"Program is being upgraded, skipping duplicate command: {program_name} -> v{target_version}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program is being upgraded, skipping duplicate command: {program_name} -> v{target_version}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        logger.info(f"Upgrading program: {program_name} -> v{target_version}, initial state: {cmd.get('state', CommandState.PENDING)}")
        
        # Mark as upgrading
        if existing:
            existing.upgrade_in_progress = True
        
        try:
            # State machine: loop until completion or failure
            max_iterations = 10
            for _ in range(max_iterations):
                state = cmd.get('state', CommandState.PENDING)
                
                if state == CommandState.COMPLETED:
                    break
                
                # Download phase
                if state in (CommandState.PENDING, CommandState.DOWNLOADING):
                    self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                    logger.info(f"Starting download: {program_name} v{target_version}")
                    
                    binary_data, _ = await self.upgrade_manager.download_version(
                        target_version,
                        package_type=package_type
                    )
                    
                    # Save binary data to memory (for subsequent steps)
                    cmd['_binary_data'] = binary_data
                    self.persistence_manager.update_command_state(cmd, CommandState.VERIFYING)
                    continue
                
                # Verification phase
                if state == CommandState.VERIFYING:
                    binary_data = cmd.get('_binary_data')
                    if binary_data is None:
                        logger.warning(f"Missing binary data, falling back to download phase: {program_name} v{target_version}")
                        self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                        continue
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.VERIFYING)
                    logger.info(f"Verifying checksum: {program_name} v{target_version}")
                    
                    if sha256:
                        if not await self.upgrade_manager.verify_checksum(binary_data, sha256):
                            raise Exception(f"Checksum verification failed: {program_name} v{target_version}")
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.INSTALLING)
                    continue
                
                # Upgrade phase
                if state == CommandState.INSTALLING:
                    binary_data = cmd.get('_binary_data')
                    if binary_data is None:
                        logger.warning(f"Missing binary data, falling back to download phase: {program_name} v{target_version}")
                        self.persistence_manager.update_command_state(cmd, CommandState.DOWNLOADING)
                        continue
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.INSTALLING)
                    logger.info(f"Starting upgrade: {program_name} -> v{target_version}")
                    
                    success = await self.program_manager.upgrade_program(
                        name=program_name,
                        target_version=target_version,
                        binary_data=binary_data,
                    )
                    
                    # Clean up temporary data
                    cmd.pop('_binary_data', None)
                    
                    if success:
                        self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                        logger.info(f"Program upgrade successful: {program_name} -> v{target_version}")
                        
                        if command_id and self.heartbeat_manager:
                            await self.heartbeat_manager.report_command_result(
                                command_id=command_id,
                                command_type=command_type,
                                success=True,
                                message=f"Program upgrade successful: {program_name} -> v{target_version}"
                            )
                        
                        # Remove command
                        self.persistence_manager.remove_command(cmd)
                        break
                    else:
                        raise Exception(f"Program upgrade failed: {program_name} -> v{target_version}")
                
                # FAILED state: reset to PENDING for retry
                if state == CommandState.FAILED:
                    logger.info(f"Recovering from failed state, restarting: {program_name} -> v{target_version}")
                    self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                    continue
                
                # Unknown state
                logger.warning(f"Unknown state, resetting to PENDING: {state}")
                self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                
        except Exception as e:
            logger.error(f"Program upgrade failed: {program_name} -> v{target_version}, error={e}")
            
            # Update state to failed
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            
            # Report failure result
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program upgrade failed: {e}"
                )
            
            # Remove command to prevent infinite retry
            self.persistence_manager.remove_command(cmd)
            return
        finally:
            # Clear upgrade in progress flag
            if existing:
                existing.upgrade_in_progress = False
    
    async def _handle_start_program(self, cmd: dict):
        """Handle start program command (with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type', 'program_command')
        max_retries = 3
        retry_count = cmd.get('retry_count', 0)
        
        # Prevent infinite retries
        if retry_count >= max_retries:
            error_msg = f"Start command reached max retries {max_retries}: {cmd.get('last_error')}"
            logger.error(f"Start command failed, no more retries: {program_name}")
            
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=error_msg
                )
            
            self.persistence_manager.remove_command(cmd)
            return
        
        # Idempotency check: program already running
        existing = self.program_manager.get_program(program_name)
        if existing and existing.status == ProgramStatus.RUNNING:
            logger.info(f"Program already running, skipping duplicate command: {program_name}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program already running, skipping: {program_name}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        current_state = cmd.get('state', CommandState.PENDING)
        logger.info(f"Starting program: {program_name}, current state: {current_state}")
        
        try:
            if cmd.get('state', CommandState.PENDING) in (CommandState.PENDING, CommandState.STARTING):
                self.persistence_manager.update_command_state(cmd, CommandState.STARTING)
                success = await self.program_manager.start_program(program_name)
                if not success:
                    error_msg = f"Program not registered or start failed: {program_name}"
                    logger.error(error_msg)
                    self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=command_type,
                            success=False,
                            message=error_msg
                        )
                    self.persistence_manager.remove_command(cmd)
                    return
                
                self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                logger.info(f"Program started successfully: {program_name}")
                
                if command_id and self.heartbeat_manager:
                    await self.heartbeat_manager.report_command_result(
                        command_id=command_id,
                        command_type=command_type,
                        success=True,
                        message=f"Program started successfully: {program_name}"
                    )
                
                self.persistence_manager.remove_command(cmd)
        except Exception as e:
            logger.error(f"Program start failed: {program_name}, error={e}")
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program start failed: {e}"
                )
            self.persistence_manager.remove_command(cmd)
            return
    
    async def _handle_stop_program(self, cmd: dict):
        """Handle stop program command (with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type', 'program_command')
        max_retries = 3
        retry_count = cmd.get('retry_count', 0)
        
        # Prevent infinite retries
        if retry_count >= max_retries:
            error_msg = f"Stop command reached max retries {max_retries}: {cmd.get('last_error')}"
            logger.error(f"Stop command failed, no more retries: {program_name}")
            
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=error_msg
                )
            
            self.persistence_manager.remove_command(cmd)
            return
        
        # Idempotency check: program already stopped
        existing = self.program_manager.get_program(program_name)
        if not existing or existing.status == ProgramStatus.STOPPED:
            logger.info(f"Program already stopped, skipping duplicate command: {program_name}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program already stopped, skipping: {program_name}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        current_state = cmd.get('state', CommandState.PENDING)
        logger.info(f"Stopping program: {program_name}, current state: {current_state}")
        
        try:
            if cmd.get('state', CommandState.PENDING) in (CommandState.PENDING, CommandState.STOPPING):
                self.persistence_manager.update_command_state(cmd, CommandState.STOPPING)
                success = await self.program_manager.stop_program(program_name)
                if not success:
                    error_msg = f"Program not registered or stop failed: {program_name}"
                    logger.error(error_msg)
                    self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=command_type,
                            success=False,
                            message=error_msg
                        )
                    self.persistence_manager.remove_command(cmd)
                    return
                
                self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                logger.info(f"Program stopped successfully: {program_name}")
                
                if command_id and self.heartbeat_manager:
                    await self.heartbeat_manager.report_command_result(
                        command_id=command_id,
                        command_type=command_type,
                        success=True,
                        message=f"Program stopped successfully: {program_name}"
                    )
                
                self.persistence_manager.remove_command(cmd)
        except Exception as e:
            logger.error(f"Program stop failed: {program_name}, error={e}")
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program stop failed: {e}"
                )
            self.persistence_manager.remove_command(cmd)
            return
    
    async def _handle_restart_program(self, cmd: dict):
        """Handle restart program command (with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type', 'program_command')
        max_retries = 3
        retry_count = cmd.get('retry_count', 0)
        
        # Prevent infinite retries
        if retry_count >= max_retries:
            error_msg = f"Restart command reached max retries {max_retries}: {cmd.get('last_error')}"
            logger.error(f"Restart command failed, no more retries: {program_name}")
            
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=error_msg
                )
            
            self.persistence_manager.remove_command(cmd)
            return
        
        # Idempotency check: program not registered or already stopped, try to start directly
        existing = self.program_manager.get_program(program_name)
        if not existing or existing.status == ProgramStatus.STOPPED:
            logger.info(f"Program not running, converting restart to start: {program_name}")
            # Convert to start command
            cmd['action'] = 'start'
            await self._handle_start_program(cmd)
            return
        
        logger.info(f"Restarting program: {program_name}, initial state: {cmd.get('state', CommandState.PENDING)}")
        
        try:
            # State machine: loop until completion or failure
            max_iterations = 10
            for _ in range(max_iterations):
                state = cmd.get('state', CommandState.PENDING)
                
                if state == CommandState.COMPLETED:
                    break
                
                # Stop phase
                if state in (CommandState.PENDING, CommandState.STOPPING):
                    self.persistence_manager.update_command_state(cmd, CommandState.STOPPING)
                    stop_success = await self.program_manager.stop_program(program_name)
                    if not stop_success:
                        error_msg = f"Program not registered or stop failed during restart: {program_name}"
                        logger.error(error_msg)
                        self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                        if command_id and self.heartbeat_manager:
                            await self.heartbeat_manager.report_command_result(
                                command_id=command_id,
                                command_type=command_type,
                                success=False,
                                message=error_msg
                            )
                        self.persistence_manager.remove_command(cmd)
                        return
                    self.persistence_manager.update_command_state(cmd, CommandState.STARTING)
                    continue
                
                # Start phase
                if state == CommandState.STARTING:
                    self.persistence_manager.update_command_state(cmd, CommandState.STARTING)
                    start_success = await self.program_manager.start_program(program_name)
                    if not start_success:
                        error_msg = f"Program not registered or start failed during restart: {program_name}"
                        logger.error(error_msg)
                        self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                        if command_id and self.heartbeat_manager:
                            await self.heartbeat_manager.report_command_result(
                                command_id=command_id,
                                command_type=command_type,
                                success=False,
                                message=error_msg
                            )
                        self.persistence_manager.remove_command(cmd)
                        return
                    self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                    logger.info(f"Program restarted successfully: {program_name}")
                    
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=command_type,
                            success=True,
                            message=f"Program restarted successfully: {program_name}"
                        )
                    
                    self.persistence_manager.remove_command(cmd)
                    break
                
                # FAILED state: reset to PENDING for retry
                if state == CommandState.FAILED:
                    logger.info(f"Recovering from failed state, restarting: {program_name}")
                    self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                    continue
                
                # Unknown state
                logger.warning(f"Unknown state, resetting to PENDING: {state}")
                self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                
        except Exception as e:
            logger.error(f"Program restart failed: {program_name}, error={e}")
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program restart failed: {e}"
                )
            self.persistence_manager.remove_command(cmd)
            return
    
    async def _handle_remove_program(self, cmd: dict):
        """Handle remove program command (with state machine)"""
        from taurus_supervisor.heartbeat import CommandState
        
        program_name = cmd.get('program_name')
        command_id = cmd.get('command_id')
        command_type = cmd.get('command_type', 'program_command')
        max_retries = 3
        retry_count = cmd.get('retry_count', 0)
        
        # Prevent infinite retries
        if retry_count >= max_retries:
            error_msg = f"Remove command reached max retries {max_retries}: {cmd.get('last_error')}"
            logger.error(f"Remove command failed, no more retries: {program_name}")
            
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=error_msg
                )
            
            self.persistence_manager.remove_command(cmd)
            return
        
        # Idempotency check: program doesn't exist or already removed
        existing = self.program_manager.get_program(program_name)
        if not existing:
            logger.info(f"Program doesn't exist, skipping remove command: {program_name}")
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=True,
                    message=f"Program doesn't exist, skipping remove: {program_name}"
                )
            self.persistence_manager.remove_command(cmd)
            return
        
        logger.info(f"Removing program: {program_name}, initial state: {cmd.get('state', CommandState.PENDING)}")
        
        try:
            # State machine: loop until completion or failure
            max_iterations = 10
            for _ in range(max_iterations):
                state = cmd.get('state', CommandState.PENDING)
                
                if state == CommandState.COMPLETED:
                    break
                
                # Stop phase
                if state in (CommandState.PENDING, CommandState.STOPPING):
                    self.persistence_manager.update_command_state(cmd, CommandState.STOPPING)
                    existing = self.program_manager.get_program(program_name)
                    if existing and existing.status == ProgramStatus.RUNNING:
                        stop_success = await self.program_manager.stop_program(program_name)
                        if not stop_success:
                            error_msg = f"Program not registered or stop failed during remove: {program_name}"
                            logger.error(error_msg)
                            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=error_msg)
                            if command_id and self.heartbeat_manager:
                                await self.heartbeat_manager.report_command_result(
                                    command_id=command_id,
                                    command_type=command_type,
                                    success=False,
                                    message=error_msg
                                )
                            self.persistence_manager.remove_command(cmd)
                            return
                    self.persistence_manager.update_command_state(cmd, CommandState.REMOVING)
                    continue
                
                # Remove phase
                if state == CommandState.REMOVING:
                    self.persistence_manager.update_command_state(cmd, CommandState.REMOVING)
                    
                    # Get info first before deletion (to avoid not being able to get it after deletion)
                    to_remove = self.program_manager.get_program(program_name)
                    
                    # Delete from memory
                    if program_name in self.program_manager.programs:
                        del self.program_manager.programs[program_name]
                    
                    # Delete version directory
                    if to_remove:
                        version_dir = self.version_manager.get_version_dir(to_remove.config.version) / program_name
                        if version_dir.exists():
                            import shutil
                            shutil.rmtree(version_dir)
                            logger.info(f"Program version directory deleted: {version_dir}")
                    
                    # Synchronously update state.json (to prevent "resurrection" after restart)
                    programs = self.state_manager.get('programs', {})
                    if program_name in programs:
                        del programs[program_name]
                        self.state_manager.set('programs', programs)
                    
                    self.persistence_manager.update_command_state(cmd, CommandState.COMPLETED)
                    logger.info(f"Program removed successfully: {program_name}")
                    
                    if command_id and self.heartbeat_manager:
                        await self.heartbeat_manager.report_command_result(
                            command_id=command_id,
                            command_type=command_type,
                            success=True,
                            message=f"Program removed successfully: {program_name}"
                        )
                    
                    self.persistence_manager.remove_command(cmd)
                    break
                
                # FAILED state: reset to PENDING for retry
                if state == CommandState.FAILED:
                    logger.info(f"Recovering from failed state, restarting: {program_name}")
                    self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                    continue
                
                # Unknown state
                logger.warning(f"Unknown state, resetting to PENDING: {state}")
                self.persistence_manager.update_command_state(cmd, CommandState.PENDING)
                
        except Exception as e:
            logger.error(f"Program removal failed: {program_name}, error={e}")
            self.persistence_manager.update_command_state(cmd, CommandState.FAILED, error=str(e))
            if command_id and self.heartbeat_manager:
                await self.heartbeat_manager.report_command_result(
                    command_id=command_id,
                    command_type=command_type,
                    success=False,
                    message=f"Program removal failed: {e}"
                )
            self.persistence_manager.remove_command(cmd)
    
    async def _report_installed_programs(self):
        """Report locally installed program status (handles abnormal recovery such as power loss)
        
        Scenario: After Supervisor downloads and starts a program, if power is lost before reporting results,
        after restart it needs to notify the backend that the program is installed, to avoid the backend repeatedly sending install commands.
        """
        if not self.heartbeat_manager:
            return
        
        for name, state in self.program_manager.get_all_programs().items():
            version = state.config.version
            if not version:
                continue
            
            # Check if version directory exists
            version_dir = self.version_manager.get_version_dir(version) / name
            binary_path = version_dir / state.config.binary_name
            
            if binary_path.exists() and binary_path.stat().st_size > 0:
                logger.info(f"Locally detected installed program: {name} v{version}")
                # Report installation success result (command_id=0 indicates status sync, not a specific command)
                await self.heartbeat_manager.report_command_result(
                    command_id=0,
                    command_type='install_config',
                    success=True,
                    message=f"Program installed (local detection): {name} v{version}"
                )
    
    async def _handle_log_collect(self, cmd: dict):
        """Handle log collection command"""
        action = cmd.get('log_action', 'start')
        
        if action == 'start':
            # Ensure log forwarder is initialized before enabling collection
            if not await self._ensure_log_forwarder():
                logger.warning("Failed to enable log forwarder, ignoring log_collect command")
                return
            
            min_level = cmd.get('min_level', 'INFO')
            programs = cmd.get('programs', [])
            duration = cmd.get('duration', 1800)
            self.log_forwarder.set_collect_mode(
                min_level=min_level,
                programs=programs or None,
                duration=duration,
            )
            msg = f"Log collection started: level={min_level}, programs={programs or 'all'}, duration={duration}s"
        else:
            if not self.log_forwarder:
                logger.warning("Log forwarder not initialized, ignoring log_collect stop command")
                return
            self.log_forwarder.disable_collect_mode()
            msg = "Log collection stopped, no more logs forwarded"
        
        logger.info(msg)
        
        command_id = cmd.get('command_id')
        if command_id and self.heartbeat_manager:
            await self.heartbeat_manager.report_command_result(
                command_id=command_id,
                command_type=cmd.get('command_type', 'log_command'),
                success=True,
                message=msg
            )
    
    async def _health_check(self):
        """Health check"""
        # First sync config from state.json (supports external tools like taurus-pm modifying config)
        await self._sync_state_config()
        
        # Then check health status (update crashed/stopped status)
        health_results = await self.program_manager.check_all_programs_health()
        
        for name, is_healthy in health_results.items():
            if not is_healthy:
                logger.warning(f"Program health check failed: {name}")
        
        # Finally report program status (ensure reported status is up-to-date)
        if self.heartbeat_manager:
            programs_status = self.program_manager.get_programs_status_for_heartbeat()
            self.heartbeat_manager.update_programs_status(programs_status)
    
    async def _sync_state_config(self):
        """Sync program config from state.json (support external tools like taurus-pm)"""
        try:
            self.state_manager._state = self.state_manager._load_state()
            programs_state = self.state_manager.get('programs', {})
            
            for name, pstate in programs_state.items():
                state = self.program_manager.get_program(name)
                if state:
                    restart_on_crash = pstate.get('restart_on_crash', True)
                    auto_start = pstate.get('auto_start', True)
                    
                    if state.config.restart_on_crash != restart_on_crash:
                        logger.debug(f"Config changed via state.json: {name}.restart_on_crash={restart_on_crash}")
                        state.config.restart_on_crash = restart_on_crash
                    
                    if state.config.auto_start != auto_start:
                        logger.debug(f"Config changed via state.json: {name}.auto_start={auto_start}")
                        state.config.auto_start = auto_start
        except Exception as e:
            logger.debug(f"Failed to sync state config: {e}")
    
    def _on_program_log(self, program_name: str, log_level: str, message: str):
        """Callback for program logs - forward to log forwarder"""
        if self.log_forwarder:
            self.log_forwarder.collect_log(
                program_name=program_name,
                log_level=log_level,
                message=message,
            )
    
    async def _start_log_forwarder(self):
        """Initialize log forwarder (disabled by default, controlled by server)"""
        # Log forwarder is not started by default, only initialize when server enables it
        logger.info("Log forwarder disabled by default, waiting for server command to enable")
    
    async def _ensure_log_forwarder(self):
        """Ensure log forwarder is initialized, create if not exists"""
        if self.log_forwarder:
            return True
        
        server_url = self.config.get('server_url', 'http://localhost:8000')
        host_id = self.config.get('host_id')
        
        if not host_id:
            logger.warning("Cannot enable log forwarder: host_id not configured")
            return False
        
        self.log_forwarder = LogForwarder(
            server_url=server_url,
            host_id=host_id,
            request_signing_secret=self.config.get('request_signing_secret', ''),
            ssl_verify=self.config.get('ssl_verify', False),
            ssl_ca_path=self.config.get('ssl_ca_path', ''),
            batch_size=self.config.get('log_batch_size', 100),
            max_buffer_size=self.config.get('log_max_buffer_size', 10000),
            send_interval=self.config.get('log_send_interval', 30),
            min_log_level=self.config.get('log_min_level', 'INFO'),
            backup_servers=self.config.get('backup_servers', []),
        )
        
        await self.log_forwarder.start()
        self.log_forwarder.attach_to_logger('taurus-supervisor')
        
        logger.info("Log forwarder enabled and started")
        return True
    
    async def _shutdown(self):
        """Shutdown daemon"""
        logger.info("Taurus Supervisor is shutting down...")
        self._running = False
        
        # Stop log forwarder
        if self.log_forwarder:
            await self.log_forwarder.stop()
            logger.info("Log forwarder stopped")
        
        # Stop Supervisor heartbeat
        if self.heartbeat_manager:
            await self.heartbeat_manager.stop()
            logger.info("Supervisor heartbeat stopped")
        
        # Stop all programs
        for name in list(self.program_manager.programs.keys()):
            await self.program_manager.stop_program(name)
        
        logger.info("Taurus Supervisor has been shut down")


def load_config() -> Dict[str, Any]:
    """Load configuration"""
    import os
    
    # Determine configuration path based on user type
    is_root = os.geteuid() == 0
    
    if is_root:
        config_paths = [
            Path('/etc/taurus-supervisor/config.yaml'),
            Path('/etc/taurus-supervisor/config.json'),
        ]
        default_base_dir = '/opt/taurus'
    else:
        home = Path.home()
        config_paths = [
            home / '.config' / 'taurus-supervisor' / 'config.yaml',
            home / '.config' / 'taurus-supervisor' / 'config.json',
            home / '.taurus-supervisor' / 'config.yaml',
            home / '.taurus-supervisor' / 'config.json',
        ]
        default_base_dir = str(home / 'taurus')
    
    # Try to load configuration file
    config = {}
    for config_file in config_paths:
        if config_file.exists():
            if config_file.suffix == '.json':
                with open(config_file, 'r') as f:
                    config = json.load(f) or {}
            else:
                import yaml
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f) or {}
            config.setdefault('base_dir', default_base_dir)
            break
    
    # If configuration file doesn't exist, use default configuration
    if not config:
        config = {
            'base_dir': default_base_dir,
            'server_url': 'http://localhost:8000',
            'health_check_interval': 30,
        }
    
    # Environment variables have higher priority, override configuration file
    config['base_dir'] = os.environ.get('BASE_DIR', config.get('base_dir'))
    config['server_url'] = os.environ.get('SERVER_URL', config.get('server_url'))
    config['host_id'] = os.environ.get('HOST_ID', config.get('host_id'))
    
    # Load signing key and auto-decrypt (supports Fernet encrypted format)
    # By default obtained from environment variable or configuration file, supports auto-decryption
    raw_secret = os.environ.get('REQUEST_SIGNING_SECRET', config.get('request_signing_secret', ''))
    config['request_signing_secret'] = auto_decrypt(raw_secret)
    if raw_secret.startswith('fernet:'):
        logger.info("Signing key decrypted from Fernet encrypted format")
    
    config['health_check_interval'] = int(os.environ.get('HEALTH_CHECK_INTERVAL', config.get('health_check_interval', 30)))
    config['supervisor_heartbeat_interval'] = int(os.environ.get('SUPERVISOR_HEARTBEAT_INTERVAL', config.get('supervisor_heartbeat_interval', 30)))
    
    # SSL configuration (supports environment variable override, compatible with old variable names)
    # SSL verification disabled by default, use HTTP in development environment
    config['ssl_verify'] = os.environ.get('SUPERVISOR_SSL_VERIFY', os.environ.get('SSL_VERIFY', 'false')).lower() == 'true'
    config['ssl_ca_path'] = os.environ.get('SUPERVISOR_SSL_CA_PATH', os.environ.get('SSL_CA_PATH', config.get('ssl_ca_path', '')))
    
    # Load backup server configuration
    backup_servers_env = os.environ.get('BACKUP_SERVERS', '')
    if backup_servers_env:
        try:
            config['backup_servers'] = json.loads(backup_servers_env)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid BACKUP_SERVERS environment variable format: {e}")
            config['backup_servers'] = []
    elif 'backup_servers' not in config:
        config['backup_servers'] = []
    
    # Load host username (from environment variable or configuration file)
    config['host_username'] = os.environ.get('HOST_USERNAME', config.get('host_username', ''))
    
    # Load log forwarder configuration
    config['log_batch_size'] = int(os.environ.get('LOG_BATCH_SIZE', config.get('log_batch_size', 100)))
    config['log_max_buffer_size'] = int(os.environ.get('LOG_MAX_BUFFER_SIZE', config.get('log_max_buffer_size', 10000)))
    config['log_send_interval'] = int(os.environ.get('LOG_SEND_INTERVAL', config.get('log_send_interval', 30)))
    config['log_min_level'] = os.environ.get('LOG_MIN_LEVEL', config.get('log_min_level', 'ERROR'))
    
    # Debug information
    logger.debug(f"Environment variable HOST_ID: {os.environ.get('HOST_ID')}")
    logger.debug(f"Environment variable HOST_USERNAME: {os.environ.get('HOST_USERNAME')}")
    logger.debug(f"Configuration host_id: {config.get('host_id')}")
    logger.debug(f"Configuration host_username: {config.get('host_username')}")
    
    return config


def main():
    """Main function"""
    config = load_config()
    supervisor = TaurusSupervisor(config)
    
    try:
        asyncio.run(supervisor.run())
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Supervisor runtime error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()