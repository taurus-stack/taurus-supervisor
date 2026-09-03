#!/usr/bin/env python3
import argparse
import asyncio
import logging
import sys
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional


class StateManager:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load state: {e}")
        return {
            'current_version': None,
            'previous_version': None,
            'upgrade_in_progress': False,
            'upgrade_target_version': None,
            'last_successful_version': None,
            'upgrade_history': [],
            'programs': {},
        }
    
    def load_state(self):
        self._state = self._load_state()
    
    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            print(f"Failed to save state: {e}")
    
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


taurus_supervisor_path = Path(__file__).parent.parent / 'taurus_supervisor'

if taurus_supervisor_path.exists():
    sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from taurus_supervisor.program_manager import ProgramManager, ProgramConfig, ProgramStatus, ProgramState
except ImportError:
    for path in sys.path:
        supervisor_path = Path(path) / 'taurus_supervisor'
        if supervisor_path.exists():
            sys.path.insert(0, path)
            from taurus_supervisor.program_manager import ProgramManager, ProgramConfig, ProgramStatus, ProgramState
            break
    else:
        raise ImportError("taurus_supervisor module not found")


def find_supervisor_config(config_path: str = None, data_dir: str = None) -> dict:
    is_root = os.geteuid() == 0
    
    if is_root:
        default_config_paths = [
            Path('/etc/taurus-supervisor/config.yaml'),
            Path('/etc/taurus-supervisor/config.json'),
        ]
        default_base_dir = '/opt/taurus'
    else:
        home = Path.home()
        default_config_paths = [
            home / '.config' / 'taurus-supervisor' / 'config.yaml',
            home / '.config' / 'taurus-supervisor' / 'config.json',
            home / '.taurus-supervisor' / 'config.yaml',
            home / '.taurus-supervisor' / 'config.json',
        ]
        default_base_dir = str(home / 'taurus')
    
    config = {}
    
    if config_path:
        config_file = Path(config_path).expanduser()
        if config_file.exists():
            if config_file.suffix == '.json':
                with open(config_file, 'r') as f:
                    config = json.load(f) or {}
            else:
                import yaml
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f) or {}
            config.setdefault('base_dir', default_base_dir)
        else:
            print(f"Warning: Config file not found: {config_file}")
            config = {'base_dir': default_base_dir}
    else:
        for config_file in default_config_paths:
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
    
    if not config:
        config = {
            'base_dir': default_base_dir,
        }
    
    if data_dir:
        config['base_dir'] = data_dir
    else:
        config['base_dir'] = os.environ.get('BASE_DIR', config.get('base_dir'))
    
    return config


def create_program_manager(config_path: str = None, data_dir: str = None):
    config = find_supervisor_config(config_path, data_dir)
    base_dir = Path(config['base_dir']).expanduser()
    data_dir_path = base_dir / 'data'
    
    state_manager = StateManager(data_dir_path / 'state.json')
    state_manager.load_state()
    
    pm = ProgramManager(base_dir, state_manager)
    
    programs_data = state_manager.get('programs', {})
    for name, prog_data in programs_data.items():
        env_vars = prog_data.get('env_vars', {})
        config_data = env_vars.get('config', {})
        
        program_config = ProgramConfig(
            name=name,
            binary_name=prog_data.get('binary_name', name),
            version=prog_data.get('version', 'unknown'),
            env_vars=env_vars,
            auto_start=prog_data.get('auto_start', True),
            restart_on_crash=prog_data.get('restart_on_crash', True),
            max_restarts=prog_data.get('max_restarts', 5),
            health_check_path=config_data.get('health_check_path'),
            user=prog_data.get('user'),
            group=prog_data.get('group'),
        )
        
        state = ProgramState(
            config=program_config,
            status=ProgramStatus(prog_data.get('status', 'stopped')),
            pid=prog_data.get('pid'),
            restart_count=prog_data.get('restart_count', 0),
        )
        pm.programs[name] = state
    
    return pm, base_dir


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


async def cmd_start(args):
    pm, base_dir = create_program_manager(args.config, args.data_dir)
    state_file = base_dir / 'data' / 'state.json'
    
    if args.program:
        if args.program not in pm.programs:
            print(f"Program not found: {args.program}")
            return
        
        enable_restart_on_crash(state_file, args.program)
        
        success = await pm.start_program(args.program)
        print(f"Started {args.program}: {'OK' if success else 'FAILED'}")
    else:
        for name in pm.programs:
            enable_restart_on_crash(state_file, name)
            
            success = await pm.start_program(name)
            print(f"Started {name}: {'OK' if success else 'FAILED'}")
        print("Started all programs")


def enable_restart_on_crash(state_file: Path, program_name: str):
    if not state_file.exists():
        return
    
    with open(state_file, 'r') as f:
        state = json.load(f)
    
    if 'programs' in state and program_name in state['programs']:
        state['programs'][program_name]['restart_on_crash'] = True
        
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)


async def cmd_stop(args):
    pm, base_dir = create_program_manager(args.config, args.data_dir)
    state_file = base_dir / 'data' / 'state.json'
    
    if args.program:
        if args.program not in pm.programs:
            print(f"Program not found: {args.program}")
            return
        
        disable_restart_on_crash(state_file, args.program)
        
        success = await pm.stop_program(args.program)
        print(f"Stopped {args.program}: {'OK' if success else 'FAILED'}")
    else:
        for name in pm.programs:
            disable_restart_on_crash(state_file, name)
            
            success = await pm.stop_program(name)
            print(f"Stopped {name}: {'OK' if success else 'FAILED'}")
        print("Stopped all programs")


def disable_restart_on_crash(state_file: Path, program_name: str):
    if not state_file.exists():
        return
    
    with open(state_file, 'r') as f:
        state = json.load(f)
    
    if 'programs' in state and program_name in state['programs']:
        state['programs'][program_name]['restart_on_crash'] = False
        
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)


async def cmd_restart(args):
    pm, base_dir = create_program_manager(args.config, args.data_dir)
    
    if args.program:
        if args.program not in pm.programs:
            print(f"Program not found: {args.program}")
            return
        
        success = await pm.restart_program(args.program)
        print(f"Restarted {args.program}: {'OK' if success else 'FAILED'}")
    else:
        for name in pm.programs:
            success = await pm.restart_program(name)
            print(f"Restarted {name}: {'OK' if success else 'FAILED'}")


async def cmd_status(args):
    pm, base_dir = create_program_manager(args.config, args.data_dir)
    await pm.check_all_programs_health()
    
    if args.program:
        if args.program not in pm.programs:
            print(f"Program not found: {args.program}")
            return
        
        state = pm.get_program_status(args.program)
        if state:
            print(f"{args.program}:")
            print(f"  Status: {state.status.value}")
            print(f"  PID: {state.pid or 'N/A'}")
            print(f"  Version: {state.config.version}")
            print(f"  Restart count: {state.restart_count}")
        else:
            print(f"Program not found: {args.program}")
    else:
        programs = pm.get_all_programs()
        if not programs:
            print("No programs found.")
            print(f"State file: {base_dir / 'data' / 'state.json'}")
            return
        
        print(f"{'Program':<25} {'Status':<10} {'PID':<8} {'Version':<15} {'Restarts':<8}")
        print("-" * 75)
        for name, state in sorted(programs.items()):
            print(f"{name:<25} {state.status.value:<10} {str(state.pid or '-'):<8} {state.config.version:<15} {state.restart_count:<8}")


async def cmd_list(args):
    pm, base_dir = create_program_manager(args.config, args.data_dir)
    await pm.check_all_programs_health()
    programs = pm.get_all_programs()
    
    if not programs:
        print("No programs found.")
        print(f"State file: {base_dir / 'data' / 'state.json'}")
        return
    
    print(f"{'Program':<25} {'Binary':<35} {'Version':<15} {'Auto Start':<10}")
    print("-" * 90)
    for name, state in sorted(programs.items()):
        auto_start = 'Yes' if state.config.auto_start else 'No'
        binary_path = base_dir / 'versions' / state.config.version / name / state.config.binary_name
        print(f"{name:<25} {str(binary_path):<35} {state.config.version:<15} {auto_start:<10}")


def main():
    parser = argparse.ArgumentParser(
        prog='taurus-pm',
        description='Taurus Process Manager - CLI tool for managing taurus-supervisor programs'
    )
    parser.add_argument('-c', '--config', help='Path to taurus-supervisor config file')
    parser.add_argument('-d', '--data-dir', help='Data directory (base_dir) for programs')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    subparsers.add_parser('list', help='List all registered programs')
    
    start_parser = subparsers.add_parser('start', help='Start programs')
    start_parser.add_argument('program', nargs='?', help='Program name to start (if not specified, start all)')
    
    stop_parser = subparsers.add_parser('stop', help='Stop programs')
    stop_parser.add_argument('program', nargs='?', help='Program name to stop (if not specified, stop all)')
    
    restart_parser = subparsers.add_parser('restart', help='Restart programs')
    restart_parser.add_argument('program', nargs='?', help='Program name to restart (if not specified, restart all)')
    
    status_parser = subparsers.add_parser('status', help='Show program status')
    status_parser.add_argument('program', nargs='?', help='Program name to check (if not specified, show all)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    setup_logging(args.verbose)
    
    try:
        asyncio.run({
            'start': cmd_start,
            'stop': cmd_stop,
            'restart': cmd_restart,
            'status': cmd_status,
            'list': cmd_list,
        }[args.command](args))
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)


if __name__ == '__main__':
    main()
