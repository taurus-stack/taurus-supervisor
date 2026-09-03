"""
Supervisor Heartbeat Manager

Responsibilities:
1. Periodically send heartbeat to Server
2. Receive and execute management commands from Server
3. Report Supervisor and all managed program statuses
4. Report system metrics (CPU, memory, disk, network, etc.)
5. Receive program configuration commands (install, upgrade, stop, etc.)
"""

import asyncio
import json
import logging
import os
import socket
import ssl
import time
import getpass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiohttp
import psutil

from taurus_supervisor.config_crypto import auto_decrypt

logger = logging.getLogger('taurus-supervisor.heartbeat')


def get_current_username() -> str:
    """Get the system username of the current running process"""
    try:
        return getpass.getuser()
    except Exception:
        try:
            return os.getlogin()
        except Exception:
            return 'unknown'


class MetricsCollector:
    """System metrics collector"""

    @staticmethod
    def collect() -> dict:
        """Collect system metrics"""
        # CPU usage (sample for 0.5 seconds)
        cpu_usage = psutil.cpu_percent(interval=0.5)

        # Memory usage
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        memory_available_mb = memory.available / 1024 / 1024

        # Disk usage (root partition)
        disk_usage = psutil.disk_usage('/').percent

        # System load
        try:
            load_1, load_5, load_15 = os.getloadavg()
            load_average = f"{load_1} {load_5} {load_15}"
        except (OSError, AttributeError):
            load_average = None

        # Network traffic
        net_io = psutil.net_io_counters()
        network_rx_bytes = net_io.bytes_recv
        network_tx_bytes = net_io.bytes_sent

        # Process count
        process_count = len(psutil.pids())

        # System uptime (seconds)
        uptime_seconds = int(time.time() - psutil.boot_time())

        return {
            'cpu_usage': round(cpu_usage, 1),
            'memory_usage': round(memory_usage, 1),
            'memory_available_mb': round(memory_available_mb, 1),
            'disk_usage': round(disk_usage, 1),
            'load_average': load_average,
            'network_rx_bytes': network_rx_bytes,
            'network_tx_bytes': network_tx_bytes,
            'process_count': process_count,
            'uptime_seconds': uptime_seconds,
        }


class CommandState:
    """Command execution state enumeration"""
    PENDING = 'pending'           # Pending execution
    DOWNLOADING = 'downloading'   # Downloading
    VERIFYING = 'verifying'       # Verifying
    INSTALLING = 'installing'     # Installing
    STARTING = 'starting'         # Starting
    STOPPING = 'stopping'         # Stopping
    REMOVING = 'removing'         # Removing
    COMPLETED = 'completed'       # Completed
    FAILED = 'failed'             # Failed


class CommandPersistenceManager:
    """Command persistence manager"""
    
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.commands_file = self.data_dir / 'pending_commands.json'
        self._commands: List[Dict[str, Any]] = []
        self._load_commands()
    
    def _load_commands(self):
        """Load commands from file"""
        if self.commands_file.exists():
            try:
                with open(self.commands_file, 'r') as f:
                    self._commands = json.load(f)
                logger.info(f"Loaded {len(self._commands)} pending commands from persistence file")
            except Exception as e:
                logger.error(f"Failed to load command file: {e}")
                self._commands = []
        else:
            self._commands = []
    
    def _save_commands(self):
        """Save commands to file"""
        try:
            # Filter out non-serializable fields (e.g., _binary_data)
            serializable_commands = []
            for cmd in self._commands:
                clean_cmd = {k: v for k, v in cmd.items() if not k.startswith('_')}
                serializable_commands.append(clean_cmd)
            
            with open(self.commands_file, 'w') as f:
                json.dump(serializable_commands, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save command file: {e}")
    
    def add_command(self, command: Dict[str, Any]):
        """Add command"""
        command['retry_count'] = command.get('retry_count', 0)
        command['created_at'] = command.get('created_at', time.time())
        command['last_error'] = None
        command['state'] = command.get('state', CommandState.PENDING)
        command['state_updated_at'] = time.time()
        self._commands.append(command)
        self._save_commands()
    
    def update_command_state(self, command: Dict[str, Any], state: str, **extra):
        """Update command execution state"""
        for cmd in self._commands:
            if cmd == command or cmd.get('command_id') == command.get('command_id'):
                cmd['state'] = state
                cmd['state_updated_at'] = time.time()
                cmd.update(extra)
                self._save_commands()
                logger.info(f"Command state updated: command_id={command.get('command_id')}, state={state}")
                return
        logger.warning(f"Command not found, cannot update state: command_id={command.get('command_id')}")
    
    def get_commands(self) -> List[Dict[str, Any]]:
        """Get all pending commands"""
        return list(self._commands)
    
    def remove_command(self, command: Dict[str, Any]):
        """Remove command (execution successful)"""
        self._commands = [c for c in self._commands if c != command and c.get('command_id') != command.get('command_id')]
        self._save_commands()
    
    def update_command_error(self, command: Dict[str, Any], error: str):
        """Update command error information (for retry)"""
        for cmd in self._commands:
            if cmd == command or cmd.get('command_id') == command.get('command_id'):
                cmd['retry_count'] = cmd.get('retry_count', 0) + 1
                cmd['last_error'] = error
                cmd['last_retry_at'] = time.time()
                cmd['state'] = CommandState.FAILED
                self._save_commands()
                return
    
    def clear_all(self):
        """Clear all commands"""
        self._commands.clear()
        self._save_commands()
    
    @property
    def pending_count(self) -> int:
        """Number of pending commands"""
        return len(self._commands)


class ServerNode:
    """Server node"""
    
    def __init__(self, url: str, priority: int = 0, is_primary: bool = False):
        self.url = url.rstrip('/')
        self.priority = priority
        self.is_primary = is_primary
        self.consecutive_failures = 0
        self.last_success_time = 0
        self.last_failure_time = 0
        self.is_available = True
    
    def record_success(self):
        """Record success"""
        self.consecutive_failures = 0
        self.last_success_time = time.time()
        self.is_available = True
    
    def record_failure(self):
        """Record failure"""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= 3:
            self.is_available = False
    
    def reset(self):
        """Reset state"""
        self.consecutive_failures = 0
        self.is_available = True
    
    def __repr__(self):
        return f"ServerNode(url={self.url}, priority={self.priority}, available={self.is_available})"


class ServerFailoverManager:
    """Server failover manager"""
    
    def __init__(self, servers: List[Dict[str, Any]]):
        self.nodes: List[ServerNode] = []
        self.current_node: Optional[ServerNode] = None
        self._load_servers(servers)
    
    def _load_servers(self, servers: List[Dict[str, Any]]):
        """Load server list"""
        for idx, server in enumerate(servers):
            url = server.get('url', '')
            priority = server.get('priority', idx)
            is_primary = server.get('is_primary', idx == 0)
            
            if url:
                node = ServerNode(url, priority, is_primary)
                self.nodes.append(node)
        
        # Sort by priority (lower number means higher priority)
        self.nodes.sort(key=lambda n: n.priority)
        
        # Select the first available node
        if self.nodes:
            self.current_node = self.nodes[0]
            logger.info(f"Loaded {len(self.nodes)} server nodes, current primary: {self.current_node.url}")
    
    def get_current_server(self) -> Optional[str]:
        """Get current server URL"""
        if self.current_node and self.current_node.is_available:
            return self.current_node.url

        # Current node unavailable (or none), try switching to a backup
        if self._switch_to_next():
            return self.current_node.url

        # All nodes marked unavailable: reset failover state so heartbeats
        # can retry instead of staying dead forever
        if self.nodes:
            logger.warning(
                "All servers unavailable, resetting failover state to retry "
                f"(primary: {self.nodes[0].url})"
            )
            for node in self.nodes:
                node.reset()
            self.current_node = self.nodes[0]
            return self.current_node.url

        return None
    
    def record_success(self):
        """Record current server success"""
        if self.current_node:
            self.current_node.record_success()
    
    def record_failure(self) -> bool:
        """
        Record current server failure
        Returns: Whether switching to another server is needed
        """
        if not self.current_node:
            return False
        
        self.current_node.record_failure()
        
        if not self.current_node.is_available:
            logger.warning(f"Current server {self.current_node.url} unavailable, attempting switch...")
            return self._switch_to_next()
        
        return False
    
    def _switch_to_next(self) -> bool:
        """Switch to next available server"""
        current_idx = self.nodes.index(self.current_node) if self.current_node in self.nodes else -1
        
        for i, node in enumerate(self.nodes):
            if i <= current_idx:
                continue
            if node.is_available:
                self.current_node = node
                logger.info(f"Switched to backup server: {node.url} (priority: {node.priority})")
                return True
        
        # If no higher priority found, try from the beginning
        for i, node in enumerate(self.nodes):
            if i >= current_idx:
                continue
            if node.is_available:
                self.current_node = node
                logger.info(f"Switched to backup server: {node.url} (priority: {node.priority})")
                return True
        
        logger.error("All servers unavailable")
        return False
    
    def get_all_servers(self) -> List[Dict[str, Any]]:
        """Get all server statuses"""
        return [
            {
                'url': node.url,
                'priority': node.priority,
                'is_primary': node.is_primary,
                'is_available': node.is_available,
                'consecutive_failures': node.consecutive_failures,
                'is_current': node == self.current_node,
            }
            for node in self.nodes
        ]
    
    @property
    def has_backup(self) -> bool:
        """Whether backup servers exist"""
        return len(self.nodes) > 1


class SupervisorHeartbeatManager:
    """Supervisor heartbeat manager"""
    
    def __init__(
        self,
        server_url: str,
        host_id: str,
        interval: int = 30,
        timeout: int = 10,
        max_failures: int = 5,
        data_dir: str = None,
        backup_servers: List[Dict[str, Any]] = None,
        request_signing_secret: str = '',
        ssl_verify: bool = True,
        ssl_ca_path: str = None,
        host_username: str = None,
        host_name: str = None,
    ):
        self.host_id = host_id
        # Auto-detect system username running Supervisor (if not specified or empty)
        self.host_username = host_username if host_username else get_current_username()
        # Auto-detect hostname (if not specified or empty)
        self.host_name = host_name if host_name else socket.gethostname()
        self.interval = interval
        self.timeout = timeout
        self.max_failures = max_failures
        # Auto-decrypt signing secret (if encrypted)
        self.request_signing_secret = auto_decrypt(request_signing_secret)
        self.ssl_verify = ssl_verify
        self.ssl_ca_path = ssl_ca_path
        
        # Server failover manager
        if backup_servers:
            servers = [{'url': server_url, 'is_primary': True, 'priority': 0}] + backup_servers
            self.failover_manager = ServerFailoverManager(servers)
        else:
            self.failover_manager = None
        
        # Compatibility with old interface
        self.server_url = server_url.rstrip('/')
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._consecutive_failures = 0
        self._heartbeat_count = 0
        
        # SSL context
        self._ssl_context = self._create_ssl_context()
        
        # Status information (updated externally)
        self.supervisor_version = "1.0.0"
        self.programs_status: List[Dict[str, Any]] = []
        
        # Command persistence manager
        if data_dir:
            self.persistence_manager = CommandPersistenceManager(data_dir)
        else:
            self.persistence_manager = None
        
        # Program configuration commands (received by heartbeat, compatibility with old interface)
        self._pending_program_configs: List[Dict[str, Any]] = []
        
        # Last server URL heartbeat was sent to (for detecting switches)
        self._last_reported_server_url: Optional[str] = None
    
    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Create SSL context
        
        Returns:
            SSL context object, or None if verification is disabled
        """
        if not self.ssl_verify:
            logger.warning("SSL verification disabled! Not recommended for production environments")
            return None
        
        ctx = ssl.create_default_context()
        
        # If custom CA certificate path is specified
        if self.ssl_ca_path:
            if os.path.exists(self.ssl_ca_path):
                ctx.load_verify_locations(self.ssl_ca_path)
                logger.info(f"Loaded custom CA certificate: {self.ssl_ca_path}")
            else:
                logger.error(f"CA certificate file not found: {self.ssl_ca_path}")
                return None
        
        return ctx
    
    async def start(self):
        """Start heartbeat task"""
        if self._running:
            logger.warning("Supervisor heartbeat already running")
            return
        
        self._running = True
        
        # Configure aiohttp connector to use SSL
        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"Supervisor heartbeat started: interval={self.interval}s, ssl_verify={self.ssl_verify}")
    
    async def stop(self):
        """Stop heartbeat task"""
        if not self._running:
            return
        
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self._session:
            await self._session.close()
        
        logger.info("Supervisor heartbeat stopped")
    
    async def _heartbeat_loop(self):
        """Heartbeat loop"""
        while self._running:
            try:
                await self._send_heartbeat()
                self._consecutive_failures = 0
                self._heartbeat_count += 1
                
                # Record server success
                if self.failover_manager:
                    self.failover_manager.record_success()
                
                # Wait for next heartbeat
                await asyncio.sleep(self.interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(
                    f"Supervisor heartbeat error #{self._consecutive_failures}: {e}"
                )
                
                # Record server failure, may need to switch
                if self.failover_manager:
                    switched = self.failover_manager.record_failure()
                    if switched:
                        logger.info(f"Switched to new server: {self.failover_manager.get_current_server()}")
                
                if self._consecutive_failures >= self.max_failures:
                    logger.critical(
                        f"Supervisor heartbeat failed {self._consecutive_failures} consecutive times"
                    )
                
                # Exponential backoff
                wait_time = min(2 ** self._consecutive_failures, 60)
                await asyncio.sleep(wait_time)
    
    async def _send_heartbeat(self):
        """Send heartbeat"""
        # Use enhanced metrics collector (run in a thread so the blocking
        # psutil.cpu_percent(interval=...) sampling doesn't block the loop)
        metrics = await asyncio.to_thread(MetricsCollector.collect)

        logger.debug(f"Heartbeat programs_status: {self.programs_status}")

        payload = {
            'host_id': self.host_id,
            'host_name': self.host_name,
            'host_username': self.host_username,
            'supervisor_version': self.supervisor_version,
            'programs': self.programs_status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'metrics': metrics,
        }
        
        # Add executing command status (for reporting after crash recovery)
        if self.persistence_manager:
            executing_commands = []
            for cmd in self.persistence_manager.get_commands():
                state = cmd.get('state', CommandState.PENDING)
                if state not in (CommandState.COMPLETED, CommandState.FAILED):
                    executing_commands.append({
                        'command_id': cmd.get('command_id'),
                        'command_type': cmd.get('command_type'),
                        'state': state,
                        'state_updated_at': cmd.get('state_updated_at'),
                    })
            if executing_commands:
                payload['executing_commands'] = executing_commands
        
        # Get current server URL and always include it in heartbeat
        if self.failover_manager:
            server_url = self.failover_manager.get_current_server()
            if not server_url:
                raise Exception("All servers unavailable")
        else:
            server_url = self.server_url
        
        payload['current_server_url'] = server_url
        self._last_reported_server_url = server_url
        
        # Add signature (if signing secret is configured)
        signing_secret = self.request_signing_secret or os.environ.get('REQUEST_SIGNING_SECRET', '')
        if signing_secret:
            import time
            import uuid
            import hashlib
            import hmac
            import json
            
            timestamp_int = int(time.time())
            nonce = str(uuid.uuid4())
            
            # First add signature parameters (with empty signature placeholder)
            payload['signature'] = ''
            payload['nonce'] = nonce
            payload['timestamp_int'] = timestamp_int
            
            # Calculate signature using full request body
            body_str = json.dumps(payload, sort_keys=True)
            message = f"{self.host_id}:{timestamp_int}:{nonce}:{body_str}"
            signature = hmac.new(
                signing_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Update with real signature
            payload['signature'] = signature
        
        heartbeat_url = f"{server_url}/api/taurus/supervisor/heartbeat/"
        
        async with self._session.post(heartbeat_url, json=payload) as resp:
            if resp.status == 200:
                response_data = await resp.json()
                
                # Check if business logic succeeded (HTTP 200 doesn't mean business success)
                if response_data.get('code') != 2000:
                    error_msg = response_data.get('msg', 'Unknown error')
                    logger.warning(f"Supervisor heartbeat business logic failed: {error_msg}")
                    raise Exception(f"Business error: {error_msg}")
                
                await self._handle_commands(response_data)
                
                # Log detailed metrics
                logger.debug(
                    f"Heartbeat #{self._heartbeat_count + 1}: "
                    f"cpu={metrics['cpu_usage']}% mem={metrics['memory_usage']}% "
                    f"disk={metrics['disk_usage']}% load={metrics['load_average']} "
                    f"procs={metrics['process_count']} uptime={metrics['uptime_seconds']}s"
                )
            else:
                body = await resp.text()
                logger.warning(f"Supervisor heartbeat failed: status={resp.status}, body={body}")
                raise Exception(f"HTTP {resp.status}")
    
    async def _handle_commands(self, response_data: dict):
        """Handle server commands"""
        if not response_data:
            return
        
        data = response_data.get('data') or {}
        commands = data.get('commands', [])
        
        for command in commands:
            cmd_type = command.get('type')
            
            try:
                if cmd_type == 'install_program':
                    await self._handle_install_program(command)
                elif cmd_type == 'upgrade_program':
                    await self._handle_upgrade_program(command)
                elif cmd_type == 'start_program':
                    await self._handle_start_program(command)
                elif cmd_type == 'stop_program':
                    await self._handle_stop_program(command)
                elif cmd_type == 'restart_program':
                    await self._handle_restart_program(command)
                elif cmd_type == 'remove_program':
                    await self._handle_remove_program(command)
                elif cmd_type == 'log_collect':
                    await self._handle_log_collect(command)
                else:
                    logger.debug(f"Unknown command type: {cmd_type}")
            except Exception as e:
                logger.error(f"Failed to execute command [{cmd_type}]: {e}")
    
    # ===== Generic Program Management Commands =====
    
    async def _handle_log_collect(self, command: dict):
        """Handle log collection on-demand command"""
        action = command.get('log_action', 'start')
        min_level = command.get('min_level', 'INFO')
        programs = command.get('programs', [])
        duration = command.get('duration', 1800)
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'log_command')
        
        logger.info(f"Received log_collect command: action={action}, level={min_level}, "
                    f"programs={programs or 'all'}, duration={duration}s")
        
        cmd_data = {
            'action': 'log_collect',
            'log_action': action,
            'min_level': min_level,
            'programs': programs,
            'duration': duration,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
            logger.info(f"Command persisted: log_collect {action}")
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_install_program(self, command: dict):
        """Handle install program command"""
        program_name = command.get('program_name')
        version = command.get('version')
        download_url = command.get('download_url')
        sha256 = command.get('sha256')
        config = command.get('config', {})
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        
        logger.info(f"Received install program command: {program_name} v{version}")
        
        cmd_data = {
            'action': 'install',
            'program_name': program_name,
            'version': version,
            'download_url': download_url,
            'sha256': sha256,
            'config': config,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
            logger.info(f"Command persisted: install {program_name} v{version}")
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_upgrade_program(self, command: dict):
        """Handle upgrade program command"""
        program_name = command.get('program_name')
        target_version = command.get('target_version')
        download_url = command.get('download_url')
        sha256 = command.get('sha256')
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        
        logger.info(f"Received upgrade program command: {program_name} -> v{target_version}")
        
        cmd_data = {
            'action': 'upgrade',
            'program_name': program_name,
            'target_version': target_version,
            'download_url': download_url,
            'sha256': sha256,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
            logger.info(f"Command persisted: upgrade {program_name} -> v{target_version}")
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_start_program(self, command: dict):
        """Handle start program command"""
        program_name = command.get('program_name')
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        logger.info(f"Received start program command: {program_name}")
        
        cmd_data = {
            'action': 'start',
            'program_name': program_name,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_stop_program(self, command: dict):
        """Handle stop program command"""
        program_name = command.get('program_name')
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        logger.info(f"Received stop program command: {program_name}")
        
        cmd_data = {
            'action': 'stop',
            'program_name': program_name,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_restart_program(self, command: dict):
        """Handle restart program command"""
        program_name = command.get('program_name')
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        logger.info(f"Received restart program command: {program_name}")
        
        cmd_data = {
            'action': 'restart',
            'program_name': program_name,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
        else:
            self._pending_program_configs.append(cmd_data)
    
    async def _handle_remove_program(self, command: dict):
        """Handle remove program command"""
        program_name = command.get('program_name')
        command_id = command.get('command_id')
        command_type = command.get('command_type', 'program_command')
        logger.info(f"Received remove program command: {program_name}")
        
        cmd_data = {
            'action': 'remove',
            'program_name': program_name,
            'command_id': command_id,
            'command_type': command_type,
            'timestamp': time.time(),
        }
        
        # If persistence is enabled, only add to persistence file (avoid duplicate processing)
        if self.persistence_manager:
            self.persistence_manager.add_command(cmd_data)
        else:
            self._pending_program_configs.append(cmd_data)
    
    def get_pending_program_configs(self) -> List[dict]:
        """Get pending program configuration commands"""
        return list(self._pending_program_configs)
    
    def clear_pending_program_configs(self):
        """Clear pending program configuration commands"""
        self._pending_program_configs.clear()
    
    async def report_command_result(self, command_id: int, command_type: str, success: bool, message: str):
        """Report command execution result to server"""
        if not self._session:
            logger.warning("Cannot report command result: HTTP session not initialized")
            return
        
        # Get current server URL
        if self.failover_manager:
            server_url = self.failover_manager.get_current_server()
            if not server_url:
                logger.error("Cannot report command result: all servers unavailable")
                return
        else:
            server_url = self.server_url
        
        report_url = f"{server_url}/api/taurus/supervisor/report_command_result/"
        
        payload = {
            'host_id': self.host_id,
            'command_id': command_id,
            'command_type': command_type,
            'status': 2 if success else 3,  # 2=success, 3=failed
            'result_message': message,
        }
        
        # Add signature (if signing secret is configured)
        signing_secret = self.request_signing_secret or os.environ.get('REQUEST_SIGNING_SECRET', '')
        if signing_secret:
            import time
            import uuid
            import hashlib
            import hmac
            import json
            
            timestamp_int = int(time.time())
            nonce = str(uuid.uuid4())
            
            # First add signature parameters (with empty signature placeholder)
            payload['signature'] = ''
            payload['nonce'] = nonce
            payload['timestamp_int'] = timestamp_int
            
            # Calculate signature using full request body
            body_str = json.dumps(payload, sort_keys=True)
            message = f"{self.host_id}:{timestamp_int}:{nonce}:{body_str}"
            signature = hmac.new(
                signing_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Update with real signature
            payload['signature'] = signature
        
        try:
            async with self._session.post(report_url, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Command result reported successfully: command_id={command_id}")
                else:
                    body = await resp.text()
                    logger.error(f"Command result report failed: command_id={command_id}, http_status={resp.status}, body={body}")
        except Exception as e:
            logger.error(f"Command result report error: command_id={command_id}, error={e}")
            # Retry on report failure (max 3 times, with increasing intervals)
            import asyncio
            for retry in range(1, 4):
                try:
                    logger.info(f"Command result report retry ({retry}/3): command_id={command_id}")
                    await asyncio.sleep(2 * retry)
                    async with self._session.post(report_url, json=payload) as resp:
                        if resp.status == 200:
                            logger.info(f"Command result reported successfully (after {retry} retries): command_id={command_id}")
                            return
                        else:
                            body = await resp.text()
                            logger.error(f"Command result report failed (retry {retry}): command_id={command_id}, http_status={resp.status}")
                except Exception as retry_e:
                    logger.error(f"Command result report retry error ({retry}/3): command_id={command_id}, error={retry_e}")
            
            logger.error(f"Command result report completely failed (after 3 retries): command_id={command_id}")
    
    def update_programs_status(self, programs_status: List[Dict[str, Any]]):
        """Update all program statuses (called by ProgramManager)"""
        self.programs_status = programs_status
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def heartbeat_count(self) -> int:
        return self._heartbeat_count
    
    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures