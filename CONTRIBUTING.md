# Contributing to RelayAid

## Project setup

Requirements: Python 3.11+ (developed on 3.14), Node.js 18+ (developed on 24), Git. Docker is optional.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash: source .venv/Scripts/activate)
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

Run the system:

```bash
python scripts/run_cluster.py --seed      # server + three nodes on 7400-7403
cd frontend && npm run dev                # dashboard on http://127.0.0.1:5173
```

## Repository layout

```
backend/relayaid/     node service (events, projection, sync, security, API)
backend/tests/        pytest suite (in-process four-node cluster)
frontend/src/         React + TypeScript dashboard
scripts/              cluster launcher, seeding, benchmarks, screenshots, PDF build
deploy/               Dockerfiles and nginx config
docs/                 screenshots, measured results, PDFs
```

## Development workflow

1. Branch from `main`: `git checkout -b feat/short-description`.
2. Make a focused change. If it touches merge rules, sync or allocation, add or update a test that compares real replica state (digests), not just HTTP status codes.
3. Run the checks:
   ```bash
   cd backend && ../.venv/Scripts/python -m pytest -q     # all tests must pass
   cd ../frontend && npm run typecheck && npm run build
   ```
4. Commit with a conventional message (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`).
5. Open a pull request describing the behaviour change and how it was verified.

## Code conventions

- **Python:** type hints, small pure functions in `projection.py`, no wall-clock or randomness inside the projection (it must stay deterministic), 120-column lines.
- **Events are forever:** never change the meaning of an existing event type. Add a new type or a new optional field and bump `SCHEMA_VERSION` with a migration plan.
- **Every new event type** needs a Pydantic payload model in `events.py` (`extra="forbid"`, explicit limits) and a projection handler.
- **Local checks must mirror projection validation.** If an operation spends allocation rights, implement the check in `node.py` and the validation in `projection.py`; the randomized convergence test asserts that no locally created event is ever rejected by the projection.
- **Never log or audit sensitive content.** Log ids and event types only. Person fields must be encrypted before an event is built.
- **TypeScript:** strict mode, functional React components, Tailwind utility classes, data fetched only through `src/api.ts`.

## Testing guidelines

- Use the `cluster` fixture (`backend/tests/conftest.py`): four real nodes with separate databases, talking through the signed protocol.
- Use `cluster.partition([...], [...])`, `cluster.heal()`, `cluster.sync(a, b)` and `cluster.digests()`.
- Prefer assertions on converged state and preserved history over assertions on response codes alone.
- Do not report performance numbers that were not produced by `scripts/benchmark.py`.

## Security

- Do not commit `runtime-data/`, `.env` files, keys or databases (all are in `.gitignore`).
- Report vulnerabilities privately to the maintainers rather than in a public issue.

## Contribution guidelines

- Be explicit about guarantees: document what a change guarantees, under which assumptions, and what it does not.
- Keep the dashboard honest: display measured values only.
- Personal study notes belong in `private-notes/`, which is ignored by Git.
