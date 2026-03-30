"""Tests for the tmux terminal backend."""

import inspect
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from maniple_mcp.terminal_backends.base import TerminalSession
from maniple_mcp.terminal_backends.tmux import TmuxBackend, tmux_session_name_for_project


# subprocess is still needed for tests that mock tmux CalledProcessError


@pytest.mark.asyncio
async def test_send_text_uses_send_keys(monkeypatch):
    backend = TmuxBackend()
    calls = []

    async def fake_run(args):
        calls.append(args)
        return ""

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    session = TerminalSession("tmux", "%1", "%1")
    await backend.send_text(session, "hello")

    assert calls == [["send-keys", "-t", "%1", "-l", "hello"]]


@pytest.mark.asyncio
async def test_send_key_maps_ctrl_c(monkeypatch):
    backend = TmuxBackend()
    calls = []

    async def fake_run(args):
        calls.append(args)
        return ""

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    session = TerminalSession("tmux", "%2", "%2")
    await backend.send_key(session, "ctrl-c")

    assert calls == [["send-keys", "-t", "%2", "C-c"]]


@pytest.mark.asyncio
async def test_list_sessions_parses_panes(monkeypatch):
    backend = TmuxBackend()
    session_one = tmux_session_name_for_project("/Users/test/claude-team")
    session_two = tmux_session_name_for_project("/Users/test/other-project")

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return (
            f"{session_one}\t@1\tworker-1\t0\t0\t%1\n"
            "unrelated\t@2\tother\t0\t0\t%5\n"
            f"{session_two}\t@3\tworker-2\t1\t2\t%9\n"
        )

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    sessions = await backend.list_sessions()
    assert len(sessions) == 2
    assert sessions[0].native_id == "%1"
    assert sessions[0].metadata["session_name"] == session_one
    assert sessions[0].metadata["window_name"] == "worker-1"
    assert sessions[1].metadata["pane_index"] == "2"


@pytest.mark.asyncio
async def test_list_sessions_includes_legacy_prefix(monkeypatch):
    backend = TmuxBackend()
    legacy_session = "claude-team-legacy-project"

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return f"{legacy_session}\t@1\tworker-1\t0\t0\t%1\n"

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    sessions = await backend.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].metadata["session_name"] == legacy_session


@pytest.mark.asyncio
async def test_create_session_uses_tmux_commands(monkeypatch):
    backend = TmuxBackend()
    calls = []
    project_path = "/Users/test/claude-team/.worktrees/feature-foo"
    session_name = tmux_session_name_for_project(project_path)

    async def fake_run(args):
        calls.append(args)
        if args[:2] == ["has-session", "-t"]:
            raise subprocess.CalledProcessError(1, ["tmux"])
        if args[:2] == ["new-session", "-d"]:
            return "%7\t@7\t0"
        return ""

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    session = await backend.create_session(
        "test-session",
        project_path=project_path,
        issue_id="cic-e55",
    )

    assert calls[0] == ["has-session", "-t", session_name]
    assert calls[1][:4] == ["new-session", "-d", "-s", session_name]
    assert session.native_id == "%7"
    assert session.metadata["session_name"] == session_name
    assert session.metadata["window_name"] == "test-session | claude-team [cic-e55]"
    assert session.metadata["project_name"] == "claude-team"
    assert session.metadata["issue_id"] == "cic-e55"


@pytest.mark.asyncio
async def test_create_session_uses_badge_issue_id(monkeypatch):
    backend = TmuxBackend()
    calls = []
    project_path = "/Users/test/deedee-ai"
    session_name = tmux_session_name_for_project(project_path)

    async def fake_run(args):
        calls.append(args)
        if args[:2] == ["has-session", "-t"]:
            raise subprocess.CalledProcessError(1, ["tmux"])
        if args[:2] == ["new-session", "-d"]:
            return "%8\t@8\t1"
        return ""

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    session = await backend.create_session(
        "worker",
        project_path=project_path,
        coordinator_badge="Handle BEA-123 follow-up",
    )

    assert calls[0] == ["has-session", "-t", session_name]
    assert session.metadata["window_name"] == "worker | deedee-ai [BEA-123]"
    assert session.metadata["issue_id"] == "BEA-123"


@pytest.mark.asyncio
async def test_find_available_window_prefers_active_pane(monkeypatch):
    backend = TmuxBackend()
    session_one = tmux_session_name_for_project("/Users/test/alpha")
    session_two = tmux_session_name_for_project("/Users/test/bravo")

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return (
            f"{session_one}\t@1\t0\t0\t0\t%1\n"
            f"{session_one}\t@1\t0\t1\t1\t%2\n"
            f"{session_two}\t@2\t0\t0\t1\t%3\n"
        )

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    result = await backend.find_available_window(max_panes=3, managed_session_ids=None)

    assert result is not None
    session_name, window_index, session = result
    assert session_name == session_one
    assert window_index == "0"
    assert session.native_id == "%2"
    assert session.metadata["pane_index"] == "1"


@pytest.mark.asyncio
async def test_find_available_window_includes_legacy_prefix(monkeypatch):
    backend = TmuxBackend()
    legacy_session = "claude-team-legacy-project"

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return f"{legacy_session}\t@1\t0\t0\t1\t%1\n"

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    result = await backend.find_available_window(max_panes=4, managed_session_ids=None)
    assert result is not None
    session_name, _, _ = result
    assert session_name == legacy_session


@pytest.mark.asyncio
async def test_find_available_window_respects_managed_filter(monkeypatch):
    backend = TmuxBackend()
    session_one = tmux_session_name_for_project("/Users/test/alpha")
    session_two = tmux_session_name_for_project("/Users/test/bravo")

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return (
            f"{session_one}\t@1\t0\t0\t1\t%1\n"
            f"{session_one}\t@1\t0\t1\t0\t%2\n"
            f"{session_two}\t@2\t1\t0\t1\t%3\n"
        )

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    result = await backend.find_available_window(
        max_panes=4,
        managed_session_ids={"%3"},
    )

    assert result is not None
    session_name, window_index, session = result
    assert session_name == session_two
    assert window_index == "1"
    assert session.native_id == "%3"


@pytest.mark.asyncio
async def test_find_available_window_returns_none_when_full(monkeypatch):
    backend = TmuxBackend()
    session_one = tmux_session_name_for_project("/Users/test/alpha")

    async def fake_run(args):
        assert args[:2] == ["list-panes", "-a"]
        return (
            f"{session_one}\t@1\t0\t0\t1\t%1\n"
            f"{session_one}\t@1\t0\t1\t0\t%2\n"
        )

    monkeypatch.setattr(backend, "_run_tmux", fake_run)

    result = await backend.find_available_window(max_panes=2)

    assert result is None


def test_tmux_session_name_format():
    """Test that session names follow the format maniple-{slug}."""
    session = tmux_session_name_for_project("/Users/test/my-project")
    assert session == "maniple-my-project"


def test_tmux_session_name_same_for_worktree_and_main():
    """Test that worktree and main repo produce the same session name."""
    worktree_path = "/Users/test/claude-team/.worktrees/feature-foo"
    main_repo_path = "/Users/test/claude-team"

    worktree_session = tmux_session_name_for_project(worktree_path)
    main_session = tmux_session_name_for_project(main_repo_path)

    assert worktree_session == main_session
    assert worktree_session == "maniple-claude-team"


def test_tmux_session_name_fallback_for_none():
    """Test that None project path produces fallback session name."""
    session = tmux_session_name_for_project(None)
    assert session == "maniple-project"


@pytest.mark.asyncio
async def test_create_session_calls_iterm_manager(monkeypatch):
    """create_session delegates to ItermManager.open_session for named workers."""
    backend = TmuxBackend()
    calls = []
    tmux_calls = []

    async def fake_run(args):
        tmux_calls.append(args)
        if args[0] == "has-session":
            raise subprocess.CalledProcessError(1, "has-session")
        if args[0] == "new-session":
            return "%99\t@99\t0"
        if args[0] == "set-hook":
            return ""
        return ""

    async def fake_open_session(tmux_session, project=None):
        calls.append((tmux_session, project))

    monkeypatch.setattr(backend, "_run_tmux", fake_run)
    monkeypatch.setattr(backend._iterm, "open_session", fake_open_session)

    await backend.create_session(
        "test-worker",
        project_path="/Users/test/my-project",
    )

    # ItermManager.open_session should have been called with the tmux session name
    assert len(calls) == 1
    tmux_session_name, project_name = calls[0]
    assert tmux_session_name.startswith("maniple-")
    assert project_name == "my-project"


@pytest.mark.asyncio
async def test_close_session_cleans_up_gateway(monkeypatch):
    """close_session calls ItermManager.close_session for gateway cleanup."""
    backend = TmuxBackend()
    tmux_calls = []
    iterm_calls = []

    async def fake_run(args):
        tmux_calls.append(args)
        return ""

    async def fake_iterm_close(tmux_session):
        iterm_calls.append(tmux_session)

    monkeypatch.setattr(backend, "_run_tmux", fake_run)
    monkeypatch.setattr(backend._iterm, "close_session", fake_iterm_close)

    session = TerminalSession(
        "tmux", "%5", "%5",
        metadata={"session_name": "maniple-worker", "window_id": "@5"},
    )
    await backend.close_session(session)

    # ItermManager.close_session should be called with session name
    assert iterm_calls == ["maniple-worker"]
    # tmux kill-window should still be called
    assert any("kill-window" in c for c in tmux_calls)


@pytest.mark.asyncio
async def test_close_session_skips_iterm_when_no_session_name(monkeypatch):
    """close_session skips gateway cleanup for sessions without session_name."""
    backend = TmuxBackend()
    tmux_calls = []
    iterm_calls = []

    async def fake_run(args):
        tmux_calls.append(args)
        return ""

    async def fake_iterm_close(tmux_session):
        iterm_calls.append(tmux_session)

    monkeypatch.setattr(backend, "_run_tmux", fake_run)
    monkeypatch.setattr(backend._iterm, "close_session", fake_iterm_close)

    session = TerminalSession(
        "tmux", "%5", "%5",
        metadata={"window_id": "@5"},
    )
    await backend.close_session(session)

    # No iterm close call (no session_name in metadata)
    assert iterm_calls == []
    # tmux kill-window still called
    assert any("kill-window" in c for c in tmux_calls)


def test_no_osascript_in_tmux_backend():
    """No AppleScript (osascript) references remain in the tmux backend module."""
    import maniple_mcp.terminal_backends.tmux as tmux_mod
    source = inspect.getsource(tmux_mod)
    assert "osascript" not in source, "Found 'osascript' in tmux backend — AppleScript should be fully removed"
