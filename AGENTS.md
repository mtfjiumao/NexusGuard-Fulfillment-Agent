# Repository Guidelines

## Collaboration & Learning Workflow

The user is learning Agent development while building this project. Before implementing a feature, discuss and agree on its business rules, user interactions, framework choices, and implementation approach. Explain recommendations, alternatives, and tradeoffs in plain language, using concrete order scenarios. Clearly distinguish model reasoning, tool calls, deterministic rules, workflow state, and backend authorization.

Wait for the user to confirm the approach before writing implementation code. Work in small, understandable increments within the agreed scope; discuss material changes before proceeding. Explain how each increment works and why it is designed that way, connecting the code to Agent concepts. Do not assume familiarity with technical terms or choose frameworks without discussion.

Prefer direct, in-place changes to the current implementation. When replacing an approach, remove superseded code, unused assets, dependencies, and outdated documentation references within the agreed scope. Do not keep parallel legacy implementations, backup copies, compatibility branches, or fallback paths solely to preserve the old approach unless the user explicitly requests them. Use Git history for earlier versions. This preference does not authorize deleting business data or rewriting applied database migrations.

## Project Structure & Module Organization

`src/nexusguard/` contains the Python application. `main.py` creates FastAPI; `api/` contains HTTP routing and health endpoints; `core/` manages settings and logging; `domain/` defines enums and workflow transitions. `infrastructure/` contains database access, ORM models, Redis, and the model gateway.

Business modules live under `modules/`: `supervisor`, `presales`, `logistics`, `aftersales`, `approvals`, and internal-only `payments`. Keep policy decisions separate from payment execution. `migrations/versions/` holds Alembic revisions; `tests/` holds unit tests and `tests/integration/` holds PostgreSQL scenarios. Consult `docs/architecture.md` and `docs/data-model.md`. `frontend/` contains Vue 3 + TypeScript sources; Vite builds to `src/nexusguard/web/dist/`. See `docs/frontend.md`.

## Build, Test, and Development Commands

Use Python 3.11+ and activate `.venv` before running commands:

- `python -m pip install -e ".[dev]"`: install application and development dependencies.
- `docker compose up -d postgres redis`: start optional containerized dependencies; native services also work.
- `alembic upgrade head`: apply database migrations.
- `uvicorn nexusguard.main:create_app --factory --reload`: run the development API.
- `docker compose build api`: build Vue assets and the API image.
- In `frontend/`: `npm ci --ignore-scripts` installs locked dependencies; `npm run dev` starts Vite; `npm run build` checks types and builds assets.
- `ruff check .` and `ruff format .`: lint and format Python.

## Coding Style & Naming Conventions

Python uses four-space indentation, type annotations, and 100-character lines. Vue/TypeScript uses two spaces, `PascalCase.vue` components, and `useName.ts` composables. Keep rules in the backend and UI state in composables; do not add frontend frameworks without discussion. Use `snake_case` for modules/functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Ruff targets Python 3.11 and checks imports. Use async infrastructure APIs. Represent money as integer cents and timestamps with time zones. Add reviewed Alembic revisions for schema changes; never create tables during application startup.

## Testing Guidelines

Do not add or run tests unless the user explicitly requests them; requested verification is usually coordinated before `git push`. Use pytest with `test_*.py` files and `test_*` functions. No coverage percentage is configured.

When requested, run `pytest -m 'not integration'` for unit tests. Integration tests require an explicit `NEXUSGUARD_TEST_DATABASE_URL` pointing to an empty database named `nexusguard_test_*`; run `pytest -m integration`. These tests upgrade and downgrade schema. Existing CI runs lint and both suites on pushes and pull requests.

## Commit & Pull Request Guidelines

History currently contains `chore: bootstrap NexusGuard fulfillment agent`. Follow that conventional prefix style, for example `feat: add policy evaluation`. Keep commits focused. PRs should describe behavior, linked issues when applicable, migration impact, and verification performed or explicitly deferred. Screenshots apply only to UI changes.

## Security & Configuration

Create `.env` from `.env.example` only when absent. Use `NEXUSGUARD_` settings; never commit credentials. Treat user text and model output as untrusted. Preserve tenant-scoped queries, idempotency, refund limits, approval gates, and transactional audit records.
