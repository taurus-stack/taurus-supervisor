# Contributing to Taurus Supervisor

Thank you for your interest in contributing to Taurus Supervisor! This document provides guidelines for contributing to this project.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and professional environment.

## How to Contribute

### Reporting Bugs

Before creating bug reports, please check existing issues. When creating a bug report, include:

- **Clear title and description**
- **Steps to reproduce** the behavior
- **Expected vs actual behavior**
- **Environment details** (OS, Python version, etc.)
- **Logs or error messages** if applicable

### Suggesting Enhancements

Enhancement suggestions are welcome! Please include:

- **Use case** - why this enhancement would be useful
- **Proposed solution** - how you think it should work
- **Alternative solutions** you've considered

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add or update tests as appropriate
5. Ensure all tests pass
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to the branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

## Development Setup

### Prerequisites

- Python 3.12+
- Poetry (dependency management)

### Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/taurus-supervisor.git
cd taurus-supervisor

# Install dependencies
poetry install

# Install dev dependencies
poetry install --with dev
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=taurus_supervisor
```

### Code Style

We use Black for code formatting and isort for import sorting:

```bash
# Format code
black taurus_supervisor/

# Sort imports
isort taurus_supervisor/
```

## Commit Messages

Follow conventional commits:

- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

Example: `feat: add support for custom health check endpoints`

## Branch Naming

- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring

## Questions?

If you have questions, please open an issue with the `question` label.

## License

By contributing, you agree that your contributions will be licensed under the GNU Affero General Public License v3.0.