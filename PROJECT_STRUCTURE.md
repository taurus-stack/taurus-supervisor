# Taurus Supervisor - Project Structure

This document describes the file organization of Taurus Supervisor for open-source release.

## Directory Structure

```
taurus-supervisor/
├── taurus_supervisor/          # Main source code package
│   ├── __init__.py            # Package initialization
│   ├── main.py                # Main entry point and TaurusSupervisor class
│   ├── heartbeat.py           # Heartbeat manager and command processing
│   ├── program_manager.py     # General program lifecycle management
│   ├── config_crypto.py       # Configuration encryption/decryption utilities
│   └── interactive_key.py     # Secure key input for configuration decryption
│
├── templates/                  # Deployment configuration templates
│   ├── systemd/               # Systemd service files
│   │   └── taurus-supervisor.service
│   └── supervisor.env.example # Example environment variable configuration
│
├── docs/                       # Documentation
│   ├── communication.md       # Communication protocol (Chinese)
│   └── communication.en.md    # Communication protocol (English)
│
├── scripts/                    # Utility scripts
│   ├── build.sh               # Build script for PyInstaller packaging
│   └── register.sh            # Host registration and auto-installation script
│
├── README.md                   # Project documentation (Chinese)
├── README.en.md               # Project documentation (English)
├── CHANGELOG.md               # Version changelog
├── CONTRIBUTING.md            # Contribution guidelines
├── CODE_OF_CONDUCT.md         # Community code of conduct
├── SECURITY.md                # Security policy and vulnerability reporting
├── LICENSE                    # AGPLv3 License
├── .gitignore                 # Git ignore rules
├── pyproject.toml             # Poetry project configuration and dependencies
├── poetry.lock                # Locked dependency versions
├── taurus-supervisor.spec     # PyInstaller spec file
└── test_program_management.sh # Integration test script
```

## Key Components

### Source Code (`taurus_supervisor/`)

| File | Description |
|------|-------------|
| `main.py` | Main entry point, contains `TaurusSupervisor` class that orchestrates all components |
| `heartbeat.py` | `SupervisorHeartbeatManager` class for server communication, command processing, and failover |
| `program_manager.py` | `ProgramManager` class for managing arbitrary program lifecycles |
| `config_crypto.py` | Fernet encryption/decryption utilities for sensitive configuration |
| `interactive_key.py` | Secure interactive key input using `getpass` |

### Templates (`templates/`)

| File | Description |
|------|-------------|
| `systemd/taurus-supervisor.service` | Systemd service unit file for Linux deployment |
| `supervisor.env.example` | Example environment variable configuration template |

### Documentation (`docs/`)

| File | Description |
|------|-------------|
| `communication.md` | Server-Supervisor communication protocol (Chinese) |
| `communication.en.md` | Server-Supervisor communication protocol (English) |

### Scripts (`scripts/`)

| File | Description |
|------|-------------|
| `build.sh` | Automated build script using PyInstaller |
| `register.sh` | Host registration script with auto-installation support |

### Root Files

| File | Description |
|------|-------------|
| `README.md` | Main project documentation (Chinese) |
| `README.en.md` | Main project documentation (English) |
| `CHANGELOG.md` | Version history and notable changes |
| `CONTRIBUTING.md` | Guidelines for contributing to the project |
| `CODE_OF_CONDUCT.md` | Community behavior standards |
| `SECURITY.md` | Security policy and vulnerability reporting process |
| `LICENSE` | AGPLv3 License text |
| `.gitignore` | Git ignore patterns |
| `pyproject.toml` | Poetry configuration, dependencies, and build system |
| `poetry.lock` | Locked dependency versions for reproducible builds |
| `taurus-supervisor.spec` | PyInstaller specification file |
| `test_program_management.sh` | Integration test script for program management |

## Open-Source Readiness Checklist

- [x] All code comments translated to English
- [x] English README created (README.en.md)
- [x] Chinese README maintained (README.md)
- [x] LICENSE file added (AGPLv3 License)
- [x] CONTRIBUTING.md created
- [x] CODE_OF_CONDUCT.md created
- [x] SECURITY.md created
- [x] CHANGELOG.md created
- [x] Email updated to taurus-stackoutlook.com
- [x] .gitignore optimized
- [x] Documentation bilingual (Chinese + English)
- [x] File structure optimized for open-source

## Next Steps for Open-Source Release

1. Create GitHub repository
2. Add repository topics and description
3. Configure GitHub Actions for CI/CD
4. Set up issue templates
5. Configure branch protection rules
6. Add repository maintainers
7. Publish initial release tag (v1.0.0)