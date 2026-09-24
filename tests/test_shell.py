import errno
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import tempfile
import time
import unittest

from test_core import FakeOllama
from chadlike import CATEGORIES

ROOT = Path(__file__).resolve().parents[1]


class Shell:
    def __init__(self, config="", setup="", plugin_dir=None, ohmyzsh=None, enabled=True, show_startup=False):
        self.temp = tempfile.TemporaryDirectory(prefix="chadlike-test-")
        self.home = Path(self.temp.name)
        (self.home / "config.toml").write_text(f'show_startup_message = {str(show_startup).lower()}\nminimum_comment_interval_seconds = 0\n' + config)
        plugin_dir = Path(plugin_dir or ROOT)
        load = f"source {shlex.quote(str(plugin_dir / 'chadlike.plugin.zsh'))}" if enabled else ":"
        if ohmyzsh:
            custom = self.home / "custom plugins"
            (custom / "plugins").mkdir(parents=True)
            (custom / "plugins/chadlike").symlink_to(plugin_dir, target_is_directory=True)
            load = f'''export ZSH={shlex.quote(str(ohmyzsh))}
ZSH_CUSTOM={shlex.quote(str(custom))}
ZSH_CACHE_DIR={shlex.quote(str(self.home / "cache"))}
ZSH_COMPDUMP={shlex.quote(str(self.home / ".zcompdump"))}
ZSH_THEME=""
zstyle ':omz:update' mode disabled
plugins=({'chadlike' if enabled else ''})
source "$ZSH/oh-my-zsh.sh"
'''
        rc = f"""PROMPT='STATUS:%? CHADTEST> '
RPROMPT='right'
bindkey -e
{setup}
{load}
"""
        (self.home / ".zshrc").write_text(rc)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            env = dict(os.environ, HOME=str(self.home), ZDOTDIR=str(self.home), TERM="xterm-256color",
                       CHADLIKE_CONFIG=str(self.home / "config.toml"), XDG_RUNTIME_DIR=str(self.home))
            env.pop("CHADLIKE_DISABLED", None)
            os.chdir(self.home)
            os.execve("/usr/bin/zsh", ["zsh", "-d"], env)
        self.pending = b""
        self.read_until(b"CHADTEST> ", timeout=10)

    def send(self, data):
        os.write(self.fd, data.encode() if isinstance(data, str) else data)

    def read_until(self, needle, timeout=3):
        deadline = time.monotonic() + timeout
        while needle not in self.pending:
            if time.monotonic() >= deadline:
                raise AssertionError(f"Waiting for {needle!r}; received {self.pending!r}")
            if select.select([self.fd], [], [], min(0.05, max(0, deadline - time.monotonic())))[0]:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        raise AssertionError(f"Shell exited: {self.pending!r}") from error
                    raise
                self.pending += chunk
        end = self.pending.index(needle) + len(needle)
        result, self.pending = self.pending[:end], self.pending[end:]
        return result

    def command(self, command, timeout=3):
        self.drain()
        self.send(command + "\n")
        return self.read_until(b"CHADTEST> ", timeout)

    def drain(self):
        self.pending = b""
        while select.select([self.fd], [], [], 0.04)[0]:
            os.read(self.fd, 65536)

    def close(self):
        if self.fd is None:
            return
        self.send(b"\x03")
        self.drain()
        self.send("exit\n")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                break
            time.sleep(0.01)
        else:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)
        self.fd = None
        self.temp.cleanup()


class ShellTests(unittest.TestCase):
    def shell(self, *args, **kwargs):
        shell = Shell(*args, **kwargs)
        self.addCleanup(shell.close)
        return shell

    def test_load_hooks_and_exit_status(self):
        shell = self.shell("ai_enabled = false\n", setup="""
precmd() { print -r -- "EXISTING:$?"; }
preexec() { :; }
extra_precmd() { print -r -- "EXTRA:$?"; }
precmd_functions=(extra_precmd)
""")
        output = shell.command("false")
        self.assertIn(b"EXISTING:1", output)
        self.assertIn(b"EXTRA:1", output)
        self.assertIn(b"STATUS:1", output)
        self.assertIn(b"STATUS:0", shell.command("true"))
        output = shell.command('print -r -- "SAVED:$?"')
        self.assertIn(b"SAVED:0\r\n", output)
        output = shell.command('print -r -- "HOOKS:${precmd_functions}"')
        self.assertIn(b"_chadlike_precmd", output)
        shell.command("false")
        output = shell.command('print -r -- "SAVED:$?"')
        self.assertIn(b"SAVED:1\r\n", output)

    def test_fallback_after_completed_command(self):
        shell = self.shell("ai_enabled = false\n")
        shell.command("mkdir sample")
        output = shell.read_until(b"chad: ") + shell.read_until(b"\r\n")
        self.assertRegex(output, b"New folder|Directory made")

    def test_configured_name_in_startup_debug_and_helper_crash(self):
        shell = self.shell('name = "Boris"\nai_enabled = false\ndebug = true\n', show_startup=True)
        output = shell.read_until(b"boris debug:")
        self.assertRegex(output, b"boris: (Boris awake|Terminal here. Boris)")
        self.assertNotIn(b"Brain gone", output)
        self.assertNotIn(b"Chad", output)
        shell.command('kill -KILL $_CHADLIKE_PID; wait $_CHADLIKE_PID 2>/dev/null')
        output = shell.command("mkdir sample")
        self.assertIn(b"boris: Brain gone. Boris still here.", output)
        self.assertNotIn(b"Brain gone", shell.command("mkdir another"))

    def test_configured_name_in_ai_prompt_and_tag(self):
        server = FakeOllama()
        shell = self.shell(f'name = "Boris"\n[ollama]\nhost = "{server.host}"\n')
        with server:
            shell.command("mkdir sample")
            output = shell.read_until(b"The remote now shares responsibility for this code.")
            self.assertIn(b"boris: ", output)
            self.assertIn("You are Boris,", server.requests[0][1]["messages"][0]["content"])

    def test_delayed_ai_does_not_block_or_change_partial_input(self):
        server = FakeOllama(delay=0.6)
        shell = self.shell(f'[ollama]\nhost = "{server.host}"\ntimeout_seconds = 2.0\n')
        with server:
            start = time.monotonic()
            shell.command("mkdir sample")
            self.assertLess(time.monotonic() - start, 0.4)
            shell.send("print -r -- PARTIAL_INPUT")
            shell.read_until(b"The remote now shares responsibility for this code.")
            shell.send("_SURVIVED\n")
            output = shell.read_until(b"PARTIAL_INPUT_SURVIVED\r\n")
            self.assertIn(b"PARTIAL_INPUT_SURVIVED\r\n", output)
            self.assertEqual(len(server.requests), 1)

    def test_comment_waits_for_command_exit(self):
        server = FakeOllama()
        shell = self.shell(f'[ollama]\nhost = "{server.host}"\n',
                           setup="curl() { sleep 0.3; return 0; }")
        with server:
            shell.send("curl example\n")
            time.sleep(0.1)
            self.assertEqual(server.requests, [])
            shell.read_until(b"CHADTEST> ")
            shell.read_until(b"The remote now shares responsibility for this code.")
            self.assertEqual(len(server.requests), 1)

    def test_pipelines_aliases_functions_and_hook_order(self):
        shell = self.shell("ai_enabled = false\n", setup="""
autoload -Uz add-zsh-hook
later_preexec() { print -r -- PREEXEC_PRESERVED; }
add-zsh-hook preexec later_preexec
alias chad_false=false
my_failure() { return 7; }
""")
        for command, status in (("chad_false", 1), ("my_failure", 7), ("false | true", 0), ("true | false", 1)):
            output = shell.command(command)
            self.assertIn(f"STATUS:{status}".encode(), output)
            self.assertIn(b"PREEXEC_PRESERVED", output)

    def test_paused_and_disabled_config(self):
        shell = self.shell("enabled = false\nai_enabled = false\n")
        output = shell.command("false")
        self.assertIn(b"STATUS:1", output)
        time.sleep(0.15)
        shell.drain()
        self.assertNotIn(b"chad:", output)

    def test_stale_result_is_discarded(self):
        shell = self.shell("ai_enabled = false\n")
        shell.command("syswrite -o $_CHADLIKE_OUT $'1.000\\tSTALE_REPLY_MARKER\\n'")
        time.sleep(0.1)
        # The command is echoed once; a displayed reply would remain after its prompt.
        self.assertNotIn(b"STALE_REPLY_MARKER", shell.pending)

    def test_normal_exit_cleans_session(self):
        shell = self.shell("ai_enabled = false\n")
        output = shell.command('print -r -- "SESSION:$_CHADLIKE_DIR"')
        match = re.search(rb"SESSION:(\S+)\r\n", output)
        self.assertIsNotNone(match)
        directory = Path(match[1].decode())
        self.assertTrue(directory.exists())
        shell.send("exit\n")
        deadline = time.monotonic() + 2
        while directory.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(directory.exists())
        os.waitpid(shell.pid, 0)
        os.close(shell.fd)
        shell.fd = None
        shell.temp.cleanup()

    def test_ctrl_c_and_status_130(self):
        shell = self.shell("ai_enabled = false\n")
        shell.send("sleep 10\n")
        time.sleep(0.1)
        shell.send(b"\x03")
        output = shell.read_until(b"CHADTEST> ")
        self.assertIn(b"STATUS:130", output)
        shell.read_until(b"chad: ")
        shell.drain()
        shell.send("print -r -- CANCELLED")
        shell.send(b"\x03")
        shell.read_until(b"CHADTEST> ")
        self.assertIn(b"STATUS:0", shell.command("true"))

    def test_missing_backend_and_worker_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin_dir = Path(directory)
            (plugin_dir / "chadlike.plugin.zsh").write_text((ROOT / "chadlike.plugin.zsh").read_text())
            shell = self.shell(plugin_dir=plugin_dir)
        self.assertIn(b"STATUS:1", shell.command("false"))
        working = self.shell("ai_enabled = false\n")
        working.command("kill -KILL $_CHADLIKE_PID")
        time.sleep(0.05)
        output = working.command("false")
        self.assertIn(b"STATUS:1", output)
        self.assertIn(b"STATUS:0", working.command("true"))

    def test_full_request_pipe_never_blocks(self):
        shell = self.shell("ai_enabled = false\n")
        shell.command("kill -STOP $_CHADLIKE_PID")
        started = time.monotonic()
        output = shell.command("repeat 200 { _CHADLIKE_CATEGORY=mkdir; _CHADLIKE_STARTED=$EPOCHREALTIME; _chadlike_precmd; }; print FLOOD_DONE")
        self.assertIn(b"FLOOD_DONE\r\n", output)
        self.assertLess(time.monotonic() - started, 0.5)
        shell.command("kill -CONT $_CHADLIKE_PID")

    def test_raw_arguments_never_enter_ipc_even_with_legacy_opt_in(self):
        for config in ('include_command_in_ai_context = true\n',
                       'enabled = false\nai_enabled = false\ninclude_command_in_ai_context = false\n'):
            with self.subTest(config=config):
                shell = self.shell(config, setup='curl() { return 0; }\nsetopt HIST_IGNORE_SPACE\nHISTSIZE=100')
                shell.command("kill -STOP $_CHADLIKE_PID")
                fd = os.open(next(shell.home.glob("chadlike.*/events")), os.O_RDONLY | os.O_NONBLOCK)

                def drain_pipe():
                    data = b""
                    while True:
                        try:
                            data += os.read(fd, 4096)
                        except BlockingIOError:
                            return data

                try:
                    drain_pipe()
                    shell.command("curl -b session=TOPSECRET https://private.example/customer")
                    packet = drain_pipe()
                    self.assertNotIn(b"TOPSECRET", packet)
                    self.assertNotIn(b"private.example", packet)
                    fields = packet.rstrip(b"\0").split(b"\t")
                    self.assertEqual(fields[0], b"metadata_v1")
                    self.assertEqual(fields[-1], b"curl")
                    self.assertEqual(len(fields), 4)
                    shell.command(" curl -b session=TOPSECRET https://private.example/customer")
                    self.assertEqual(drain_pipe(), b"", "Leading-space commands must not be reported")
                    shell.command('print -r -- "OLD_CAPTURE:${_CHADLIKE_COMMAND-unset}"')
                    self.assertNotIn(b"TOPSECRET", drain_pipe())
                finally:
                    os.close(fd)
                    shell.command("kill -CONT $_CHADLIKE_PID")

    def test_shell_categories_and_inspect_never_forward_arguments(self):
        shell = self.shell('ai_enabled = false\n')
        cases = {
            "sudo -u root dnf install TOPSECRET": "dnf install",
            "env TOKEN=TOPSECRET curl url": "curl",
            "command git -C /TOPSECRET pull": "git pull",
            "docker --context TOPSECRET compose -f TOPSECRET up -d": "docker up",
            "flatpak uninstall --unused": "flatpak uninstall --unused",
            "git commit -m TOPSECRET": "git commit",
            "cp /TOPSECRET/medical.txt backup/": "cp",
            "curl --help": None,
            "command -pv mkdir": None,
            "sudo -v": None,
            "curl TOPSECRET | cat": None,
            "echo $(touch TOPSECRET)": None,
        }
        for command, category in cases.items():
            with self.subTest(command=command):
                shell.command(f"chadlike inspect {shlex.quote(command)} > inspection")
                output = (shell.home / "inspection").read_text()
                self.assertNotIn("TOPSECRET", output)
                if category:
                    metadata = json.loads(output.split("\n", 1)[1])
                    self.assertEqual(metadata["category"], category)
                    self.assertIn(metadata["category"], CATEGORIES)
                    self.assertNotIn("command", metadata)
                else:
                    self.assertIn("No monitored event.", output)
        self.assertFalse((shell.home / "TOPSECRET").exists())

    def test_helper_does_not_inherit_exported_credentials(self):
        shell = self.shell('ai_enabled = false\n', setup='export CHAD_TEST_SECRET_ENV=TOPSECRET')
        output = shell.command('print -r -- "HELPER:$_CHADLIKE_PID"')
        pid = int(re.search(rb"HELPER:(\d+)\r\n", output)[1])
        environment = Path(f"/proc/{pid}/environ").read_bytes()
        self.assertNotIn(b"CHAD_TEST_SECRET_ENV", environment)
        self.assertNotIn(b"TOPSECRET", environment)

    def test_confidential_arguments_absent_from_actual_ai_request(self):
        server = FakeOllama()
        shell = self.shell(f'include_command_in_ai_context = true\n[ollama]\nhost = "{server.host}"\n',
                           setup='curl() { return 0; }')
        with server:
            shell.command("curl -b session=TOPSECRET https://private.example/customer")
            shell.read_until(b"The remote now shares responsibility for this code.")
            self.assertEqual(len(server.requests), 1)
            payload = json.dumps(server.requests[0])
            self.assertNotIn("TOPSECRET", payload)
            self.assertNotIn("private.example", payload)
            metadata = json.loads(server.requests[0][1]["messages"][1]["content"].split("\n", 1)[1])
            self.assertEqual(metadata["category"], "curl")
            self.assertNotIn("command", metadata)

    def test_idempotent_source_and_cleanup(self):
        shell = self.shell("ai_enabled = false\n")
        shell.command(f"source {shlex.quote(str(ROOT / 'chadlike.plugin.zsh'))}")
        output = shell.command('print -r -- "COUNT:${#precmd_functions} DIR:$_CHADLIKE_DIR PID:$_CHADLIKE_PID"')
        match = re.search(rb"COUNT:1 DIR:(\S+) PID:(\d+)\r\n", output)
        self.assertIsNotNone(match, output)
        directory, pid = Path(match[1].decode()), int(match[2])
        shell.command("_chadlike_cleanup")
        self.assertFalse(directory.exists())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("Helper did not exit")
