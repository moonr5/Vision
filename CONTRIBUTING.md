# Contributing to SGU Logistics & Telemetry

Thank you for your interest in contributing. This document outlines the
process for proposing changes to the project.

## Getting Started

1. Fork the repository and clone it locally.
2. Install dependencies: `npm install`
3. Run the test suite to confirm everything passes: `npm test`
4. Create a branch for your work: `git checkout -b your-feature`

## Development Workflow

### Code Style

This project uses ESLint and Prettier for consistent formatting:

```bash
npm run lint          # Check for linting errors
npm run lint:fix      # Auto-fix linting issues
npm run format        # Format all files with Prettier
npm run format:check  # Verify formatting without changes
```

### Running Tests

```bash
npm test              # Full test suite (requires --runInBand)
npm run test:server   # Server integration tests only
npm run test:database # Database module tests only
```

Tests run sequentially to avoid port conflicts. The database tests use
`fake-indexeddb` and `jsdom` for browser API simulation.

### Commit Messages

Follow conventional commit format:

```
type(scope): short description

- Bullet points for details if needed
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `style`, `chore`

## Project Architecture

Before contributing, review the documentation:

- [README.md](README.md) — System overview and architecture diagrams
- [FULL_SYSTEM_DECOMPOSITION.md](FULL_SYSTEM_DECOMPOSITION.md) — Detailed layer breakdown
- [database/README.md](database/README.md) — Database architecture and API

Key architectural principle: **graceful degradation**. Every service
dependency is optional. New features must not break the system when
downstream services (Scale Engine, Route Engine, AI Backend) are offline.

## Pull Request Process

1. Ensure tests pass and new features include test coverage.
2. Update relevant documentation if your change affects APIs or architecture.
3. Keep PRs focused — one feature or fix per pull request.
4. Rebase onto `main` before submitting.

## Adding a New Scale Engine

The 28-engine architecture supports adding new engines without modifying
existing code:

1. Create a new Python file in the appropriate `scale_engine/` subdirectory
2. The engine is auto-discovered and registered by the FastAPI app
3. Follow the existing engine pattern with a class-based interface

## License

By contributing, you agree that your contributions will be licensed under
the GNU Lesser General Public License v3.0 (LGPLv3).

See [LICENSE](LICENSE) and [COPYRIGHT](COPYRIGHT) for full terms.
