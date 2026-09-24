# Architecture

The repository is an Oh My Zsh custom plugin. Oh My Zsh sources the root-level
`chadlike.plugin.zsh` when `chadlike` is in `plugins=(...)`. The entry point
resolves its own directory, including symlinks, and invokes the bundled
`chadlike.py` with Python directly. A `chadlike` shell function exposes the CLI.
No installer, separate executable, PATH change, or automatic rc edit is needed.

One Python standard-library helper belongs to each interactive zsh session.
`preexec` tokenizes the command locally and records only a fixed category and time;
`precmd` captures the completed status
and writes one small atomic packet to a nonblocking FIFO. No Python or HTTP
process starts in either hook. Oversized packets and full pipes are dropped.

The Zsh classifier and Python's in-process classifier use the same fixed
categories. Apt subcommands and pacman/yay/paru operation flags map to existing
package events; Arch-style flags normalize to `-S`, `-U`, `-R`, `-Syu`, or `-Sc`.
System information tools, chmod/chown, and process monitors have dedicated
event types. A local wrapper marker and recursive/force flag checks distinguish
`sudo rm -rf` from ordinary deletion. Parsing stops options at `--` and skips
known option values. These temporary tokens and markers never leave the shell.
Monitor and deletion comments use the same post-command `precmd` path as all
other events, with interruption precedence unchanged.

The helper accepts only a fixed category vocabulary with numeric status/duration
in versioned `metadata_v1` packets. Old raw-command packets are rejected. It feeds a
bounded queue serviced by one AI thread. The main thread remains responsive
while HTTP is in flight. Results use a second nonblocking FIFO. A zsh `zle -F`
handler invalidates the displayed line, prints literal text, and redraws the
unchanged editing buffer. Results are never written from the helper to the tty.

Raw command arguments never enter the helper, IPC, event objects, queues, or AI
requests. There is no secret extraction or redaction stage and no opt-in to raw
context, including via old config files. Leading-space commands are skipped.
The helper and shell CLI use a minimal environment without inherited exported
credentials. Unreadable/invalid TOML and invalid privacy-switch types disable AI
and commentary. The model sees only fixed categories and numeric metadata;
credential-shaped model replies are rejected as an additional precaution.

DNS uses at most one additional daemon thread per helper. The AI worker waits
only for the remaining request deadline. An overdue lookup cannot be cancelled
inside libc, so further requests use fallback until that lookup exits. The
resolver only returns addresses; it never sends HTTP requests after a timeout.

No shell command is evaluated. Compound shell syntax is classified
conservatively as an aggregate rather than attributing its final status to an
individual subcommand. Status 130 is recorded as a possible interruption, not
proof that a person pressed Ctrl+C. No signal traps or editing widgets are
replaced. The integration preserves status and leaves existing hooks intact.

Session FIFOs live in a private temporary directory; normal shell exit and
parent death clean them up. `chadlike-off` also stops the helper and removes
the hooks; re-sourcing the plugin can start a fresh session. There is no service,
database, or log. Runtime requirements are Python 3.11+, zsh, and Oh My Zsh.
Configuration and the distinct fallback personality live in one XDG TOML file
outside the plugin directory, preserved across updates and plugin removal.
Bash support is out of scope. No particular prompt theme is required.
