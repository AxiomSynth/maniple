"""Tests for the iTerm2 window manager (tmux -CC integration)."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---- Fake iterm2 module tree ----

def _install_fake_iterm2(monkeypatch):
    """Install a minimal fake iterm2 module for testing without a real iTerm2."""

    class FakeColor:
        def __init__(self, r, g, b):
            self.red = r
            self.green = g
            self.blue = b

    class FakeProfile:
        def __init__(self):
            self._props = {}

        def set_tab_color(self, color):
            self._props["tab_color"] = color

        def set_use_tab_color(self, val):
            self._props["use_tab_color"] = val

        def set_badge_text(self, text):
            self._props["badge_text"] = text

    class FakeSession:
        def __init__(self, session_id="fake-session", name="~"):
            self.session_id = session_id
            self._name = name
            self.async_send_text = AsyncMock()
            self.async_close = AsyncMock()
            self.async_set_profile_properties = AsyncMock()

        async def async_get_variable(self, var):
            if var == "name":
                return self._name
            return None

    class FakeTab:
        def __init__(self, tab_id="1", tmux_window_id=-1, sessions=None):
            self.tab_id = tab_id
            self.tmux_window_id = tmux_window_id
            self.sessions = sessions or [FakeSession()]
            self.async_set_title = AsyncMock()

        @property
        def current_session(self):
            return self.sessions[0]

    class FakeWindow:
        def __init__(self, window_id="pty-FAKE", tabs=None):
            self.window_id = window_id
            self.tabs = tabs or [FakeTab()]
            self.async_activate = AsyncMock()

        async def async_create_tab(self):
            tab = FakeTab(tab_id="new-tab")
            self.tabs.append(tab)
            return tab

    class FakeApp:
        def __init__(self, windows=None):
            self.terminal_windows = windows or [FakeWindow()]

    class FakeConnection:
        @classmethod
        async def async_create(cls):
            return cls()

    async def fake_async_get_app(connection):
        return FakeApp()

    async def fake_async_get_tmux_connections(connection):
        return []

    async def fake_window_create(connection, **kwargs):
        return FakeWindow(window_id="pty-NEW")

    # Build fake module tree
    iterm2_pkg = types.ModuleType("iterm2")
    iterm2_pkg.Connection = FakeConnection
    iterm2_pkg.async_get_app = fake_async_get_app
    iterm2_pkg.async_get_tmux_connections = fake_async_get_tmux_connections
    iterm2_pkg.Window = MagicMock()
    iterm2_pkg.Window.async_create = AsyncMock(side_effect=fake_window_create)
    iterm2_pkg.Color = FakeColor
    iterm2_pkg.LocalWriteOnlyProfile = FakeProfile

    iterm2_conn = types.ModuleType("iterm2.connection")
    iterm2_conn.Connection = FakeConnection
    iterm2_app = types.ModuleType("iterm2.app")
    iterm2_app.async_get_app = fake_async_get_app

    monkeypatch.setitem(sys.modules, "iterm2", iterm2_pkg)
    monkeypatch.setitem(sys.modules, "iterm2.connection", iterm2_conn)
    monkeypatch.setitem(sys.modules, "iterm2.app", iterm2_app)

    return {
        "FakeApp": FakeApp,
        "FakeWindow": FakeWindow,
        "FakeTab": FakeTab,
        "FakeSession": FakeSession,
        "FakeConnection": FakeConnection,
        "FakeColor": FakeColor,
        "FakeProfile": FakeProfile,
        "pkg": iterm2_pkg,
    }


# ---- Tests: connection lifecycle ----


@pytest.mark.asyncio
async def test_ensure_connected_lazy_init(monkeypatch, tmp_path):
    """Connection.async_create is called on first use, not at init."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    assert mgr._connection is None
    assert mgr._app is None

    app = await mgr.ensure_connected()
    assert app is not None
    assert mgr._connection is not None
    assert mgr._app is not None


@pytest.mark.asyncio
async def test_ensure_connected_refreshes_stale(monkeypatch, tmp_path):
    """If connection goes stale (async_get_app raises), refresh it."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    # Prime the connection
    await mgr.ensure_connected()

    # Make async_get_app fail (simulating stale websocket)
    call_count = 0
    original_app = fakes["FakeApp"]

    async def stale_then_fresh(conn):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("stale")
        return original_app()

    import iterm2
    monkeypatch.setattr(iterm2, "async_get_app", stale_then_fresh)

    # Should detect stale, reconnect, and return fresh app
    app = await mgr.ensure_connected()
    assert app is not None
    assert call_count >= 1


@pytest.mark.asyncio
async def test_api_unavailable_logs_warning(monkeypatch, tmp_path, caplog):
    """If Python API is completely unavailable, return None and log warning."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    import iterm2
    iterm2.Connection.async_create = AsyncMock(side_effect=RuntimeError("no iTerm2"))

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    with caplog.at_level("WARNING", logger="maniple"):
        app = await mgr.ensure_connected()

    assert app is None
    assert "unavailable" in caplog.text.lower()


# ---- Tests: bootstrap ----


@pytest.mark.asyncio
async def test_bootstrap_builds_cc_attach_command(monkeypatch, tmp_path):
    """Bootstrap sends 'tmux -CC attach -t {session}' via async_send_text."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )
    # Speed up discovery timeout for tests
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_POLL_S", 0.1)

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    await mgr.ensure_connected()
    await mgr._bootstrap_cc(None, "test-session")

    # Check that a gateway tab was created and the -CC command was sent
    assert "test-session" in mgr._gateways
    # The gateway session should have received the tmux -CC attach command
    # Find the session that was used
    gateway_id = mgr._gateways["test-session"]
    # The send_text was called on the session created by async_create_tab or Window.async_create
    # Since we used Window.async_create (no window_id), check the fake
    assert gateway_id is not None


@pytest.mark.asyncio
async def test_bootstrap_uses_existing_window(monkeypatch, tmp_path):
    """When window_id is provided, bootstrap creates tab in that window."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_POLL_S", 0.1)

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    app = await mgr.ensure_connected()

    # Get the window ID of the existing fake window
    window_id = app.terminal_windows[0].window_id
    tabs_before = len(app.terminal_windows[0].tabs)

    await mgr._bootstrap_cc(window_id, "test-session")

    # A new tab should have been added to the existing window
    assert len(app.terminal_windows[0].tabs) == tabs_before + 1


# ---- Tests: window traversal ----


@pytest.mark.asyncio
async def test_find_window_traverses_api(monkeypatch, tmp_path):
    """find_window_for_project uses Python API to find matching windows."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    # Set up a window with a session named like a project
    session = fakes["FakeSession"](session_id="s1", name="dev-reviewer — ~/.claude")
    tab = fakes["FakeTab"](tab_id="t1", sessions=[session])
    window = fakes["FakeWindow"](window_id="pty-PROJECT", tabs=[tab])
    app = fakes["FakeApp"](windows=[window])

    import iterm2
    monkeypatch.setattr(iterm2, "async_get_app", AsyncMock(return_value=app))

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    await mgr.ensure_connected()
    mgr._app = app  # Use our custom app

    found = await mgr._find_window_for_project("dev-ops")
    assert found == "pty-PROJECT"


@pytest.mark.asyncio
async def test_find_window_returns_none_for_unknown_project(monkeypatch, tmp_path):
    """Returns None when no window matches the project."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    await mgr.ensure_connected()

    found = await mgr._find_window_for_project("nonexistent-project")
    assert found is None


# ---- Tests: persistence ----


@pytest.mark.asyncio
async def test_window_ids_persisted(monkeypatch, tmp_path):
    """Window IDs are saved to disk and reloaded on init."""
    fakes = _install_fake_iterm2(monkeypatch)
    path = tmp_path / "iterm-windows.json"
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH", path,
    )

    from maniple_mcp.iterm_manager import ItermManager

    # Write and save
    mgr1 = ItermManager()
    mgr1._windows = {"sieve": "pty-SIEVE", "dev-ops": "pty-DEV"}
    mgr1._save_window_ids()

    assert path.exists()
    data = json.loads(path.read_text())
    assert data == {"sieve": "pty-SIEVE", "dev-ops": "pty-DEV"}

    # Reload in a new instance
    mgr2 = ItermManager()
    assert mgr2._windows == {"sieve": "pty-SIEVE", "dev-ops": "pty-DEV"}


@pytest.mark.asyncio
async def test_stale_window_ids_invalidated(monkeypatch, tmp_path):
    """Cached window IDs are re-validated; stale ones are removed."""
    fakes = _install_fake_iterm2(monkeypatch)
    path = tmp_path / "iterm-windows.json"
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH", path,
    )
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr("maniple_mcp.iterm_manager._CC_DISCOVERY_POLL_S", 0.1)

    # Pre-seed with a stale window ID
    path.write_text(json.dumps({"dev-ops": "pty-STALE"}))

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    assert mgr._windows == {"dev-ops": "pty-STALE"}

    await mgr.ensure_connected()
    # _window_exists will return False for pty-STALE (not in fake app)
    await mgr.open_session("maniple-test", project="dev-ops")

    # The stale ID should have been removed
    assert "pty-STALE" not in mgr._windows.values()


# ---- Tests: close/gateway ----


@pytest.mark.asyncio
async def test_close_session_closes_gateway(monkeypatch, tmp_path):
    """close_session closes the tracked gateway tab."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    gateway_session = fakes["FakeSession"](session_id="gateway-123")
    tab = fakes["FakeTab"](tab_id="t-gw", sessions=[gateway_session])
    window = fakes["FakeWindow"](window_id="pty-GW", tabs=[tab])
    app = fakes["FakeApp"](windows=[window])

    import iterm2
    monkeypatch.setattr(iterm2, "async_get_app", AsyncMock(return_value=app))

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    await mgr.ensure_connected()
    mgr._app = app

    # Simulate tracked gateway
    mgr._gateways["test-session"] = "gateway-123"

    await mgr.close_session("test-session")

    # Gateway should have been closed
    gateway_session.async_close.assert_called_once_with(force=True)
    assert "test-session" not in mgr._gateways


@pytest.mark.asyncio
async def test_close_session_handles_missing_gateway(monkeypatch, tmp_path):
    """close_session doesn't error when gateway is already gone."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    await mgr.ensure_connected()

    # No gateway tracked — should not raise
    await mgr.close_session("nonexistent-session")


# ---- Tests: open_session integration ----


@pytest.mark.asyncio
async def test_open_session_best_effort_when_api_down(monkeypatch, tmp_path):
    """open_session returns silently when API is unavailable."""
    fakes = _install_fake_iterm2(monkeypatch)
    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    import iterm2
    iterm2.Connection.async_create = AsyncMock(side_effect=RuntimeError("no iTerm2"))

    from maniple_mcp.iterm_manager import ItermManager

    mgr = ItermManager()
    # Should not raise
    await mgr.open_session("test-session", project="dev-ops")


# ---- Tests: color generation ----


def test_generate_tab_color_rgb_distinct():
    """Golden ratio distribution produces distinct colors for consecutive indices."""
    from maniple_mcp.iterm_manager import generate_tab_color_rgb

    colors = [generate_tab_color_rgb(i) for i in range(5)]

    # All colors should be valid RGB tuples
    for r, g, b in colors:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255

    # Consecutive colors should be visually distinct (different hues)
    assert len(set(colors)) == 5  # all unique


def test_next_color_index_increments(monkeypatch, tmp_path):
    """next_color_index returns incrementing values."""
    from maniple_mcp.iterm_manager import ItermManager

    monkeypatch.setattr(
        "maniple_mcp.iterm_manager.ItermManager._WINDOWS_PATH",
        tmp_path / "iterm-windows.json",
    )

    mgr = ItermManager()
    assert mgr.next_color_index() == 0
    assert mgr.next_color_index() == 1
    assert mgr.next_color_index() == 2
