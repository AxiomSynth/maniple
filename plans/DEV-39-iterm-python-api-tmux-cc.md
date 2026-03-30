# Plan: Migrate iTerm window management to Python API + tmux -CC

**Issue:** DEV-39
**Date:** 2026-03-29
**Status:** Spike Complete — Ready for Implementation

## Approach

Replace TmuxBackend's AppleScript window management (`_open_iterm_for_session`, `_find_iterm_window_with_session`) with a new `ItermManager` module that uses the iTerm2 Python API + `tmux -CC` control mode. This eliminates the double-scrollback problem (tmux TUI + iTerm scrollback collision) and the AppleScript fragility (case-sensitive title search, focus stealing, no event monitoring). The migration preserves all tmux server-side operations unchanged — only the client display layer changes.

After the core migration, remove the now-redundant `ItermBackend` and `iterm_utils` module entirely, consolidating all iTerm interaction into `ItermManager`.

## Key Decisions

- [LOCKED] **New iterm_manager module**: Create `src/maniple_mcp/iterm_manager.py` with lazy-init connection management. TmuxBackend instantiates `ItermManager` and delegates window/tab operations to it. Clean separation: tmux server ops stay in tmux.py, iTerm display ops go to iterm_manager.
- [LOCKED] **Remove ItermBackend**: After migration, remove `ItermBackend` class and `iterm_utils.py`. tmux -CC gives native iTerm experience with tmux reliability — no reason to maintain a separate pure-iTerm backend. Port needed features (tab colors, badges, profiles) to TmuxBackend via ItermManager.
- [LOCKED, spike-validated] **Bootstrap via Python API async_send_text**: Create iTerm tab via `window.async_create_tab()`, then `session.async_send_text("tmux -CC attach -t {session}\n")`. No AppleScript needed at all. -CC connection discovered via `iterm2.async_get_tmux_connections(conn)` in ~0.5s. `Connection.async_create()` works from existing asyncio loop (no `run_forever` needed).
- [LOCKED] **Require Python API**: If iTerm2 Python API is unavailable (user hasn't enabled it), fail with clear error. No AppleScript fallback — it defeats the purpose of the migration.
- [LOCKED, from review] **Tab colors/badges move to ItermManager**: The `isinstance(backend, ItermBackend)` features in spawn_workers (tab color via `LocalWriteOnlyProfile`, badge text, tab title, `activate_app`, coordinator window detection) move to `ItermManager` methods. TmuxBackend exposes them as optional async methods that delegate to `self._iterm`. These features are preserved, not dropped.
- [LOCKED, from review] **Python API failure mode = best-effort window**: If the Python API connection fails (iTerm not running, API disabled), `ItermManager.open_session()` logs a warning and returns without opening a window. The tmux session still exists and works — the user can `tmux attach -t {session}` manually. Spawn does NOT fail. This matches the current AppleScript behavior (line 398: `pass  # Best effort — session still works via manual attach`).
- [LOCKED, spike-validated] **Tab-close-kills-window is accepted + documented**: With `-CC`, closing a native iTerm tab kills the tmux window. This is inherent to how iTerm integrates with tmux control mode. Mitigation: (1) document prominently in maniple docs, (2) **`pane-exited` sentinel does NOT fire for -CC tab closes** — must use `after-kill-pane` global hook or Python API `SessionTerminationMonitor` for registry cleanup, (3) `close_workers` uses `kill-session` (server-side) which removes -CC tabs, but **gateway tab must be closed explicitly** (it survives kill-session as an orphaned shell).
- [LOCKED, spike-validated] **`aggressive-resize` must be OFF**: iTerm2 rejects -CC connections when `aggressive-resize on` is set. `window-size latest` provides equivalent multi-client behavior. This is a global tmux option affecting all sessions.
- [LOCKED, spike-validated] **Gateway tab tracking**: ItermManager must track gateway session IDs (via `TmuxConnection.owning_session`) so they can be cleaned up on close. `kill-session` detaches the -CC client but does NOT close the gateway tab.
- [LOCKED, spike-validated] **Zero AppleScript**: The entire bootstrap uses Python API only — `window.async_create_tab()` + `session.async_send_text()`. No AppleScript needed anywhere in the new code.

## Affected Files

### Phase 0: Spike — validate -CC bootstrap end-to-end
- No code changes. Manual validation in iTerm terminal.

### Phase 1: tmux + iTerm2 configuration
- Documentation of required iTerm2 preferences and tmux settings

### Phase 2: ItermManager module
- `src/maniple_mcp/iterm_manager.py` — **NEW**: async iTerm2 Python API client with lazy connection, -CC bootstrap, window/tab management, tab colors/badges/titles
- `tests/test_iterm_manager.py` — **NEW**: unit tests for ItermManager (mocked iTerm2 API)

### Phase 3: Replace AppleScript in TmuxBackend
- `src/maniple_mcp/terminal_backends/tmux.py` — Replace `_open_iterm_for_session` and `_find_iterm_window_with_session` with ItermManager calls. Remove AppleScript imports. Add ItermManager as `self._iterm`.
- `tests/test_tmux_backend.py` — Update/add tests for the new iterm_manager integration

### Phase 4: Close/cleanup flows
- `src/maniple_mcp/terminal_backends/tmux.py` — Update `close_session` to use `kill-session` (server-side) so the -CC tab closes as a consequence
- `src/maniple_mcp/iterm_manager.py` — Add `detach_session` method for graceful -CC disconnect

### Phase 5: Remove ItermBackend (separate PR)
- `src/maniple_mcp/terminal_backends/iterm.py` — **DELETE**
- `src/maniple_mcp/iterm_utils.py` — **DELETE** (port `build_stop_hook_settings_file` to utility module, relocate `CODEX_PRE_ENTER_DELAY` to `cli_backends/constants.py`)
- `src/maniple_mcp/colors.py` — **DELETE** or migrate iTerm color logic to `iterm_manager.py`
- `src/maniple_mcp/profile.py` — **DELETE** or migrate `apply_appearance_colors` to `iterm_manager.py`
- `src/maniple_mcp/terminal_backends/__init__.py` — Remove ItermBackend export, update `select_backend` (tmux becomes the only backend, no more iTerm/tmux selection)
- `src/maniple_mcp/server.py` — Remove `ItermBackend` creation, **delete `refresh_iterm_connection`** (dead code), simplify `ensure_connection` (no iTerm refresh path), update `app_lifespan`
- `src/maniple_mcp/tools/spawn_workers.py` — Replace all `isinstance(backend, ItermBackend)` checks (~7 locations); port tab color/badge/title/layout features to TmuxBackend via ItermManager; add `target_window` parameter to `TmuxBackend.find_available_window` signature
- `src/maniple_mcp/tools/message_workers.py` — Remove ItermBackend import, move `CODEX_PRE_ENTER_DELAY` import to new location
- `src/maniple_mcp/tools/close_workers.py` — Remove `iterm_utils` import, update `CODEX_PRE_ENTER_DELAY` import
- `tests/test_iterm_utils.py` — **DELETE** or migrate relevant tests to `test_iterm_manager.py`
- `tests/test_server_terminal_backend_fallback.py` — Update fallback tests (no more implicit iTerm → tmux; server always starts tmux backend)
- `tests/test_terminal_backends.py` — Update backend selection tests (tmux only)

## Test Plan

### CI-testable (mocked iTerm2 API)

| Criterion | Test File | Test Function | Run Command | Wiring |
|-----------|-----------|---------------|-------------|--------|
| ItermManager lazy-connects on first use | `tests/test_iterm_manager.py` | `test_ensure_connected_lazy_init` | `pytest tests/test_iterm_manager.py::test_ensure_connected_lazy_init` | Called by TmuxBackend._iterm.open_session() |
| ItermManager refreshes stale connection | `tests/test_iterm_manager.py` | `test_ensure_connected_refreshes_stale` | `pytest tests/test_iterm_manager.py::test_ensure_connected_refreshes_stale` | Internal |
| Bootstrap builds correct -CC attach command | `tests/test_iterm_manager.py` | `test_bootstrap_builds_cc_attach_command` | `pytest tests/test_iterm_manager.py::test_bootstrap_builds_cc_attach_command` | Called by open_session() |
| find_window uses Python API traversal | `tests/test_iterm_manager.py` | `test_find_window_traverses_api` | `pytest tests/test_iterm_manager.py::test_find_window_traverses_api` | Called by open_session() when finding existing window |
| Window IDs persisted to iterm-windows.json | `tests/test_iterm_manager.py` | `test_window_ids_persisted` | `pytest tests/test_iterm_manager.py::test_window_ids_persisted` | Same persistence as before, new write path |
| Old window IDs invalidated on first -CC use | `tests/test_iterm_manager.py` | `test_stale_window_ids_invalidated` | `pytest tests/test_iterm_manager.py::test_stale_window_ids_invalidated` | Handles migration from AppleScript era |
| Python API failure logs warning, doesn't fail spawn | `tests/test_iterm_manager.py` | `test_api_unavailable_logs_warning` | `pytest tests/test_iterm_manager.py::test_api_unavailable_logs_warning` | Best-effort contract |
| TmuxBackend.create_session uses ItermManager | `tests/test_tmux_backend.py` | `test_create_session_calls_iterm_manager` | `pytest tests/test_tmux_backend.py::test_create_session_calls_iterm_manager` | Replaces _open_iterm_for_session |
| No AppleScript calls remain in TmuxBackend | `tests/test_tmux_backend.py` | `test_no_osascript_in_tmux_backend` | `pytest tests/test_tmux_backend.py::test_no_osascript_in_tmux_backend` | Imports tmux module source, asserts "osascript" not in text |
| close_session uses kill-session (server-side) | `tests/test_tmux_backend.py` | `test_close_session_kills_tmux_session` | `pytest tests/test_tmux_backend.py::test_close_session_kills_tmux_session` | Called by close_workers tool |
| All existing tests pass | all test files | (full suite) | `pytest tests/` | Regression gate |

### Requires live iTerm (manual verification)

| Criterion | Verification Method |
|-----------|-------------------|
| Native iTerm2 scrollback works (no duplication, no missing content) | Spawn worker, scroll up, verify single clean scrollback |
| No focus stealing on window/tab creation | Spawn worker while typing in another app, verify focus stays |
| -CC re-attach restores window layout after disconnect | Kill maniple, restart, verify tabs reappear |
| `maniple-health.sh` reports correct state | Run after spawn/close cycle |
| Tab-close kills tmux window (documented behavior) | Close native tab, verify `tmux list-sessions` shows session gone |
| `pane-exited` sentinel fires on -CC tab-close | Close native tab, verify sentinel file created, registry cleans up |
| Tab colors and badges display correctly | Spawn worker, verify tab color and badge text match project |

## Task Checklist

### Phase 0: Spike — validate -CC bootstrap (no code)

Validate these assumptions empirically before writing any ItermManager code:

- [ ] Manually run `tmux -CC attach -t {test-session}` in iTerm — confirm native tabs appear
- [ ] Confirm `async_get_tmux_connections()` discovers the -CC connection from a Python script
- [ ] **[SPIKE PRIORITY #1]** Confirm that `Connection.async_create()` works from within an existing asyncio event loop (not just `iterm2.run_forever`). Most iTerm2 examples use `run_forever()` which manages its own loop. MCP's server already has an asyncio loop running — we need `async_create()` to work from within it. If this fails, fallback options: (a) background thread with its own loop, (b) `run_until_complete` wrapper. This is the single highest-risk assumption in the plan.
- [ ] Close a native -CC tab — confirm tmux window is killed (expected) and `pane-exited` sentinel fires (critical for registry cleanup)
- [ ] Run `kill-session -t {test-session}` — confirm native tabs close as a consequence
- [ ] Confirm `terminal_windows` API traversal can find windows by tmux session name
- [ ] Test with "Automatically bury the tmux client session" ON and OFF
- [ ] **[SPIKE PRIORITY #2]** Test bootstrap method alternatives to avoid DEV-27 AppleScript race:
  - Option A: iTerm2 profile with custom command (`tmux -CC attach -t {session}`) — avoids `write text` entirely, no race
  - Option B: Python API `session.async_send_text("tmux -CC attach -t {session}\n")` — uses Python API instead of AppleScript, more reliable than osascript
  - Option C: AppleScript `write text` (current plan) — simplest but inherits the same timing race as DEV-27
  - Determine which option works and update `_bootstrap_cc` design accordingly
- [ ] Test that -CC doesn't require any global tmux options (`set -g`) that would affect non-CC sessions. Specifically check `terminal-overrides`, `set-titles`, and `default-terminal`.
- [ ] Document findings in session journal

**Exit criteria:** All assumptions confirmed, or plan revised based on findings.

### Phase 1: tmux + iTerm2 configuration

Moved before ItermManager because -CC behavior depends on these settings.

- [x] Document required iTerm2 settings (→ `docs/tmux-cc-setup.md`)
- [x] Update tmux config: `aggressive-resize off` (→ `~/.tmux.conf`)
- [x] Update tmux config: `history-limit 50000`, `mouse on`, `terminal-overrides RGB`
- [x] Document global vs per-session scope for each option
- [x] Commit: `DEV-39: Document iTerm2 and tmux configuration for -CC mode` (e47c01f)

### Phase 2: ItermManager module

Phase 2 is all-new code with heavy iTerm2 API mocking. Strict red-green TDD for 11 tests would be slower than building the module from spike learnings, then writing tests against the real API surface. Approach: **implement core methods first (informed by spike), then write tests against the implementation.** This is justified because the spike already validates the behavioral assumptions — the tests verify the module wiring, not the assumptions.

#### Core tests (write alongside implementation)
- [x] `test_ensure_connected_lazy_init` — mock Connection.async_create, verify called on first use
- [x] `test_ensure_connected_refreshes_stale` — mock stale connection, verify refresh
- [x] `test_api_unavailable_logs_warning` — connection fails, logs warning, returns without error
- [x] `test_bootstrap_builds_cc_attach_command` — verify constructed command includes `tmux -CC attach`
- [x] `test_find_window_traverses_api` — mock app.terminal_windows, verify traversal

#### Persistence and integration tests (write after core is working)
- [x] `test_window_ids_persisted` — verify read/write to iterm-windows.json
- [x] `test_stale_window_ids_invalidated` — verify old AppleScript-era IDs are re-validated via Python API on first use
- [x] `test_bootstrap_uses_existing_window` — cached window ID, creates tab in existing window
- [x] `test_open_session_best_effort_when_api_down` — API unavailable, returns silently
- [x] `test_close_session_closes_gateway` — gateway tab closed on close_session
- [x] `test_close_session_handles_missing_gateway` — no error when gateway already gone
- [x] `test_find_window_returns_none_for_unknown_project` — no match returns None

#### Implementation
- [x] Create `src/maniple_mcp/iterm_manager.py` with `ItermManager` class:
  - `__init__()` — initialize empty connection/app state, load persisted window IDs, init gateway tracking dict
  - `async ensure_connected() -> App | None` — lazy-init via `Connection.async_create()` (spike-validated: works from existing asyncio loop). On failure: log warning, return None.
  - `async open_session(tmux_session: str, project: str | None) -> None` — find/create iTerm window, bootstrap -CC via Python API (`window.async_create_tab()` + `session.async_send_text()`), discover TmuxConnection via `iterm2.async_get_tmux_connections(conn)`, track gateway session ID. Best-effort: returns silently if API unavailable.
  - `async find_window_for_project(project: str | None) -> str | None` — Python API traversal of `app.terminal_windows`. Re-validates cached window IDs. Use `tab.tmux_window_id != -1` to identify -CC controlled tabs.
  - `async _bootstrap_cc(window_id: str | None, session_name: str) -> None` — Python API only (zero AppleScript): `window.async_create_tab()` + `session.async_send_text("tmux -CC attach -t {session}\n")`. Poll `iterm2.async_get_tmux_connections(conn)` until connection appears (~0.5s). Track gateway via `TmuxConnection.owning_session`.
  - `async set_tab_appearance(session_id: str, color: tuple, title: str, badge: str) -> None` — tab color via `LocalWriteOnlyProfile`, title via `async_set_title`, badge text
  - `async activate_window(window_id: str) -> None` — bring window to front without stealing focus from other apps
  - `async close_session(tmux_session: str) -> None` — kill-session (server-side), then close gateway tab via Python API
  - `_gateways: dict[str, str]` — maps tmux session name → iTerm gateway session ID
  - `_load_window_ids()` / `_save_window_ids()` — same persistence to `~/.maniple/iterm-windows.json`
- [x] All Phase 2 tests pass (green) — 12/12 pass
- [x] Commit: `DEV-39: Add iterm_manager module with Python API + tmux -CC bootstrap` (d21f630)

### Phase 3: Replace AppleScript in TmuxBackend

#### Tests (write first)
- [ ] Write test: `test_create_session_calls_iterm_manager` — mock ItermManager, verify open_session called
- [ ] Verify tests compile and fail (red)

#### Implementation
- [ ] Add `self._iterm = ItermManager()` to `TmuxBackend.__init__`
- [ ] Replace `self._open_iterm_for_session(session_name, window_group)` call in `create_session` with `self._iterm.open_session(session_name, window_group)`
- [ ] Delete `_open_iterm_for_session` method (~80 lines)
- [ ] Delete `_find_iterm_window_with_session` method (~30 lines)
- [ ] Delete `_iterm_windows` class dict, `_ITERM_WINDOWS_PATH`, `_load_iterm_windows`, `_save_iterm_windows` (moved to ItermManager)
- [ ] Remove `osascript` subprocess imports if no longer needed
- [ ] All Phase 3 tests pass (green)
- [ ] Full test suite passes
- [ ] Commit: `DEV-39: Replace AppleScript in TmuxBackend with ItermManager`

### Phase 4: Close/cleanup flows

#### Spike findings that drive this phase:
- `pane-exited` sentinel does NOT fire on -CC tab close
- `after-kill-pane` (global hook) DOES fire
- `kill-session` kills tmux session + -CC native tabs, but gateway tab survives as orphaned shell
- Gateway identified via `TmuxConnection.owning_session`

#### Tests (write first)
- [ ] Write test: `test_close_session_kills_tmux_session` — verify `kill-session` used for named workers
- [ ] Write test: `test_close_session_closes_gateway_tab` — verify gateway tab is closed after kill-session
- [ ] Write test: `test_close_session_handles_already_disconnected` — gateway already gone, no error
- [ ] Verify tests compile and fail (red)

#### Implementation
- [ ] Track gateway session IDs in ItermManager: `_gateways: dict[str, str]` mapping tmux session name → iTerm session ID (from `TmuxConnection.owning_session`)
- [ ] Add `async close_session(tmux_session: str) -> None` to ItermManager:
  1. `kill-session -t {session}` (server-side, kills tmux + native -CC tabs)
  2. Close gateway tab via Python API `session.async_close(force=True)` (handles orphan)
  3. Remove from `_gateways` tracking
- [ ] Update `TmuxBackend.close_session()`:
  - For named workers: delegate to `self._iterm.close_session()` instead of `kill-window`
  - For shared sessions: existing `kill-window`/`kill-pane` behavior unchanged
- [ ] Replace `pane-exited` sentinel with registry-compatible detection:
  - Option A: Global `after-kill-pane` hook writes sentinel (works for -CC)
  - Option B: Python API `SessionTerminationMonitor` (event-driven, no polling)
  - Choose based on implementation complexity — both validated in spike
- [ ] Handle case where gateway is already disconnected (iTerm crash, user closed tab) — catch and log, don't fail
- [ ] All Phase 4 tests pass (green)
- [ ] Full test suite passes
- [ ] Commit: `DEV-39: Handle -CC close/cleanup with gateway tracking`

### Phase 5: Remove ItermBackend (separate PR)

This phase has the largest blast radius — it touches spawn_workers, message_workers, server, and multiple test files. Do as a follow-up PR after Phases 0-4 are verified in production.

**Migration window notes:**
- During Phases 2-4, both `ItermBackend` and `ItermManager` exist. `select_backend()` still returns `ItermBackend` when configured for iTerm mode. No feature flag needed — the two backends serve different use cases during the transition. Phase 5 is the one-way door where `ItermBackend` is removed and `select_backend()` always returns `TmuxBackend`.
- **`iterm-windows.json` collision risk:** Both `TmuxBackend._iterm_windows` (old) and `ItermManager._windows` (new) read/write `~/.maniple/iterm-windows.json`. The format is identical: `{ "project_key": "window_id" }`. No collision — both use the same keys (project names) and same values (iTerm window IDs). ItermManager re-validates cached IDs via Python API on first use, so stale AppleScript-era IDs are handled gracefully. Phase 3 deletes the old persistence from TmuxBackend, at which point only ItermManager writes the file.

- [ ] Create `src/maniple_mcp/cli_backends/constants.py` — relocate `CODEX_PRE_ENTER_DELAY` from `iterm_utils` (**hot file warning**: `message_workers.py` and `close_workers.py` import paths change — flag in PR description to avoid conflicts with in-flight work)
- [ ] Port `build_stop_hook_settings_file` from `iterm_utils` to a utility module (e.g., `src/maniple_mcp/hook_utils.py`)
- [ ] Migrate `colors.py` iTerm color logic into `iterm_manager.py` (or delete if fully replaced by `set_tab_appearance`)
- [ ] Migrate `profile.py` `apply_appearance_colors` into `iterm_manager.py` (or delete if replaced)
- [ ] Port agent-ready wait patterns from `iterm_utils` to TmuxBackend (already has `_wait_for_agent_ready` via process polling)
- [ ] Add `target_window` parameter to `TmuxBackend.find_available_window` signature (delegate to `self._iterm.find_window_for_project`)
- [ ] Add tab color/badge/title support to TmuxBackend via `self._iterm.set_tab_appearance()` (port from ItermBackend's spawn_workers integration)
- [ ] Update all `isinstance(backend, ItermBackend)` checks in spawn_workers.py (~7 locations) — replace with TmuxBackend method calls
- [ ] Update `message_workers.py` — remove ItermBackend import, update `CODEX_PRE_ENTER_DELAY` import path
- [ ] Update `close_workers.py` — update `CODEX_PRE_ENTER_DELAY` import path
- [ ] Update `server.py` — **delete `refresh_iterm_connection`** (dead code), remove iTerm refresh from `ensure_connection`, simplify `app_lifespan` (always TmuxBackend)
- [ ] Update `terminal_backends/__init__.py` — remove ItermBackend export, simplify `select_backend`
- [ ] Delete `iterm.py`, `iterm_utils.py`
- [ ] Update/delete affected tests (`test_iterm_utils.py`, `test_server_terminal_backend_fallback.py`, `test_terminal_backends.py`)
- [ ] Full test suite passes
- [ ] Commit: `DEV-39: Remove ItermBackend and iterm_utils (consolidated into ItermManager)`

### Verification
- [ ] All tests pass (green)
- [ ] /verify-before-commit
- [ ] /review-work
- [ ] Live test: spawn workers, verify native scrollback
- [ ] Live test: close workers, verify clean -CC detach
- [ ] Live test: close native tab, verify sentinel fires and registry cleans up
- [ ] Live test: maniple restart, verify -CC re-attach
- [ ] Live test: tab colors and badges display correctly

## Security Considerations

**Trust boundaries:**
- [x] All user/external input validated before use — session names are slug-sanitized before passing to tmux/AppleScript
- [x] No raw string interpolation in queries — AppleScript bootstrap uses pre-sanitized session names (existing `_tmux_safe_slug`)
- [x] Sensitive data never logged — no tokens/keys involved in window management

**Attack surface:** Minimal. The AppleScript bootstrap takes a pre-sanitized tmux session name. The Python API connects via local Unix socket (same as current ItermBackend). No new network-facing surface.

## Simplicity Check

- [x] Could this be done with fewer files/abstractions? — Single new module (iterm_manager.py) replaces two AppleScript scripts + the entire ItermBackend. Net reduction in code.
- [x] Any part of this plan solving a hypothetical future problem? — No. All changes address documented user-reported issues (double scrollback, missing content, focus stealing).
- [x] Would a junior developer understand this approach in 5 minutes? — Yes. "tmux -CC makes iTerm show tmux sessions as native tabs. ItermManager handles the window management via Python API instead of AppleScript."

## Performance Considerations

**Data volume:** N/A — window management is low-frequency (spawn/close events only)

**Main thread budget:**
- [x] All iTerm API calls are async (websocket-based)
- [x] AppleScript bootstrap runs in subprocess with 5s timeout (existing pattern)

**Query patterns:** N/A — no database operations

**Performance budget:**
- Window creation: <2s (current AppleScript is ~1-2s)
- Tab creation in existing window: <500ms
- -CC discovery after bootstrap: <1s (poll `async_get_tmux_connections`)

**Measurement plan:** Logging at DEBUG level for connection lifecycle and bootstrap timing (existing pattern in server.py)

## Code Snippets

### ItermManager core interface (spike-validated)

```python
import iterm2

class ItermManager:
    """Manages iTerm2 windows for tmux sessions via Python API + tmux -CC.

    Zero AppleScript. All operations use the iTerm2 Python API.
    Best-effort: if API unavailable, logs warning — tmux sessions still work.
    """

    _WINDOWS_PATH = Path.home() / ".maniple" / "iterm-windows.json"

    def __init__(self) -> None:
        self._connection: iterm2.Connection | None = None
        self._app: iterm2.App | None = None
        self._windows: dict[str, str] = {}  # project_key -> window_id
        self._gateways: dict[str, str] = {}  # tmux_session -> iTerm gateway session_id
        self._load_window_ids()

    async def ensure_connected(self) -> iterm2.App | None:
        """Lazy-init and refresh stale connection. Returns None on failure."""
        # Spike-validated: Connection.async_create() works from existing asyncio loop
        if self._app is not None:
            try:
                refreshed = await iterm2.async_get_app(self._connection)
                if refreshed is not None:
                    self._app = refreshed
                    return self._app
            except Exception:
                pass

        try:
            self._connection = await iterm2.Connection.async_create()
            self._app = await iterm2.async_get_app(self._connection)
            return self._app
        except Exception as e:
            logger.warning("iTerm2 Python API unavailable (%s) — windows won't open", e)
            return None

    async def open_session(
        self, tmux_session: str, project: str | None = None,
    ) -> None:
        """Open an iTerm2 window/tab for a tmux session via -CC."""
        app = await self.ensure_connected()
        if app is None:
            return

        project_key = project or "_default"
        window_id = self._windows.get(project_key)

        if window_id and not await self._window_exists(window_id):
            del self._windows[project_key]
            self._save_window_ids()
            window_id = None

        if not window_id:
            window_id = await self.find_window_for_project(project)

        await self._bootstrap_cc(window_id, tmux_session)

        if not window_id:
            window_id = await self._discover_window_for_session(tmux_session)
            if window_id:
                self._windows[project_key] = window_id
                self._save_window_ids()

    async def _bootstrap_cc(
        self, window_id: str | None, tmux_session: str,
    ) -> None:
        """Bootstrap -CC via Python API (zero AppleScript).

        1. Create tab in target window (or new window)
        2. Send tmux -CC attach command via async_send_text
        3. Poll async_get_tmux_connections until connection appears (~0.5s)
        4. Track gateway session ID for cleanup
        """
        if window_id:
            window = self._find_window_by_id(window_id)
            tab = await window.async_create_tab()
        else:
            window = await iterm2.Window.async_create(self._connection)
            tab = window.tabs[0]

        gateway = tab.current_session
        await gateway.async_send_text(f"tmux -CC attach -t {tmux_session}\n")

        # Track gateway for cleanup (kill-session doesn't close it)
        self._gateways[tmux_session] = gateway.session_id

        # Wait for -CC connection (typically ~0.5s)
        for _ in range(20):
            await asyncio.sleep(0.5)
            conns = await iterm2.async_get_tmux_connections(self._connection)
            if conns:
                break

    async def close_session(self, tmux_session: str) -> None:
        """Close a -CC session: kill tmux session + close gateway tab."""
        # kill-session removes tmux + native -CC tabs
        # but gateway tab survives as orphaned shell
        try:
            await self._run_tmux(["kill-session", "-t", tmux_session])
        except Exception:
            pass

        # Close orphaned gateway tab
        gateway_id = self._gateways.pop(tmux_session, None)
        if gateway_id:
            app = await self.ensure_connected()
            if app:
                for w in app.terminal_windows:
                    for t in w.tabs:
                        if t.current_session.session_id == gateway_id:
                            await t.current_session.async_close(force=True)
                            return

    async def find_window_for_project(self, project: str | None) -> str | None:
        """Find existing iTerm window for project via Python API.

        Uses tab.tmux_window_id to identify -CC controlled tabs (-1 = non-tmux).
        """
        ...

    async def set_tab_appearance(
        self, session_id: str, color: tuple[int, int, int],
        title: str, badge: str,
    ) -> None:
        """Set tab color, title, and badge via Python API."""
        ...
```

## Acceptance Criteria

From DEV-39:
- [ ] Native iTerm2 scrollback in all worker sessions (no duplication, no missing content)
- [ ] Window/tab management via stable IDs (no AppleScript title search)
- [ ] Tab titles and colors set via Python API
- [ ] No focus stealing on window/tab creation
- [ ] Clean detach on worker close (tmux session survives until explicit kill)
- [ ] -CC re-attach restores window layout after disconnect
- [ ] All existing maniple tests pass
- [ ] maniple-health.sh reports correct state

Added from review:
- [ ] Python API failure degrades gracefully (warning + no window, spawn succeeds)
- [ ] Old AppleScript-era window IDs in cache are re-validated on first use
- [ ] Tab colors, badges, and titles work via ItermManager (feature parity with ItermBackend)

Added from spike:
- [ ] Zero AppleScript in new code (Python API only for bootstrap and management)
- [ ] Gateway tabs cleaned up on close_session (no orphaned shells)
- [ ] `aggressive-resize off` in tmux config (required for -CC)
- [ ] Registry cleanup uses `after-kill-pane` global hook or SessionTerminationMonitor (NOT `pane-exited` — doesn't fire for -CC)
- [ ] `tab.tmux_window_id` used to identify -CC controlled tabs
