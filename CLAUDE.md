# Questy

@../conventions/universal.md

Local-first import of Quest Diagnostics lab PDFs into SQLite (`questy-import`) and a
read-only FastMCP 4.x server over that database (`questy-mcp`). `README.md` is the
user-facing description; this file carries what a session needs beyond it.

- **Origin is Forgejo only; the repo is not public.** The conventions module is
  imported from the sibling checkout, so no mirror is kept. `ADOPTION.md` has no
  questy column: none of its rules apply to a Python-only repo.
- **Plans** are made in Claude Code plan mode and are not tracked. There is no spec
  directory: `README.md` and the MCP server's instructions and tool docstrings are
  the behaviour documents, and they change in the same commit as the code.
- **Gate**: `githooks/`, activated once per clone with
  `git config core.hooksPath githooks`. pre-commit runs ruff on the staged Python and
  the tests the staged paths select; pre-push runs `ruff check .` and the full suite
  with `--cov-fail-under=80`. The venv is `.venv`
  (`python3 -m venv .venv && .venv/bin/pip install -e '.[mcp,dev]'`). ruff's rule
  families are pinned in `pyproject.toml`.
- **Data**: fixtures are synthetic (`tests/conftest.py`: DOE, JANE and DOE, JOHN,
  invented values). No PDF, database or value from a real report enters the repo;
  `.gitignore` excludes `*.pdf`, `*.db` and `data/`. Test names in docs and tests are
  catalogue spellings, not data.
- **Production** runs on the owner's server from a pull-only checkout, deployed by
  the ops instance, never from here.
- **MCP tool text**: the contract is in `README.md` ("What the model sees") and held by
  `tests/test_mcp_tool_descriptions.py`. On a fastmcp major, re-check the stdio wire
  contract (tools/list, one tools/call per tool, instructions, resources) against a
  synthetic database before and after.
