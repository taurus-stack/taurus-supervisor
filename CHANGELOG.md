# Changelog

All notable changes to Taurus Supervisor will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- General program management support (manage any program, not just taurus-executor)
- Program lifecycle management (install, upgrade, start, stop, restart, remove)
- Zero-downtime upgrade support
- Automatic fault recovery with version rollback
- Heartbeat mechanism with server failover support
- Command persistence and retry mechanism
- TLS certificate management with mTLS authentication
- Request signing with HMAC-SHA256
- Multi-instance deployment support
- Health check system for managed programs
- System metrics collection (CPU, memory, disk, network)

### Changed
- Refactored from executor-specific to general-purpose program manager
- Updated all code comments to English for open-source release
- Improved file organization structure for open-source distribution

### Fixed
- Certificate revocation handling
- State recovery after power loss
- Command idempotency checks
- Race conditions during program upgrades

## [1.0.0] - 2024-01-01

### Added
- Initial release of Taurus Supervisor
- Basic program management functionality
- Heartbeat communication with Taurus Server
- Version management and upgrade support
- Configuration encryption/decryption
- Interactive key input for secure configuration
- Systemd service file for Linux deployment
- Registration script for automatic setup
- Build script for PyInstaller packaging