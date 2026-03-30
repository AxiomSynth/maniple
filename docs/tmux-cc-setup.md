# tmux -CC Integration Setup

Maniple uses iTerm2's tmux control mode (`-CC`) for native terminal integration.
This gives each worker its own native iTerm tab with full scrollback, instead of
running inside a tmux TUI with duplicated/garbled scrollback.

## Prerequisites

- iTerm2 (macOS)
- tmux 3.2+ (for control mode support)
- `iterm2` Python package (already in maniple's dependencies)

## iTerm2 Preferences

Open iTerm2 > Settings (Cmd+,) and configure:

### General > Magic
- **Enable Python API**: ON (required for maniple to manage windows/tabs)

### General > tmux
- **Automatically bury the tmux client session**: ON (hides the -CC gateway tab)
- **When attaching, restore windows as**: Tabs in existing window

### Profile > Terminal
- **Save lines to scrollback in alternate screen mode**: OFF
- **Save lines to scrollback when an app status bar is present**: OFF

These scrollback settings prevent the double-scrollback problem that occurs with
regular `tmux attach` (where iTerm's scrollback captures tmux's raw TUI output).

## tmux Configuration

Add to `~/.tmux.conf`:

```tmux
# Title propagation to iTerm tab titles
set-option -g set-titles on
set-option -g set-titles-string "#W"

# Window sizing — "latest" resizes to the most recently active client.
# IMPORTANT: aggressive-resize must be OFF — iTerm2 rejects -CC connections
# when it is enabled. window-size=latest provides equivalent behavior.
set-option -g aggressive-resize off
set-option -g window-size latest

# Recommended: increase scrollback for long Claude Code sessions
set-option -g history-limit 50000

# Recommended: enable mouse support
set-option -g mouse on

# Recommended: true color support
set-option -g default-terminal "tmux-256color"
set-option -ga terminal-overrides ",xterm-256color:RGB"
```

### Global vs per-session options

| Option | Scope | Notes |
|--------|-------|-------|
| `aggressive-resize off` | **Global** | REQUIRED for -CC. Affects all sessions. |
| `window-size latest` | **Global** | Affects all sessions. Equivalent to aggressive-resize for multi-client. |
| `history-limit 50000` | **Global** | Affects new windows in all sessions. Higher is better for Claude output. |
| `mouse on` | **Global** | Affects all sessions. Optional but recommended. |
| `set-titles on` | **Global** | Affects all sessions. Already used by maniple (PAT-019). |
| `terminal-overrides` | **Global** | Affects all sessions. Verify non-CC sessions still render correctly. |

Note: `-CC` mode itself is per-client (it's a flag on `tmux attach`, not a server
option). Running `tmux -CC attach -t session` does not affect other clients attached
to the same or different sessions.

## Behavior Notes

### Tab close kills tmux window
With -CC, each tmux window appears as a native iTerm tab. **Closing a native tab
kills the corresponding tmux window** (and the process running in it). This is
inherent to iTerm's tmux integration and cannot be changed.

Maniple handles this automatically:
- Workers detect window death via `after-kill-pane` global hook
- The session registry cleans up stale entries

### Gateway tab
The -CC gateway tab (where `tmux -CC attach` runs) is automatically buried when
"Automatically bury the tmux client session" is enabled. If the session is killed
server-side, the gateway tab becomes an orphaned shell — maniple cleans this up
automatically on `close_workers`.

## Troubleshooting

**"The aggressive-resize option is turned on in tmux"**
Set `set-option -g aggressive-resize off` in `~/.tmux.conf` and run
`tmux set-option -g aggressive-resize off` in any terminal to fix immediately.

**No tabs appear after -CC attach**
Check that "Enable Python API" is ON in iTerm2 > Settings > General > Magic.
Also verify that `tmux -CC attach -t session` works when typed manually.

**Double scrollback / garbled output**
Turn OFF both "Save lines to scrollback" options in Profile > Terminal.
