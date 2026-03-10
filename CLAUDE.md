# RevisionAid — Claude Code Context

## Project
GCSE revision tool. FastAPI backend + Alpine.js v3 SPA frontend + SQLite.
No build step — all JS/CSS loaded from CDN, pages served as static HTML fragments.

## Dev Server
```
python run.py          # FastAPI on http://localhost:8000, hot-reload enabled
```
Preview server in Claude Code is already configured (port 8001 proxy).

## Architecture
- `backend/routers/` — FastAPI routers, one file per resource
- `backend/services/claude_service.py` — all Anthropic API calls; `EXTRACTION_MODEL`, `QUIZ_MODEL`, `FACT_CHECK_MODEL` are the defaults
- `backend/prompts/` — each prompt is a module-level string constant
- `frontend/pages/` — HTML fragments loaded by `frontend/js/router.js` hash-router
- `data/revisionaid.db` — SQLite; never commit this file
- DB migrations: add columns via `_add_column_if_missing()` helper at bottom of `init_db()` in `database.py`

## AI Settings (DB-driven)
All models and prompts are overridable at runtime. Defaults live in `claude_service.py`; overrides are stored in the `settings` table and read via `_get_ai_setting(key)`. Admin panel at `/admin` → AI Settings tab.
Card labels come from `_AI_SETTING_METADATA` dict in `backend/routers/admin.py`.

## Release Process
1. Commit to `main` → GitHub Actions runs tests + builds `:latest` Docker image automatically
2. For a versioned release: `git tag -a v1.x.0 -m "..."` then `git push origin v1.x.0`
   - Tag **must** be three-part semver `v*.*.*` to match the `publish.yml` trigger
   - `v1.1` (two-part) will NOT trigger the workflow — always use `v1.1.0`
3. GitHub Actions (`publish.yml`) builds multi-platform image (amd64 + arm64) and pushes to `ghcr.io/jarrah31/ai-revision-aid`
4. **Never build Docker images locally** — Actions handles it; monitor with `gh run list`
5. **Always update `APP_VERSION` in `backend/app.py`** to match the new tag before committing and tagging — this is what the home page displays.

## Git Remote
GitHub: `https://github.com/jarrah31/AI-Revision-Aid.git` (not Bitbucket)

## Alpine.js Gotchas
- **`<select>` + `x-for` options**: `:value` on the `<select>` doesn't work when options are rendered by a nested `x-for` (options don't exist yet at evaluation time). Fix: use `:selected="edit === m"` on each `<option>` instead.
- **`:disabled` on reactive objects**: accessing a missing key on an Alpine reactive proxy inside `x-for` can return a truthy proxy instead of `undefined`. Always wrap in a method with explicit `!!` coercion rather than using the raw property access in the template.

## Testing
```
pytest tests/ --tb=short -q       # full suite
pytest tests/test_admin.py -q     # single file
```
Tests use an in-memory SQLite DB (see `tests/conftest.py`). Requires `ANTHROPIC_API_KEY=""` and `JWT_SECRET` env vars (handled by conftest).
