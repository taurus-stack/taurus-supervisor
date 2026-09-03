#!/usr/bin/env python3
"""
General Program Manager

Responsibilities:
1. Manage multiple managed programs (any executable)
2. Independent process management for each program
3. Support program lifecycle (start, stop, restart)
4. Support program upgrade and rollback
"""

import asyncio
import json
import logging
import os
import pwd
import grp
import subprocess
import tarfile
import io
import signal
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger('taurus-supervisor.program_manager')


class ProgramStatus(str, Enum):
    """Program running status"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"
    UPGRADING = "upgrading"


@dataclass
class ProgramConfig:
    """Program configuration (generic, not coupled to any business fields)"""
    name: str                          # Program name (e.g., taurus-executor)
    binary_name: str                   # Binary file name (e.g., taurus-executor)
    version: str                       # Current version
    env_vars: Dict[str, str] = field(default_factory=dict)  # Environment variables (all business configs passed through here)
    auto_start: bool = True            # Whether to auto-start
    restart_on_crash: bool = True      # Whether to auto-restart on crash
    max_restarts: int = 5              # Maximum restart count (prevent infinite restarts)
    health_check_path: Optional[str] = None  # Health check path (HTTP)
    user: Optional[str] = None         # Running user (None means current user)
    group: Optional[str] = None        # Running group (None means user's primary group)


@dataclass
class ProgramState:
    """Program running state"""
    config: ProgramConfig
    status: ProgramStatus = ProgramStatus.STOPPED
    pid: Optional[int] = None
    port: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    restart_count: int = 0
    last_start_time: Optional[float] = None
    last_crash_time: Optional[float] = None
    upgrade_in_progress: bool = False
    install_in_progress: bool = False  # Prevent concurrent installation conflicts


class ProgramManager:
    """General program manager, manages multiple managed programs"""
    
    def __init__(self, base_dir: Path, state_manager, log_callback=None):
        self.base_dir = base_dir
        self.versions_dir = base_dir / 'versions'
        self.state_manager = state_manager
        self.programs: Dict[str, ProgramState] = {}
        self.log_callback = log_callback
    
    def register_program(self, config: ProgramConfig):
        """Register a managed program (auto-fill default user/group)"""
        # If user/group not specified, default to current supervisor running user and primary group
        if not config.user or not config.group:
            try:
                current_uid = os.getuid()
                current_pw = pwd.getpwuid(current_uid)
                default_user = current_pw.pw_name
                default_gid = current_pw.pw_gid
                
                import grp
                default_group = grp.getgrgid(default_gid).gr_name
                
                if not config.user:
                    config.user = default_user
                    logger.info(f"Program {config.name} using default user: {default_user}")
                if not config.group:
                    config.group = default_group
                    logger.info(f"Program {config.name} using default group: {default_group}")
            except Exception as e:
                logger.warning(f"Failed to get default user/group: {e}, keeping configured user={config.user}, group={config.group}")
        
        state = ProgramState(config=config)
        self.programs[config.name] = state
        logger.info(f"Registered program: {config.name} v{config.version}")
        self._save_program_state(config.name, state)

    @staticmethod
    def _is_tar_gz(data: bytes) -> bool:
        """Detect if byte stream is a tar.gz file"""
        # tar.gz magic bytes: 1f 8b (gzip)
        if len(data) < 2:
            return False
        if data[0] == 0x1f and data[1] == 0x8b:
            return True
        return False

    @staticmethod
    def _check_pid_alive(pid: int) -> bool:
        """Check if process with specified pid is still running"""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _detect_port(env_vars: Dict[str, str]) -> Optional[int]:
        """Detect port from env_vars (try common port variable names by priority)
        Try from high to low priority, return first valid port number found (1-65535).
        """
        # Common port environment variables (sorted by priority, program-specific first)
        PORT_CANDIDATES = [
            'GRPC_PORT', 'PORT', 'HTTP_PORT', 'HTTP_LISTEN_PORT',
            'METRICS_PORT', 'HTTPS_PORT', 'LISTEN_PORT',
            'SUPERVISOR_PORT', 'SERVICE_PORT', 'API_PORT',
        ]
        for key in PORT_CANDIDATES:
            if key in env_vars:
                try:
                    port = int(env_vars[key])
                    if 1 <= port <= 65535:
                        return port
                except (ValueError, TypeError):
                    continue
        return None

    def _install_binary_data(self, binary_data: bytes, version_dir: Path, program_name: str, binary_name: str) -> Path:
        """
        Install program binary data to version directory

        Smart detection:
        - If tar.gz: extract to version directory, use executable with same name as binary_name
        - If pure binary: write directly to version_dir/binary_name

        Returns:
            Path to executable file
        """
        version_dir.mkdir(parents=True, exist_ok=True)

        if self._is_tar_gz(binary_data):
            logger.info(f"Detected tar.gz package, extracting: {program_name} -> {version_dir}")

            try:
                tar_stream = io.BytesIO(binary_data)
                with tarfile.open(fileobj=tar_stream, mode='r:gz') as tar:
                    tar.extractall(path=str(version_dir), filter='data')
                    logger.info(f"Extracted {len(tar.getnames())} files to {version_dir}")
            except Exception as e:
                raise RuntimeError(f"Failed to extract tar.gz: {e}") from e

            # Find executable (prefer matching binary_name)
            binary_path = version_dir / binary_name
            if not binary_path.exists():
                # Try to find in subdirectories
                found = None
                for f in version_dir.rglob('*'):
                    if f.is_file() and f.name == binary_name:
                        found = f
                        break
                if found is None:
                    # Still not found, list directory contents for debugging
                    files = list(version_dir.iterdir())
                    raise RuntimeError(
                        f"Cannot find executable in tar.gz '{binary_name}'，directory contents: {[f.name for f in files]}"
                    )
                binary_path = found

            os.chmod(str(binary_path), 0o755)
            logger.info(f"tar.gz extraction complete, executable: {binary_path}")
            return binary_path
        else:
            # Pure binary file, write directly
            binary_path = version_dir / binary_name
            with open(binary_path, 'wb') as f:
                f.write(binary_data)
            os.chmod(str(binary_path), 0o755)
            logger.info(f"Binary file written to: {binary_path}")
            return binary_path

    def get_program(self, name: str) -> Optional[ProgramState]:
        """Get program state"""
        return self.programs.get(name)
    
    def get_all_programs(self) -> Dict[str, ProgramState]:
        """Get all program states"""
        return self.programs
    
    def get_running_programs(self) -> Dict[str, ProgramState]:
        """Get running programs
        
        Returns:
            Dictionary containing only programs with RUNNING status {name: ProgramState}
        """
        return {
            name: state 
            for name, state in self.programs.items() 
            if state.status == ProgramStatus.RUNNING
        }
    
    async def start_program(self, name: str, reset_restart_count: bool = True) -> bool:
        """Start specified program

        Args:
            name: Program name
            reset_restart_count: Reset crash restart counter (False when called
                from the crash-restart path so max_restarts can take effect)
        """
        if name not in self.programs:
            logger.error(f"Program not registered: {name}")
            return False
        
        state = self.programs[name]
        config = state.config
        
        if state.status == ProgramStatus.RUNNING:
            logger.warning(f"Program already running: {name} (PID={state.pid})")
            return True
        
        # Clean up orphan processes with same name (prevent duplicate start after supervisor restart)
        await self._ensure_no_orphan_processes(config.binary_name, exclude_pid=None)
        
        version_dir = self.versions_dir / f"v{config.version}" / name
        binary_path = version_dir / config.binary_name
        
        if not binary_path.exists():
            logger.error(f"Program binary does not exist: {binary_path}")
            return False
        
        state.status = ProgramStatus.STARTING
        logger.info(f"Starting program: {name} v{config.version}")
        
        # Prepare environment variables (generic passthrough, no hardcoded business fields)
        env = os.environ.copy()
        
        # Inject supervisor-managed common environment variables
        env['PROGRAM_NAME'] = name
        env['PROGRAM_VERSION'] = config.version
        
        # Passthrough all business environment variables (e.g., GRPC_PORT, METRICS_PORT, TLS_DIR, LOG_LEVEL, etc.)
        for key, value in config.env_vars.items():
            env[key] = value
        
        try:
            # Get current user info
            current_uid = os.getuid()
            current_user = pwd.getpwuid(current_uid).pw_name

            # Determine actual running user and their HOME directory as cwd
            target_user = config.user if config.user else current_user
            try:
                if config.user and config.user != current_user:
                    run_pw = pwd.getpwnam(config.user)
                else:
                    run_pw = pwd.getpwuid(current_uid)
                user_home = run_pw.pw_dir
                user_shell = run_pw.pw_shell or '/bin/bash'
            except (KeyError, OSError) as e:
                logger.warning(f"Failed to get HOME for user {target_user}: {e}, fallback to current user HOME")
                run_pw = pwd.getpwuid(current_uid)
                user_home = run_pw.pw_dir
                user_shell = run_pw.pw_shell or '/bin/bash'

            # Sync child process HOME/USER/LOGNAME/SHELL environment variables,
            # Prevent process HOME from still pointing to supervisor original user HOME after user switch
            env['HOME'] = user_home
            env['USER'] = target_user
            env['LOGNAME'] = target_user
            env['SHELL'] = user_shell

            # Ensure cwd directory exists (user HOME must exist, for robustness)
            cwd_path = user_home
            if not os.path.isdir(cwd_path):
                logger.warning(f"User HOME directory does not exist: {cwd_path}, fallback to {version_dir}")
                cwd_path = str(version_dir)

            logger.info(f"Program working directory (cwd): {cwd_path} (user: {target_user})")

            # Prepare preexec_fn for child process to switch user
            preexec_fn = None
            if config.user:
                # Specified user is same as current user, no switch needed
                if config.user == current_user:
                    logger.info(f"Program running as current user {config.user} (no switch needed)")
                else:
                    # Specified user is different from current user, need to switch
                    # Only root can switch user
                    if current_uid == 0:
                        try:
                            pw = pwd.getpwnam(config.user)
                            uid = pw.pw_uid
                            gid = grp.getgrnam(config.group).gr_gid if config.group else pw.pw_gid

                            def set_user():
                                os.setgid(gid)
                                os.setuid(uid)

                            preexec_fn = set_user
                            logger.info(f"Program will run as user {config.user}:{config.group or pw.pw_name} (root switch)")
                        except KeyError as e:
                            logger.warning(f"User or group not found: {config.user} - {e}, will run as current user {current_user}")
                    else:
                        logger.warning(
                            f"Supervisor running as normal user {current_user}, cannot switch to {config.user},"
                            f"will run as current user"
                        )

            process = subprocess.Popen(
                [str(binary_path)],
                cwd=cwd_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=preexec_fn,
            )
            
            state.process = process
            state.pid = process.pid
            state.port = self._detect_port(config.env_vars)
            state.status = ProgramStatus.RUNNING
            state.last_start_time = asyncio.get_event_loop().time()
            if reset_restart_count:
                state.restart_count = 0
            state.config.restart_on_crash = True
            
            self._save_program_state(name, state)
            
            if self.log_callback:
                asyncio.create_task(self._read_program_logs(name, process))
            
            logger.info(f"Program started: {name} PID={process.pid} PORT={state.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start program: {name} - {e}")
            state.status = ProgramStatus.CRASHED
            state.last_crash_time = asyncio.get_event_loop().time()
            return False
    
    async def stop_program(self, name: str, timeout: int = 10) -> bool:
        """Stop specified program"""
        if name not in self.programs:
            logger.error(f"Program not registered: {name}")
            return False
        
        state = self.programs[name]
        
        # Two valid running states: (status=RUNNING with process) or (has pid pointing to running process)
        has_process = state.status == ProgramStatus.RUNNING and state.process
        has_pid = state.pid
        if not has_process and not (has_pid and self._check_pid_alive(state.pid)):
            logger.warning(f"Program not running: {name}")
            state.status = ProgramStatus.STOPPED
            return True
        
        state.status = ProgramStatus.STOPPING
        
        # Get pid to terminate: prefer state.process.pid, otherwise use state.pid
        pid = state.process.pid if state.process else state.pid
        logger.info(f"Stopping program: {name} PID={pid}")
        
        try:
            if state.process:
                state.process.terminate()
            else:
                # No process object, sending SIGTERM via pid
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    logger.warning(f"Process no longer exists: {name} PID={pid}")
                    self._cleanup_program_state(name, state)
                    return True
            
            # Wait for process exit (prefer poll, fallback to os.kill polling if no process object)
            for _ in range(timeout):
                if state.process:
                    if state.process.poll() is not None:
                        logger.info(f"Program exited: {name} PID={pid}")
                        self._cleanup_program_state(name, state)
                        return True
                else:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        logger.info(f"Program exited: {name} PID={pid}")
                        self._cleanup_program_state(name, state)
                        return True
                await asyncio.sleep(1)
            
            # Timeout, force kill
            logger.warning(f"Program did not exit within {timeout} seconds, sending SIGKILL: {name}")
            if state.process:
                state.process.kill()
                state.process.wait()
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self._cleanup_program_state(name, state)
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop program: {name} - {e}")
            return False
    
    async def restart_program(self, name: str) -> bool:
        """Restart specified program"""
        logger.info(f"Restarting program: {name}")
        await self.stop_program(name)
        
        # Ensure process truly exits
        state = self.programs.get(name)
        if state and state.process:
            # Extra wait to ensure process fully cleaned up
            for _ in range(5):
                if state.process.poll() is not None:
                    break
                await asyncio.sleep(1)
            else:
                # Process still not exited, force kill
                logger.warning(f"Process not responding, force kill: {name} PID={state.process.pid}")
                try:
                    state.process.kill()
                    state.process.wait()
                except Exception:
                    pass
        
        # Wait for port release
        await asyncio.sleep(2)
        
        return await self.start_program(name)
    
    async def upgrade_program(self, name: str, target_version: str, 
                                binary_data: bytes, config_files: Dict[str, bytes] = None) -> bool:
        """Upgrade specified program (stop old version -> start new version, rollback on failure)
        
        Design notes：
        - For single-instance agents like executor, blue-green deployment/zero downtime is not needed
        - Port remains unchanged (use existing configuration), avoid port drift
        - A few seconds downtime is completely acceptable in agent scenarios
        - Automatically rollback to old version if new version fails to start
        """
        if name not in self.programs:
            logger.error(f"Program not registered: {name}")
            return False
        
        state = self.programs[name]
        config = state.config
        old_version = config.version
        old_process = state.process
        
        logger.info(f"Upgrading program: {name} {old_version} -> {target_version}")
        state.upgrade_in_progress = True
        state.status = ProgramStatus.UPGRADING
        
        try:
            # Install new version (supports tar.gz extraction)
            new_version_dir = self.versions_dir / f"v{target_version}" / name
            binary_path = self._install_binary_data(
                binary_data, new_version_dir, name, config.binary_name
            )
            
            # Copy configuration files
            if config_files:
                for filename, content in config_files.items():
                    config_path = new_version_dir / filename
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(config_path, 'wb') as f:
                        f.write(content)
            
            # Stop old version (release port)
            logger.info(f"Stopping old version: {name} v{old_version}")
            if old_process and old_process.poll() is None:
                try:
                    old_process.terminate()
                    old_process.wait(timeout=10)
                    logger.info(f"Old version exited: {name} v{old_version} PID={old_process.pid}")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Old version did not exit within 10 seconds, sending SIGKILL: {name}")
                    try:
                        old_process.kill()
                        old_process.wait(timeout=5)
                    except Exception as e:
                        logger.error(f"Failed to force kill old version: {e}")
                except Exception as e:
                    logger.error(f"Error stopping old version: {e}")
            
            # Ensure all processes with same name have exited (clean up residuals)
            await self._ensure_no_orphan_processes(name, old_process.pid if old_process else None)
            
            # Update version number (port remains unchanged, avoid port drift)
            config.version = target_version
            
            # Start new version
            logger.info(f"Starting new version: {name} v{target_version}")
            success = await self.start_program(name)
            
            if success:
                # Health check (wait a few seconds then confirm process is still running)
                await asyncio.sleep(3)
                
                if state.status == ProgramStatus.RUNNING:
                    logger.info(f"New version health check passed: {name} v{target_version}")
                    state.upgrade_in_progress = False
                    
                    # Record upgrade history
                    if hasattr(self, 'state_manager'):
                        try:
                            self.state_manager.add_upgrade_history(
                                program_name=name,
                                from_version=old_version,
                                to_version=target_version,
                                status="success"
                            )
                        except Exception:
                            pass
                    
                    logger.info(f"Upgrade successful: {name} {old_version} -> {target_version}")
                    return True
            
            # New version failed to start -> rollback to old version
            logger.error(f"New version failed to start, rolling back to old version: {name} v{old_version}")
            config.version = old_version
            await self.start_program(name)
            
            if hasattr(self, 'state_manager'):
                try:
                    self.state_manager.add_upgrade_history(
                        program_name=name,
                        from_version=old_version,
                        to_version=target_version,
                        status="rollback"
                    )
                except Exception:
                    pass
            
            return False
                
        except Exception as e:
            logger.error(f"Exception upgrading program: {name} - {e}", exc_info=True)
            # Rollback: restore old version number, attempt to start old version
            config.version = old_version
            state.upgrade_in_progress = False
            await self.start_program(name)
            return False
    
    def _create_version_dir(self, version: str, program_name: str) -> Path:
        """Create version directory"""
        version_dir = self.versions_dir / f"v{version}" / program_name
        version_dir.mkdir(parents=True, exist_ok=True)
        return version_dir
    
    def _save_program_state(self, name: str, state: ProgramState):
        """Save program state to state manager (nested structure)
        - env_vars only saves environment variables issued by backend, supervisor does not modify
        - Port information stored independently in port field
        """
        programs = self.state_manager.get('programs', {})
        programs[name] = {
            'version': state.config.version,
            'pid': state.pid,
            'port': state.port,
            'status': state.status.value,
            'auto_start': state.config.auto_start,
            'restart_on_crash': state.config.restart_on_crash,
            'user': state.config.user,
            'group': state.config.group,
            'env_vars': state.config.env_vars,
        }
        self.state_manager.set('programs', programs)
    
    def _cleanup_program_state(self, name: str, state: ProgramState):
        """Clean up program state"""
        state.process = None
        state.pid = None
        state.port = None
        state.status = ProgramStatus.STOPPED
        self._save_program_state(name, state)
    
    @staticmethod
    def _pid_matches_binary(pid: int, binary_name: str) -> bool:
        """Check if process pid was executed from a binary whose basename equals binary_name"""
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                argv0 = f.read().split(b'\x00')[0].decode('utf-8', errors='replace')
            return os.path.basename(argv0) == binary_name
        except (OSError, ValueError):
            return False

    async def _ensure_no_orphan_processes(self, binary_name: str, exclude_pid: Optional[int] = None):
        """Ensure no orphan processes with same name (clean up old version process residuals during upgrade)"""
        try:
            # Use pgrep -x for exact process name match (avoid substring false positives from -f)
            result = subprocess.run(
                ['pgrep', '-x', binary_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                pids = [int(pid.strip()) for pid in result.stdout.strip().split('\n') if pid.strip()]

                for pid in pids:
                    if pid == os.getpid():
                        continue
                    if exclude_pid and pid == exclude_pid:
                        continue
                    # Verify exact binary match via /proc cmdline (argv[0] basename)
                    if not self._pid_matches_binary(pid, binary_name):
                        continue
                    
                    try:
                        # Check if process exists
                        os.kill(pid, 0)
                        logger.warning(f"Found residual process: {binary_name} PID={pid}, sending SIGTERM")
                        os.kill(pid, signal.SIGTERM)
                        
                        # Wait for exit
                        for _ in range(5):
                            try:
                                os.kill(pid, 0)
                                await asyncio.sleep(1)
                            except ProcessLookupError:
                                logger.info(f"Residual process exited: PID={pid}")
                                break
                        else:
                            # Still not exited, force kill
                            logger.warning(f"Residual process not responding, sending SIGKILL: PID={pid}")
                            os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Process no longer exists
        except Exception as e:
            logger.debug(f"Error cleaning up orphan processes: {e}")
    
    def _sync_state_from_file(self, name: str, state):
        """Sync program config from state.json (support external tools like taurus-pm)"""
        try:
            default_base_dir = '/opt/taurus' if os.geteuid() == 0 else str(Path.home() / 'taurus')
            state_file = Path(os.environ.get('BASE_DIR', default_base_dir)) / 'data' / 'state.json'
            if state_file.exists():
                with open(state_file, 'r') as f:
                    state_data = json.load(f)
                
                programs = state_data.get('programs', {})
                if name in programs:
                    pstate = programs[name]
                    restart_on_crash = pstate.get('restart_on_crash', True)
                    auto_start = pstate.get('auto_start', True)
                    
                    if state.config.restart_on_crash != restart_on_crash:
                        logger.debug(f"Config changed via state.json: {name}.restart_on_crash={restart_on_crash}")
                        state.config.restart_on_crash = restart_on_crash
                    
                    if state.config.auto_start != auto_start:
                        logger.debug(f"Config changed via state.json: {name}.auto_start={auto_start}")
                        state.config.auto_start = auto_start
        except Exception as e:
            logger.debug(f"Failed to sync state from file: {e}")
    
    async def check_all_programs_health(self) -> Dict[str, bool]:
        """Check health status of all programs"""
        import os as _os
        results = {}
        for name, state in self.programs.items():
            if state.status == ProgramStatus.RUNNING:
                self._sync_state_from_file(name, state)
                # Check if process is still running
                is_alive = False
                
                if state.process:
                    # Has process object, check directly
                    is_alive = state.process.poll() is None
                elif state.pid:
                    # No process object after state restore, check via PID
                    try:
                        _os.kill(state.pid, 0)
                        is_alive = True
                    except ProcessLookupError:
                        is_alive = False
                    except PermissionError:
                        is_alive = True  # Process exists but no permission, means still running
                
                if not is_alive:
                    # Process exited
                    state.status = ProgramStatus.CRASHED
                    state.last_crash_time = asyncio.get_event_loop().time()
                    results[name] = False
                    logger.warning(f"Program exited unexpectedly: {name} PID={state.pid}")
                    
                    # Auto restart
                    if state.config.restart_on_crash and state.restart_count < state.config.max_restarts:
                        state.restart_count += 1
                        logger.info(f"Auto restarting program: {name} (attempt {state.restart_count})")
                        await asyncio.sleep(2)
                        await self.start_program(name, reset_restart_count=False)
                else:
                    results[name] = True
            else:
                results[name] = (state.status == ProgramStatus.STOPPED and not state.config.auto_start)
        return results
    
    def get_programs_status_for_heartbeat(self) -> List[Dict[str, Any]]:
        """Get all program states for heartbeat reporting"""
        status_list = []
        for name, state in self.programs.items():
            status_list.append({
            'name': name,
            'version': state.config.version,
            'status': state.status.value,
            'pid': state.pid,
            'port': state.port,
            'restart_count': state.restart_count,
            'upgrade_in_progress': state.upgrade_in_progress,
        })
        return status_list
    
    async def _read_program_logs(self, program_name: str, process: subprocess.Popen):
        """Async task to read subprocess stdout/stderr and forward logs"""
        import threading
        
        def read_stream(stream, log_level):
            """Read from a stream and call log callback"""
            try:
                for line in iter(stream.readline, b''):
                    if not line:
                        break
                    try:
                        message = line.decode('utf-8', errors='replace').strip()
                        if message:
                            self.log_callback(
                                program_name=program_name,
                                log_level=log_level,
                                message=message,
                            )
                    except Exception as e:
                        logger.debug(f"Failed to process log line: {e}")
            except Exception as e:
                logger.debug(f"Stream read error: {e}")
            finally:
                stream.close()
        
        stdout_thread = threading.Thread(
            target=read_stream,
            args=(process.stdout, 'INFO'),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(process.stderr, 'ERROR'),
            daemon=True
        )
        
        stdout_thread.start()
        stderr_thread.start()
        
        while process.poll() is None:
            await asyncio.sleep(1)
        
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        
        if stdout_thread.is_alive():
            logger.debug(f"stdout thread still alive after process exit: {program_name}")
        if stderr_thread.is_alive():
            logger.debug(f"stderr thread still alive after process exit: {program_name}")