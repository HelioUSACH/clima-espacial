# Clima Espacial USACH: instructions for Claude

See **`AGENTS.md`** — that is the full contract (read order, hard rules,
module boundary) and applies to every AI session, Claude included.

Claude-specific notes:
- This project's venv lives outside the repo (`~/.venv-cache/clima-espacial`
  in the reference development setup) so `__pycache__`/`.venv` never end up
  in a commit.
- Run `ruff check .` and `pytest` before calling a change done; both are also
  enforced by CI (`.github/workflows/ci.yml`).
