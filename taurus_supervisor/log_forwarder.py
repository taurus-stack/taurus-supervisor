"""
Log Forwarder Module

Responsibilities:
1. Collect logs from Supervisor and managed programs
2. Buffer logs in memory with configurable size limits
3. Periodically forward logs to backend server
4. Support request signing for security
5. Handle failures and retry logic
6. Support log level filtering
7. Support program-specific log collection
"""

import asyncio
import json
import logging
import os
import ssl
import time
import uuid
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from collections import deque

import aiohttp

from taurus_supervisor.config_crypto import auto_decrypt

logger = logging.getLogger('taurus-supervisor.log_forwarder')


class LogLevelFilter(logging.Filter):
    """Log level filter - only allow logs at or above specified level"""
    
    def __init__(self, min_level: int):
        super().__init__()
        self.min_level = min_level
    
    def filter(self, record):
        return record.levelno >= self.min_level


class LogForwarderHandler(logging.Handler):
    """Custom logging handler that forwards logs to the log forwarder"""
    
    def __init__(self, log_forwarder: 'LogForwarder', program_name: str = None):
        super().__init__()
        self.log_forwarder = log_forwarder
        self.program_name = program_name
    
    def emit(self, record):
        try:
            self.log_forwarder.collect_log(
                program_name=self.program_name,
                log_level=record.levelname,
                message=self.format(record),
                source_file=record.pathname,
                source_line=record.lineno,
                process_id=record.process,
                thread_id=record.thread,
                log_time=datetime.fromtimestamp(record.created, timezone.utc),
            )
        except Exception as e:
            logger.error(f"Failed to emit log to forwarder: {e}")


class LogForwarder:
    """Log forwarder - collects and forwards logs to remote backend"""
    
    LEVEL_MAP = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }
    
    def __init__(
        self,
        server_url: str,
        host_id: str,
        request_signing_secret: str = '',
        ssl_verify: bool = True,
        ssl_ca_path: str = None,
        batch_size: int = 100,
        max_buffer_size: int = 10000,
        send_interval: int = 30,
        min_log_level: str = 'INFO',
        backup_servers: List[Dict[str, Any]] = None,
    ):
        self.server_url = server_url.rstrip('/')
        self.host_id = host_id
        self.request_signing_secret = auto_decrypt(request_signing_secret)
        self.ssl_verify = ssl_verify
        self.ssl_ca_path = ssl_ca_path
        self.batch_size = batch_size
        self.max_buffer_size = max_buffer_size
        self.send_interval = send_interval
        self.min_log_level = min_log_level
        
        # Log buffer (thread-safe via deque)
        self._log_buffer = deque(maxlen=max_buffer_size)
        
        # Server failover
        self._servers = [{'url': server_url, 'is_primary': True, 'priority': 0}]
        if backup_servers:
            for i, srv in enumerate(backup_servers):
                self._servers.append({
                    'url': srv.get('server_url', srv.get('url', '')).rstrip('/'),
                    'is_primary': False,
                    'priority': i + 1,
                })
        self._current_server_index = 0
        
        # SSL context
        self._ssl_context = self._create_ssl_context()
        
        # Running state
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        # Main event loop (captured in start(), used for thread-safe scheduling)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Statistics
        self._total_collected = 0
        self._total_sent = 0
        self._total_failed = 0
        self._last_send_time = 0
        
        # On-demand collection mode (disabled by default, no logs forwarded until backend command)
        self._collect_mode = {
            'enabled': False,
            'min_level': 'INFO',
            'programs': [],
            'expires_at': 0,
            'duration': 0,
        }
    
    def _create_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Create SSL context for HTTPS connections"""
        if not self.ssl_verify:
            logger.warning("SSL verification disabled for log forwarder")
            return None
        
        ctx = ssl.create_default_context()
        
        if self.ssl_ca_path:
            if os.path.exists(self.ssl_ca_path):
                ctx.load_verify_locations(self.ssl_ca_path)
                logger.info(f"Loaded custom CA certificate for log forwarder: {self.ssl_ca_path}")
            else:
                logger.error(f"CA certificate file not found: {self.ssl_ca_path}")
                return None
        
        return ctx
    
    def collect_log(
        self,
        program_name: str = None,
        log_level: str = 'INFO',
        message: str = '',
        source_file: str = None,
        source_line: int = None,
        process_id: int = None,
        thread_id: int = None,
        log_time: datetime = None,
        extra_info: dict = None,
    ):
        """
        Collect a log entry into the buffer
        
        Args:
            program_name: Name of the program generating the log (None for Supervisor)
            log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            message: Log message content
            source_file: Source file path
            source_line: Source line number
            process_id: Process ID
            thread_id: Thread ID
            log_time: Log timestamp (defaults to current time)
            extra_info: Additional metadata
        """
        if not message:
            return
        
        if not self._collect_mode['enabled']:
            return
        
        self._check_collect_mode_expiry()
        if not self._collect_mode['enabled']:
            return
        
        entry_level = self.LEVEL_MAP.get(log_level.upper(), logging.INFO)
        
        if self._collect_mode['programs']:
            # program_name=None means Supervisor's own log, matches 'taurus-supervisor'
            effective_name = program_name if program_name else 'taurus-supervisor'
            if effective_name not in self._collect_mode['programs']:
                return
        
        min_level = self.LEVEL_MAP.get(self._collect_mode['min_level'], logging.INFO)
        if entry_level < min_level:
            return
        
        log_entry = {
            'program_name': program_name,
            'log_level': log_level.upper(),
            'message': message,
            'source_file': source_file,
            'source_line': source_line,
            'process_id': process_id,
            'thread_id': thread_id,
            'log_time': (log_time or datetime.now(timezone.utc)).isoformat(),
            'extra_info': extra_info or {},
        }
        
        self._log_buffer.append(log_entry)
        self._total_collected += 1
        
        # If buffer exceeds batch size, trigger immediate send
        if len(self._log_buffer) >= self.batch_size:
            logger.debug(f"Log buffer full ({len(self._log_buffer)}), triggering send")
            # collect_log may be called from non-event-loop threads
            # (e.g. program log reader threads), so schedule the flush
            # thread-safely instead of asyncio.create_task
            if self._loop and self._loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(self._send_logs(), self._loop)
                except RuntimeError as e:
                    logger.debug(f"Failed to schedule log send: {e}")
    
    def get_log_buffer_size(self) -> int:
        """Get current buffer size"""
        return len(self._log_buffer)
    
    def get_statistics(self) -> Dict[str, int]:
        """Get log forwarding statistics"""
        return {
            'total_collected': self._total_collected,
            'total_sent': self._total_sent,
            'total_failed': self._total_failed,
            'buffer_size': len(self._log_buffer),
            'last_send_time': self._last_send_time,
        }
    
    async def start(self):
        """Start the log forwarder"""
        if self._running:
            logger.warning("Log forwarder already running")
            return
        
        self._running = True

        # Capture the main event loop for thread-safe scheduling from collect_log
        self._loop = asyncio.get_running_loop()

        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30)
        )
        
        self._task = asyncio.create_task(self._forward_loop())
        logger.info(f"Log forwarder started: interval={self.send_interval}s, batch_size={self.batch_size}, "
                    f"server={self.server_url}, min_level={self.min_log_level}")
    
    async def stop(self):
        """Stop the log forwarder"""
        if not self._running:
            return
        
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        # Send remaining logs
        if self._session and len(self._log_buffer) > 0:
            await self._send_logs()
        
        if self._session:
            await self._session.close()
        
        logger.info("Log forwarder stopped")
    
    async def _forward_loop(self):
        """Main forwarding loop"""
        while self._running:
            try:
                await asyncio.sleep(self.send_interval)
                
                if len(self._log_buffer) > 0:
                    await self._send_logs()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Log forwarder loop error: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    async def _send_logs(self):
        """Send buffered logs to server"""
        if not self._session:
            logger.warning("Cannot send logs: HTTP session not initialized")
            return
        
        if len(self._log_buffer) == 0:
            return
        
        # Get batch of logs
        batch = []
        while len(self._log_buffer) > 0 and len(batch) < self.batch_size:
            batch.append(self._log_buffer.popleft())
        
        logger.debug(f"Sending {len(batch)} logs to server")
        
        # Try all servers with failover
        for attempt, server_info in enumerate(self._servers):
            server_url = server_info['url']
            if not server_url:
                continue
            
            try:
                success = await self._send_to_server(server_url, batch)
                
                if success:
                    self._total_sent += len(batch)
                    self._last_send_time = time.time()
                    self._current_server_index = attempt
                    logger.debug(f"Successfully sent {len(batch)} logs to {server_url}")
                    return
                
            except Exception as e:
                logger.warning(f"Failed to send logs to {server_url} (attempt {attempt + 1}): {e}")
        
        # All servers failed, put logs back to buffer
        for log_entry in batch:
            if len(self._log_buffer) < self.max_buffer_size:
                self._log_buffer.append(log_entry)
        
        self._total_failed += len(batch)
        logger.error(f"Failed to send {len(batch)} logs after {len(self._servers)} attempts")
    
    async def _send_to_server(self, server_url: str, logs: List[Dict[str, Any]]) -> bool:
        """Send logs to a specific server"""
        url = f"{server_url}/api/taurus/host-log/receive/"
        
        payload = {
            'host_id': self.host_id,
            'logs': logs,
        }
        
        # Add signature if configured
        signing_secret = self.request_signing_secret or os.environ.get('REQUEST_SIGNING_SECRET', '')
        if signing_secret:
            timestamp_int = int(time.time())
            nonce = str(uuid.uuid4())
            
            payload['signature'] = ''
            payload['nonce'] = nonce
            payload['timestamp_int'] = timestamp_int
            
            body_str = json.dumps(payload, sort_keys=True)
            message = f"{self.host_id}:{timestamp_int}:{nonce}:{body_str}"
            signature = hmac.new(
                signing_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            payload['signature'] = signature
        
        async with self._session.post(url, json=payload) as resp:
            if resp.status == 200:
                response_data = await resp.json()
                if response_data.get('code') == 2000:
                    return True
                
                error_msg = response_data.get('msg', 'Unknown error')
                logger.warning(f"Log send failed (business error): {error_msg}")
                return False
            
            body = await resp.text()
            logger.warning(f"Log send failed: HTTP {resp.status}, body={body}")
            return False
    
    def _check_collect_mode_expiry(self):
        if self._collect_mode['enabled'] and self._collect_mode['expires_at'] > 0:
            if time.time() >= self._collect_mode['expires_at']:
                self.disable_collect_mode()
                logger.info("Collection mode auto-expired, no more logs will be forwarded")
    
    def set_collect_mode(self, min_level: str = 'INFO', programs: list = None, duration: int = 1800):
        """
        Enable on-demand log collection (disabled by default, no logs forwarded)
        
        Args:
            min_level: Log level threshold to collect
            programs: Target programs (None or empty = all programs)
            duration: Duration in seconds (default 1800 = 30 min, 0 = manual stop via stop command)
        """
        if min_level.upper() not in self.LEVEL_MAP:
            logger.warning(f"Invalid log level for collection mode: {min_level}, using INFO")
            min_level = 'INFO'
        
        self._collect_mode['enabled'] = True
        self._collect_mode['min_level'] = min_level.upper()
        self._collect_mode['programs'] = programs or []
        self._collect_mode['duration'] = duration
        self._collect_mode['expires_at'] = time.time() + duration if duration > 0 else 0
        
        logger.info(f"Collection mode enabled: level={min_level}, programs={programs or 'all'}, duration={duration}s")
    
    def disable_collect_mode(self):
        """Disable on-demand log collection mode, no more logs forwarded"""
        self._collect_mode['enabled'] = False
        self._collect_mode['programs'] = []
        self._collect_mode['expires_at'] = 0
        self._collect_mode['duration'] = 0
        
        logger.info("Collection mode disabled, no more logs will be forwarded to backend")
    
    def get_collect_mode(self) -> Dict[str, Any]:
        """Get current collection mode status"""
        mode = dict(self._collect_mode)
        if mode['expires_at'] > 0:
            mode['remaining'] = max(0, mode['expires_at'] - time.time())
        else:
            mode['remaining'] = -1
        return mode
    
    def attach_to_logger(self, logger_name: str, program_name: str = None):
        target_logger = logging.getLogger(logger_name)
        
        handler = LogForwarderHandler(self, program_name=program_name)
        
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(levelname)s - [%(pathname)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        
        handler.addFilter(LogLevelFilter(logging.DEBUG))
        
        target_logger.addHandler(handler)
        
        logger.info(f"Attached log forwarder to logger '{logger_name}' (program={program_name})")
        
        return handler
    
    @staticmethod
    def tail_file(file_path: str, max_lines: int = 100) -> List[str]:
        """
        Tail a log file and return the last N lines
        
        Args:
            file_path: Path to log file
            max_lines: Maximum number of lines to return
        
        Returns:
            List of log lines (newest first)
        """
        lines = []
        
        try:
            with open(file_path, 'r') as f:
                for line in f:
                    lines.append(line.rstrip('\n'))
                    if len(lines) > max_lines:
                        lines.pop(0)
        except FileNotFoundError:
            logger.warning(f"Log file not found: {file_path}")
        except Exception as e:
            logger.error(f"Failed to read log file {file_path}: {e}")
        
        return lines[-max_lines:]