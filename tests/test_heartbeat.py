"""
Unit tests for taurus_supervisor/heartbeat.py ServerNode / ServerFailoverManager / CommandPersistenceManager

Test contents:
1. ServerNode - Server node status management
2. ServerFailoverManager - Failover management
3. CommandPersistenceManager - Command persistence management
"""
import json
import time
import pytest
from pathlib import Path

from taurus_supervisor.heartbeat import (
    ServerNode,
    ServerFailoverManager,
    CommandPersistenceManager,
    CommandState,
)


class TestServerNode:
    def test_initial_state(self):
        node = ServerNode(url="http://server1:8000", priority=1, is_primary=True)
        assert node.url == "http://server1:8000"
        assert node.priority == 1
        assert node.is_primary is True
        assert node.consecutive_failures == 0
        assert node.is_available is True

    def test_record_success(self):
        node = ServerNode(url="http://server1:8000")
        node.consecutive_failures = 2
        node.record_success()
        assert node.consecutive_failures == 0
        assert node.is_available is True
        assert node.last_success_time > 0

    def test_record_failure_under_threshold(self):
        node = ServerNode(url="http://server1:8000")
        node.record_failure()
        assert node.consecutive_failures == 1
        assert node.is_available is True

    def test_record_failure_exceeds_threshold(self):
        node = ServerNode(url="http://server1:8000")
        for _ in range(3):
            node.record_failure()
        assert node.consecutive_failures == 3
        assert node.is_available is False

    def test_reset(self):
        node = ServerNode(url="http://server1:8000")
        node.consecutive_failures = 5
        node.is_available = False
        node.reset()
        assert node.consecutive_failures == 0
        assert node.is_available is True

    def test_repr(self):
        node = ServerNode(url="http://server1:8000", priority=1)
        repr_str = repr(node)
        assert "server1:8000" in repr_str
        assert "priority=1" in repr_str

    def test_url_trailing_slash_stripped(self):
        node = ServerNode(url="http://server1:8000/")
        assert node.url == "http://server1:8000"


class TestServerFailoverManager:
    def test_load_servers(self):
        servers = [
            {"url": "http://s1:8000", "priority": 2},
            {"url": "http://s2:8000", "priority": 1},
        ]
        manager = ServerFailoverManager(servers)
        assert len(manager.nodes) == 2
        assert manager.nodes[0].priority == 1
        assert manager.nodes[1].priority == 2

    def test_first_node_is_current(self):
        servers = [
            {"url": "http://s1:8000", "priority": 1},
            {"url": "http://s2:8000", "priority": 2},
        ]
        manager = ServerFailoverManager(servers)
        assert manager.current_node.url == "http://s1:8000"

    def test_get_current_server(self):
        servers = [{"url": "http://s1:8000"}]
        manager = ServerFailoverManager(servers)
        assert manager.get_current_server() == "http://s1:8000"

    def test_get_current_server_unavailable(self):
        servers = [{"url": "http://s1:8000"}]
        manager = ServerFailoverManager(servers)
        manager.current_node.is_available = False
        assert manager.get_current_server() is None

    def test_record_success(self):
        servers = [{"url": "http://s1:8000"}]
        manager = ServerFailoverManager(servers)
        manager.current_node.consecutive_failures = 2
        manager.record_success()
        assert manager.current_node.consecutive_failures == 0

    def test_record_failure_switches_to_backup(self):
        servers = [
            {"url": "http://s1:8000", "priority": 1},
            {"url": "http://s2:8000", "priority": 2},
        ]
        manager = ServerFailoverManager(servers)
        for _ in range(3):
            manager.record_failure()
        assert manager.current_node.url == "http://s2:8000"

    def test_record_failure_no_switch_under_threshold(self):
        servers = [
            {"url": "http://s1:8000", "priority": 1},
            {"url": "http://s2:8000", "priority": 2},
        ]
        manager = ServerFailoverManager(servers)
        manager.record_failure()
        manager.record_failure()
        assert manager.current_node.url == "http://s1:8000"

    def test_all_servers_down(self):
        servers = [
            {"url": "http://s1:8000", "priority": 1},
            {"url": "http://s2:8000", "priority": 2},
        ]
        manager = ServerFailoverManager(servers)
        for node in manager.nodes:
            node.is_available = False
        result = manager.record_failure()
        assert result is False

    def test_has_backup(self):
        servers = [
            {"url": "http://s1:8000"},
            {"url": "http://s2:8000"},
        ]
        manager = ServerFailoverManager(servers)
        assert manager.has_backup is True

    def test_no_backup(self):
        servers = [{"url": "http://s1:8000"}]
        manager = ServerFailoverManager(servers)
        assert manager.has_backup is False

    def test_get_all_servers(self):
        servers = [
            {"url": "http://s1:8000", "priority": 1, "is_primary": True},
            {"url": "http://s2:8000", "priority": 2, "is_primary": False},
        ]
        manager = ServerFailoverManager(servers)
        all_servers = manager.get_all_servers()
        assert len(all_servers) == 2
        assert all_servers[0]["is_primary"] is True
        assert all_servers[0]["is_current"] is True

    def test_empty_servers_list(self):
        manager = ServerFailoverManager([])
        assert manager.current_node is None
        assert manager.get_current_server() is None

    def test_skip_empty_url(self):
        servers = [
            {"url": "", "priority": 1},
            {"url": "http://s1:8000", "priority": 2},
        ]
        manager = ServerFailoverManager(servers)
        assert len(manager.nodes) == 1
        assert manager.nodes[0].url == "http://s1:8000"


class TestCommandPersistenceManager:
    def test_add_and_get_commands(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        manager.add_command({"command_id": "cmd-1", "command_type": "install"})
        commands = manager.get_commands()
        assert len(commands) == 1
        assert commands[0]["command_id"] == "cmd-1"

    def test_remove_command(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        cmd = {"command_id": "cmd-1", "command_type": "install"}
        manager.add_command(cmd)
        manager.remove_command(cmd)
        assert len(manager.get_commands()) == 0

    def test_update_command_state(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        cmd = {"command_id": "cmd-1", "command_type": "install"}
        manager.add_command(cmd)
        manager.update_command_state(cmd, CommandState.INSTALLING)
        commands = manager.get_commands()
        assert commands[0]["state"] == CommandState.INSTALLING

    def test_update_command_error(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        cmd = {"command_id": "cmd-1", "command_type": "install"}
        manager.add_command(cmd)
        manager.update_command_error(cmd, "disk full")
        commands = manager.get_commands()
        assert commands[0]["state"] == CommandState.FAILED
        assert commands[0]["last_error"] == "disk full"
        assert commands[0]["retry_count"] == 1

    def test_clear_all(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        manager.add_command({"command_id": "cmd-1"})
        manager.add_command({"command_id": "cmd-2"})
        manager.clear_all()
        assert len(manager.get_commands()) == 0

    def test_pending_count(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        assert manager.pending_count == 0
        manager.add_command({"command_id": "cmd-1"})
        assert manager.pending_count == 1

    def test_persistence_across_instances(self, tmp_path):
        manager1 = CommandPersistenceManager(data_dir=str(tmp_path))
        manager1.add_command({"command_id": "cmd-1", "command_type": "install"})

        manager2 = CommandPersistenceManager(data_dir=str(tmp_path))
        commands = manager2.get_commands()
        assert len(commands) == 1
        assert commands[0]["command_id"] == "cmd-1"

    def test_add_command_sets_defaults(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        manager.add_command({"command_id": "cmd-1"})
        cmd = manager.get_commands()[0]
        assert cmd["retry_count"] == 0
        assert cmd["state"] == CommandState.PENDING
        assert "created_at" in cmd

    def test_remove_by_command_id(self, tmp_path):
        manager = CommandPersistenceManager(data_dir=str(tmp_path))
        manager.add_command({"command_id": "cmd-1", "command_type": "install"})
        manager.add_command({"command_id": "cmd-2", "command_type": "start"})
        manager.remove_command({"command_id": "cmd-1"})
        commands = manager.get_commands()
        assert len(commands) == 1
        assert commands[0]["command_id"] == "cmd-2"

    def test_corrupted_file_handled(self, tmp_path):
        data_dir = tmp_path
        corrupted_file = data_dir / "pending_commands.json"
        corrupted_file.write_text("not valid json {{{")
        manager = CommandPersistenceManager(data_dir=str(data_dir))
        assert manager.get_commands() == []