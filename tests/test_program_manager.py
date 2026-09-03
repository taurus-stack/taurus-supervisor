"""
Unit tests for taurus_supervisor/program_manager.py ProgramManager

Test contents:
1. ProgramConfig / ProgramState data structures
2. ProgramManager.register_program - Register a program
3. ProgramManager._is_tar_gz - Detect tar.gz format
4. ProgramManager._check_pid_alive - Check if process is alive
5. ProgramManager._detect_port - Detect port
6. ProgramManager.get_program / get_all_programs / get_running_programs
"""
import gzip
import io
import os
import tarfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from taurus_supervisor.program_manager import (
    ProgramConfig,
    ProgramState,
    ProgramStatus,
    ProgramManager,
)


class TestProgramConfig:
    def test_defaults(self):
        config = ProgramConfig(name="test", binary_name="test-bin", version="1.0")
        assert config.auto_start is True
        assert config.restart_on_crash is True
        assert config.max_restarts == 5
        assert config.health_check_path is None
        assert config.user is None
        assert config.group is None
        assert config.env_vars == {}


class TestProgramStatus:
    def test_values(self):
        assert ProgramStatus.STOPPED == "stopped"
        assert ProgramStatus.STARTING == "starting"
        assert ProgramStatus.RUNNING == "running"
        assert ProgramStatus.STOPPING == "stopping"
        assert ProgramStatus.CRASHED == "crashed"
        assert ProgramStatus.UPGRADING == "upgrading"


class TestProgramState:
    def test_defaults(self):
        config = ProgramConfig(name="test", binary_name="test-bin", version="1.0")
        state = ProgramState(config=config)
        assert state.status == ProgramStatus.STOPPED
        assert state.pid is None
        assert state.port is None
        assert state.process is None
        assert state.restart_count == 0


class TestProgramManagerIsTarGz:
    def test_gzip_magic_bytes(self):
        data = b"\x1f\x8b" + b"\x00" * 10
        assert ProgramManager._is_tar_gz(data) is True

    def test_non_gzip_bytes(self):
        data = b"\x00\x00" + b"\x00" * 10
        assert ProgramManager._is_tar_gz(data) is False

    def test_too_short_data(self):
        assert ProgramManager._is_tar_gz(b"\x1f") is False

    def test_empty_data(self):
        assert ProgramManager._is_tar_gz(b"") is False


class TestProgramManagerCheckPidAlive:
    def test_alive_process(self):
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            assert ProgramManager._check_pid_alive(os.getpid()) is True

    def test_dead_process(self):
        with patch("os.kill", side_effect=OSError):
            assert ProgramManager._check_pid_alive(999999) is False


class TestProgramManagerDetectPort:
    def test_grpc_port(self):
        env = {"GRPC_PORT": "50051"}
        assert ProgramManager._detect_port(env) == 50051

    def test_port(self):
        env = {"PORT": "8080"}
        assert ProgramManager._detect_port(env) == 8080

    def test_http_port(self):
        env = {"HTTP_PORT": "3000"}
        assert ProgramManager._detect_port(env) == 3000

    def test_grpc_port_priority_over_port(self):
        env = {"GRPC_PORT": "50051", "PORT": "8080"}
        assert ProgramManager._detect_port(env) == 50051

    def test_no_port_found(self):
        env = {"OTHER_VAR": "value"}
        assert ProgramManager._detect_port(env) is None

    def test_empty_env(self):
        assert ProgramManager._detect_port({}) is None

    def test_invalid_port_value(self):
        env = {"PORT": "not-a-number"}
        assert ProgramManager._detect_port(env) is None

    def test_port_out_of_range(self):
        env = {"PORT": "99999"}
        assert ProgramManager._detect_port(env) is None

    def test_port_zero(self):
        env = {"PORT": "0"}
        assert ProgramManager._detect_port(env) is None


class TestProgramManagerRegister:
    def setup_method(self):
        self.tmp_dir = Path("/tmp/test_pm")
        self.mock_state_manager = MagicMock()
        self.pm = ProgramManager(
            base_dir=self.tmp_dir,
            state_manager=self.mock_state_manager,
        )

    def test_register_program(self):
        config = ProgramConfig(name="test-prog", binary_name="test-bin", version="1.0")
        self.pm.register_program(config)
        assert "test-prog" in self.pm.programs
        state = self.pm.programs["test-prog"]
        assert state.config.version == "1.0"
        assert state.status == ProgramStatus.STOPPED

    def test_get_program(self):
        config = ProgramConfig(name="test-prog", binary_name="test-bin", version="1.0")
        self.pm.register_program(config)
        state = self.pm.get_program("test-prog")
        assert state is not None
        assert state.config.name == "test-prog"

    def test_get_nonexistent_program(self):
        assert self.pm.get_program("nonexistent") is None

    def test_get_all_programs(self):
        config1 = ProgramConfig(name="prog1", binary_name="bin1", version="1.0")
        config2 = ProgramConfig(name="prog2", binary_name="bin2", version="2.0")
        self.pm.register_program(config1)
        self.pm.register_program(config2)
        all_progs = self.pm.get_all_programs()
        assert len(all_progs) == 2

    def test_get_running_programs(self):
        config1 = ProgramConfig(name="prog1", binary_name="bin1", version="1.0")
        config2 = ProgramConfig(name="prog2", binary_name="bin2", version="2.0")
        self.pm.register_program(config1)
        self.pm.register_program(config2)
        self.pm.programs["prog1"].status = ProgramStatus.RUNNING
        running = self.pm.get_running_programs()
        assert len(running) == 1
        assert "prog1" in running


class TestProgramManagerInstallBinaryData:
    def setup_method(self):
        self.tmp_dir = Path("/tmp/test_pm_install")
        self.mock_state_manager = MagicMock()
        self.pm = ProgramManager(
            base_dir=self.tmp_dir,
            state_manager=self.mock_state_manager,
        )

    def _create_tar_gz(self, files: dict) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, content in files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_install_plain_binary(self, tmp_path):
        version_dir = tmp_path / "v1.0" / "test-prog"
        binary_data = b"\x7fELF" + b"\x00" * 100
        result = self.pm._install_binary_data(
            binary_data, version_dir, "test-prog", "test-bin"
        )
        assert result.exists()
        assert result.name == "test-bin"
        assert os.access(str(result), os.X_OK)

    def test_install_tar_gz(self, tmp_path):
        tar_data = self._create_tar_gz({"test-bin": "#!/bin/bash\necho hello"})
        version_dir = tmp_path / "v1.0" / "test-prog"
        result = self.pm._install_binary_data(
            tar_data, version_dir, "test-prog", "test-bin"
        )
        assert result.exists()
        assert result.name == "test-bin"
        assert os.access(str(result), os.X_OK)

    def test_install_tar_gz_binary_in_subdir(self, tmp_path):
        tar_data = self._create_tar_gz({"subdir/test-bin": "#!/bin/bash\necho hello"})
        version_dir = tmp_path / "v1.0" / "test-prog"
        result = self.pm._install_binary_data(
            tar_data, version_dir, "test-prog", "test-bin"
        )
        assert result.exists()
        assert result.name == "test-bin"

    def test_install_tar_gz_missing_binary_raises(self, tmp_path):
        tar_data = self._create_tar_gz({"other-file": "content"})
        version_dir = tmp_path / "v1.0" / "test-prog"
        with pytest.raises(RuntimeError, match="Cannot find executable"):
            self.pm._install_binary_data(
                tar_data, version_dir, "test-prog", "test-bin"
            )