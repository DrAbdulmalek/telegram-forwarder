# Contributing to telegram-forwarder

Thank you for your interest in contributing to **telegram-forwarder**! This guide will help you get started.

## Getting Started

### Prerequisites
- Python 3.10+
- Git
- A GitHub account

### Setup
```bash
git clone https://github.com/DrAbdulmalek/telegram-forwarder.git
cd telegram-forwarder
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## How to Contribute

### Reporting Bugs
1. Check if the bug has already been reported in [Issues](https://github.com/DrAbdulmalek/telegram-forwarder/issues)
2. If not, open a new issue using the **Bug Report** template
3. Include: OS, Python version, steps to reproduce, expected vs actual behavior

### Suggesting Features
1. Open an issue using the **Feature Request** template
2. Describe the use case and expected behavior
3. Include examples if possible

### Submitting Changes
1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Run linting: `flake8 .` and `bandit -r .`
5. Run tests: `pytest` or `python -m unittest discover`
6. Commit with clear messages: `git commit -m "feat: add description"`
7. Push to your fork: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Commit Message Convention
We follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `style:` Formatting, no code change
- `refactor:` Code restructuring
- `test:` Adding/updating tests
- `chore:` Maintenance tasks

## Code Style
- Follow PEP 8
- Line length: 120 characters
- Use type hints where possible
- Add docstrings to public functions/classes
- Run `flake8 .` before committing

## Pull Request Process
1. Ensure all CI checks pass
2. Update documentation if needed
3. Keep PRs focused on a single concern
4. Respond to review feedback promptly

## Questions?
Feel free to open a [Discussion](https://github.com/DrAbdulmalek/telegram-forwarder/discussions) or reach out via Issues.
