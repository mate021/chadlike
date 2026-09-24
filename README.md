# chadlike

A tiny Linux terminal companion, packaged as an **Oh My Zsh plugin**, with a local
AI brain and an extremely limited backup brain. Chad comments **after commands finish**. He does not run commands,
read their output, or make your prompt wait for an AI response.

For example, after `sudo dnf install cowsay` completes:

```text
chad: Another package installed. Your dependency collection continues to flourish.
```

If Ollama is unavailable or disabled:

```text
chad: Package installed. Chad did computer.
```

AI Chad is dry, competent, sarcastic, and concise. Fallback Chad has approximately
two brain cells. The sudden stupidity is the status indicator; normal use never
prints backend errors.

## Requirements and plugin activation

Linux, Python 3.11+, zsh 5.8+, [Oh My Zsh](https://ohmyz.sh/), and the usual
`mktemp`/`mkfifo` utilities. No pip packages, desktop environment, service,
database, or root installation.
On Fedora, if needed:

```sh
sudo dnf install python3 zsh
```

For AI mode, install and start [Ollama](https://docs.ollama.com/linux), then obtain
the default model:

```sh
ollama pull qwen3:1.7b
```

Put this repository in your custom plugins directory as `chadlike`, or link the
existing checkout. From the repository root:

```zsh
chadlike_plugins="${ZSH_CUSTOM:-${ZSH:-$HOME/.oh-my-zsh}/custom}/plugins"
mkdir -p -- "$chadlike_plugins"
ln -s -- "$PWD" "$chadlike_plugins/chadlike"
unset chadlike_plugins
```

Skip the link step if the repository is already at that location. The link
command intentionally refuses to overwrite an existing plugin directory.

Add `chadlike` to the existing `plugins=(...)` array in your `.zshrc`, preserving
your other plugins, **before** the line that sources Oh My Zsh. For example:

```zsh
plugins=(git chadlike)
source "$ZSH/oh-my-zsh.sh"
```

Open a new terminal. This follows Oh My Zsh's
[custom plugin convention](https://github.com/ohmyzsh/ohmyzsh/wiki/Customization#adding-a-new-plugin):
the root `chadlike.plugin.zsh` entry point loads the bundled Python core.
Ollama is optional; fallback mode works without it. No specific theme is needed.

There is no standalone installer or uninstaller. The plugin does not edit
`.zshrc`, change PATH, or copy executables elsewhere. It exposes the `chadlike`
CLI as a shell function. Updating the plugin files leaves user configuration
untouched; repeated loading does not duplicate hooks or workers.

For development, the same entry point can be sourced directly in interactive
zsh: `source /path/to/chadlike/chadlike.plugin.zsh`.

## Configuration

Edit `${XDG_CONFIG_HOME:-$HOME/.config}/chadlike/config.toml`. `chadlike config`
creates it if missing and prints the path. `CHADLIKE_CONFIG` overrides the path.
Changes take effect in new shells, or after `chadlike-off` and sourcing
`chadlike.plugin.zsh` again. [The example](config/config.example.toml) contains every
default, the complete personality prompt, and all editable fallback pools.

```toml
enabled = true
ai_enabled = true
long_command_seconds = 30
minimum_comment_interval_seconds = 1
show_startup_message = true
include_command_in_ai_context = false # Legacy setting; true is ignored.
name = "Chad"
prefix = "{name_lower}: "
debug = false
max_response_age_seconds = 15

[ollama]
host = "http://127.0.0.1:11434"
model = "qwen3:1.7b"
think = false
temperature = 0.9
num_predict = 40
timeout_seconds = 3.0
```

Set `name = "Boris"` at the top level (before `[ollama]`) to get `boris: `
tags, Boris in the AI personality, and lines such as `Package installed. Boris did
computer.` This also changes debug labels and the helper-crash message.
Custom prefixes, personality prompts, and fallback messages support `{name}` and
`{name_lower}` placeholders. Other braces remain literal. Old copies of the
built-in Chad defaults follow the new name automatically; custom text stays as
written unless it uses a placeholder. Names are limited to 32 characters;
empty or invalid names use `Chad`.

The client uses Ollama's [documented `/api/chat` request](https://docs.ollama.com/api/chat):
`think` and `stream=false` are top-level fields; `temperature` and `num_predict`
are in `options`. Only the configured endpoint is contacted. Redirects and
environment HTTP proxies are not followed. The host must be an HTTP(S) origin
without credentials, a query, or a path prefix. A remote host is possible only
if you explicitly configure one; the default is loopback.

Cold model loading may exceed the three-second deadline. That produces a
fallback comment; increasing `timeout_seconds` or preloading the model can help.
DNS lookup shares that deadline. If a lookup stalls, requests use fallback until
it finishes; at most one background resolver is allowed per helper, and an
expired lookup never sends a delayed AI request.
The prompt remains available throughout. Unreadable or malformed TOML, and
invalid types for privacy switches, disable commentary and AI until corrected
and reloaded. Invalid non-privacy settings/pools use defaults. Command sharing
is permanently disabled, including in existing configs that set it to true.

To pause comments in one terminal, set `CHADLIKE_DISABLED=1`; unset it to resume.
Set it before sourcing to skip initialization entirely. `chadlike-off` stops the
helper, closes its pipes, and removes its hooks in the current shell. Set
`enabled=false` for persistent silence or `ai_enabled=false` for fallback only.

## Supported completed events

| Commands | What Chad reacts to |
| --- | --- |
| `dnf install/remove/upgrade/autoremove` | Package operations |
| `flatpak install/uninstall/update`, `uninstall --unused` | Flatpak package operations |
| `flatpak run`, `ssh`, `nano`, `vim`, `nvim` | Application, session, or editor returning to the shell |
| `mkdir`, `rm`, `rmdir`, `cp`, `mv` | Filesystem operations |
| `curl`, `wget` | Completed requests/downloads |
| `tar`, `zip`, `unzip` | Archive operations |
| `systemctl start/stop/restart` | Service operations |
| `docker compose up/down/start/stop/restart/pull/build` | Completed Compose commands |
| `git clone/pull/commit/push` | Completed Git commands |
| `clear` | The screen having been cleared |

An optional startup event, possible interruptions, long commands, and otherwise
unclassified failures are also supported. Precedence is interruption, recognized
command, long unclassified command, then generic failure. Ordinary successful
commands are silent. There is at most one primary event per submitted command;
rate limiting and overload may drop commentary.

Classification uses tokenization, executable names, known subcommands, common
options, and wrappers such as `sudo -u`, `env FOO=bar`, and `command`. There is no
substring matching or `eval`. Pipelines, compound statements, redirects, and
substitutions are conservatively treated as unclassified aggregate commands;
their final shell status still supports long/failure/interruption events.
Aliases and functions execute normally but are not expanded by the classifier.
`--help` and `--version` do not count as completed operations; after `--`, they
remain literal command arguments.

Exit code 130 means *possible SIGINT*: a program can deliberately return it.
Chad records that uncertainty and does not claim proof of a keypress. Ctrl+C at
an empty/editing prompt has no completed command and produces no event. No
signal traps, keybindings, history, completion, or autosuggestion widgets are
replaced. Background jobs are not tracked after the shell has returned.

## How it stays asynchronous

The [architecture note](ARCHITECTURE.md) describes the small design:

- One Python helper per shell, with one AI thread and a three-entry request queue.
- `add-zsh-hook` registers `preexec` to record a fixed category/time and `precmd` to capture
  the final status before other hook-array entries. Hooks use only zsh builtins.
- Metadata packets use a private nonblocking FIFO and atomic writes below Linux's
  `PIPE_BUF`. Full pipes are dropped immediately. Commands exceeding 4,000 characters
  are treated as unclassified without tokenizing their arguments.
- The helper classifies and rate-limits events; one HTTP request runs at a time.
  Full queues use cheap fallback when rate limits permit. Results are bounded,
  validated, and sent through a second nonblocking FIFO.
- A supported [`zle -F` handler](https://zsh.sourceforge.io/Doc/Release/Zsh-Line-Editor.html)
  calls `zle -I` before literal printing. ZLE redraws the existing editing buffer;
  Chad never writes directly to `/dev/tty` and never changes `BUFFER` or `CURSOR`.
  While an editor or other command owns the terminal, comments wait in the pipe.
- Stale AI results expire at `max_response_age_seconds`. Responses buffered in
  the shell additionally have a fixed 15-second display age limit.
- Normal shell exit, `chadlike-off`, and parent death remove the private
  session pipes. A crashed helper triggers one small emergency fallback at a
  subsequent prompt; open a new shell to restart it. The helper supplies that
  sentence with the configured name and prefix during initialization. A helper
  that dies before supplying it stays silent.

Existing hook functions remain intact. Returning zero from Chad's `precmd`
allows later hooks to run; zsh preserves the command status around dispatch.
The Oh My Zsh plugin uses standard zsh hooks and requires no particular theme.
It preserves existing `PROMPT`/`RPROMPT` settings. A deliberately failing existing `precmd` function can stop
zsh's hook array before Chad is called; Chad does not replace that function.

## Privacy and troubleshooting

No stdout/stderr capture, file-content access, history scan, environment scan,
telemetry, analytics, cloud AI, or persistent logs. The helper reads only its
configuration and session pipes. Zsh supplies command text to the shell hook,
which tokenizes it locally without expansion or execution, retains only a fixed
category such as `curl` or `git push`, and discards the arguments. No raw command
text enters IPC, Python event objects, queues, AI requests, or debug output.
This excludes credentials, URLs, filenames, commit messages, and other arguments
regardless of their format. Commands beginning with whitespace are skipped entirely.

Only category, event type, exit status, duration, and interruption evidence reach
the model. `include_command_in_ai_context` is obsolete: even `true` cannot enable
raw context. The helper and shell CLI start with a minimal environment rather
than inheriting exported credentials. The model has no tools or file access.
Model output is limited to 180 characters, flattened to one line, and stripped
of terminal escapes and Unicode control/format characters. Obvious credential-shaped
replies (key blocks, credential assignments, authenticated URLs, and long opaque
strings) are rejected and use fallback. This output check is an additional precaution,
not a universal secret detector; withholding command arguments is the privacy boundary.
Prefixes and custom fallback messages are also validated for terminal safety.

Set `debug=true` to show short event/status/duration/queue/mode/error-code lines
through the same safe display channel. Debug does not log commands, response
bodies, or endpoint credentials. Inspect classification without executing the
supplied command or contacting Ollama:

```sh
chadlike inspect 'sudo dnf install cowsay' --status 0 --duration 8.4
```

The shell function converts that input to a fixed category before starting Python;
only metadata is printed. When invoking Python directly, `inspect` accepts only
a category, for example `python3 chadlike.py inspect 'dnf install'`. Invalid input
is rejected without echoing its contents.

After upgrading from a version that captured command text, restart existing shells,
or run `chadlike-off` and source the updated plugin. Already-running helpers and
hooks do not update automatically.

If nothing appears, check that the shell is interactive zsh with ZLE enabled,
that `enabled` is true, and that comments are not paused or rate-limited. Run
`zle -F -L` to inspect the listener. If Chad becomes stupid, check your local
Ollama process/model or enable debug. A missing Python interpreter or bundled
core simply disables initialization without breaking startup. Bash is outside
the scope of this Oh My Zsh plugin.

## Disabling and removing the plugin

Remove `chadlike` from your `.zshrc` `plugins=(...)` array. Run `chadlike-off` in
each existing terminal, or close those terminals, to remove their hooks and stop
their helpers. Then remove the custom `plugins/chadlike` directory or symlink.
If you linked a development checkout, removing the link leaves the checkout intact.

`chadlike-off` is safe to repeat. It preserves your configuration and leaves
the `chadlike` CLI function available until the shell closes; re-sourcing the
plugin entry point starts a fresh session. Configuration remains at
`${XDG_CONFIG_HOME:-$HOME/.config}/chadlike/config.toml` (or `CHADLIKE_CONFIG`).
You can delete that exact file separately if you want to discard your settings.

If migrating from an earlier standalone version, remove its marked chadlike
source block from `.zshrc` before enabling the plugin. Its old
`~/.local/bin/chadlike` symlink and `~/.local/share/chadlike` files are no longer
used and can be removed after reviewing their contents. Keep the existing XDG
config. The plugin does not automatically migrate or delete an earlier setup.

## Development and tests

```sh
python3 -m unittest discover -s tests -v
zsh -n chadlike.plugin.zsh
python3 -m py_compile chadlike.py
```

Tests use only the standard library, temporary home directories, localhost fake
Ollama servers, and real interactive zsh pseudo-terminals. They require permission
to open local sockets and PTYs, but neither a real Ollama server nor a downloaded
model. They exercise typing during delayed inference, Ctrl+C, exit status,
existing hooks, full queues/pipes, worker failure, plugin relocation, unloading,
reloading, configuration preservation, and cleanup. Real Oh My Zsh loader tests
use `~/.oh-my-zsh` when present; set `CHADLIKE_TEST_OMZ=/path/to/ohmyzsh` to use
another checkout. Those two tests are explicitly skipped if none is available.
Tests isolate HOME, ZDOTDIR, custom plugins, and framework caches in temporary
directories and disable Oh My Zsh updates. They do not change your real settings.

Python and zsh are available from Fedora; Oh My Zsh is installed separately.
MIT licensed.
