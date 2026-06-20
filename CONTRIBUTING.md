# Contributing to open-compute

Thank you for your interest in contributing!

## Getting started

```bash
git clone https://github.com/ellmos-ai/open-compute.git
cd open-compute
pip install -e ".[dev]"
python -X utf8 -m pytest -q
```

All 354 tests must pass before opening a pull request.

## Submitting changes

- Open an issue first for larger changes.
- Keep pull requests focused — one feature or fix per PR.
- Add tests for new functionality (mock-only; no live OS calls in CI).
- Ensure `python -X utf8 -m pytest -q` exits green.

## Code style

- Python 3.10+ compatible.
- No runtime dependencies in the core (`open_compute/`) — extras only.
- Lazy imports for vendor SDKs (`anthropic`, `openai`, `mss`, etc.).

## Alpha notice

open-compute is in **alpha**. APIs may change between minor versions.
Breaking changes are documented in [CHANGELOG.md](CHANGELOG.md).

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
