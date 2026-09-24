"""Broader black-box coverage; only dummy secrets and isolated test homes."""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from chadlike import AIError, CATEGORIES, Event, Fallback, generate, load_config, safe_text
from test_core import FakeOllama
from test_shell import ROOT, Shell


class ExtendedCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chadlike-extended-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.toml"
        self.config = load_config(self.path)
        self.event = Event("git_push", "git push", "success", 0, 1)

    def test_every_category_has_safe_fallback_for_all_results(self):
        fallback = Fallback(self.config)
        for category, kind in CATEGORIES.items():
            for result, status in (("success", 0), ("failure", 1)):
                with self.subTest(category=category, result=result):
                    reply = fallback.choose(Event(kind, category, result, status, 1))
                    self.assertTrue(reply)
                    self.assertEqual(safe_text(reply), reply)

    def test_all_numeric_config_bounds(self):
        for value in ("-1", "0", "1e100", "inf", "-inf", "nan"):
            with self.subTest(value=value):
                self.path.write_text(f"long_command_seconds = {value}\n"
                                     f"minimum_comment_interval_seconds = {value}\n"
                                     f"max_response_age_seconds = {value}\n"
                                     f"[ollama]\ntimeout_seconds = {value}\ntemperature = {value}\n")
                config = load_config(self.path)
                for container, key, low, high in (
                    (config, "long_command_seconds", 0.01, 86400),
                    (config, "minimum_comment_interval_seconds", 0, 3600),
                    (config, "max_response_age_seconds", 1, 300),
                    (config["ollama"], "timeout_seconds", 0.05, 60),
                    (config["ollama"], "temperature", 0, 2),
                ):
                    self.assertGreaterEqual(container[key], low)
                    self.assertLessEqual(container[key], high)

    def test_oversized_config_integer_does_not_crash(self):
        for sign in ("", "-"):
            with self.subTest(sign=sign or "positive"):
                value = sign + "9" * 400
                self.path.write_text(f"long_command_seconds = {value}\n"
                                     f"minimum_comment_interval_seconds = {value}\n"
                                     f"max_response_age_seconds = {value}\n"
                                     f"[ollama]\ntimeout_seconds = {value}\n"
                                     f"temperature = {value}\nnum_predict = {value}\n")
                config = load_config(self.path)
                for container, key, low, high in (
                    (config, "long_command_seconds", 0.01, 86400),
                    (config, "minimum_comment_interval_seconds", 0, 3600),
                    (config, "max_response_age_seconds", 1, 300),
                    (config["ollama"], "timeout_seconds", 0.05, 60),
                    (config["ollama"], "temperature", 0, 2),
                    (config["ollama"], "num_predict", 1, 256),
                ):
                    self.assertEqual(container[key], low if sign else high, key)

    def test_deeply_nested_response_uses_backend_error(self):
        with FakeOllama(b"[" * 1100 + b"]" * 1100) as server:
            self.config["ollama"]["host"] = server.host
            with self.assertRaises(AIError):
                generate(self.config, self.event)

    def test_invalid_utf8_and_oversized_responses(self):
        for body in (b"\xff\xfe\xff", b"x" * 16385):
            with self.subTest(size=len(body)), FakeOllama(body) as server:
                self.config["ollama"]["host"] = server.host
                with self.assertRaises(AIError):
                    generate(self.config, self.event)

    def test_standalone_cli_smoke_and_private_errors(self):
        env = dict(os.environ, CHADLIKE_CONFIG=str(self.path), HOME=self.temp.name)
        for args, code in ((["--help"], 0), (["--version"], 0), (["config"], 0),
                           (["inspect", "git push", "--status", "1"], 0),
                           (["inspect", "curl -b DUMMY_SECRET"], 2),
                           (["inspect", "curl", "--duration", "DUMMY_SECRET"], 2)):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-B", str(ROOT / "chadlike.py"), *args],
                                        env=env, capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, code)
                self.assertNotIn("DUMMY_SECRET", result.stdout + result.stderr)


class ExtendedShellTests(unittest.TestCase):
    def shell(self, *args, **kwargs):
        shell = Shell(*args, **kwargs)
        self.addCleanup(shell.close)
        return shell

    def test_all_categories_through_shell_inspect(self):
        shell = self.shell("ai_enabled = false\n")
        for category, kind in CATEGORIES.items():
            command = category.replace("docker ", "docker compose ", 1)
            with self.subTest(command=command):
                shell.command(f"chadlike inspect {shlex.quote(command)} > inspection")
                output = (shell.home / "inspection").read_text()
                self.assertIn("\n", output, output)
                metadata = json.loads(output.split("\n", 1)[1])
                self.assertEqual(metadata["event"], kind)
                self.assertEqual(metadata["category"], category)
                self.assertNotIn("command", metadata)

    def test_startup_fallback_and_disabled_before_load(self):
        shell = self.shell("ai_enabled = false\n", show_startup=True)
        output = shell.read_until(b"chad: ") + shell.read_until(b"\r\n")
        self.assertRegex(output, b"Chad awake|Terminal here")
        disabled = self.shell(setup="CHADLIKE_DISABLED=1", show_startup=True)
        self.assertEqual(list(disabled.home.glob("chadlike.*")), [])
        self.assertIn(b"STATUS:1", disabled.command("false"))

    def test_argument_matrix_retains_only_fixed_category(self):
        shell = self.shell("ai_enabled = false\n")
        values = ["DUMMY_SECRET", "spaces DUMMY_SECRET", "DUMMY_SECRET\tvalue",
                  '"DUMMY_SECRET"', "'DUMMY_SECRET'", "æ雪DUMMY_SECRET",
                  "$(touch SHOULD_NOT_EXIST)DUMMY_SECRET", "`touch SHOULD_NOT_EXIST`DUMMY_SECRET",
                  "-----BEGIN PRIVATE KEY-----\nDUMMY_SECRET", "x" * 4500 + "DUMMY_SECRET"]
        for name in ("curl", "git commit", "cp"):
            for value in values:
                with self.subTest(name=name, value=value[:40]):
                    command = f"{name} {shlex.quote(value)}"
                    shell.command(f"chadlike inspect {shlex.quote(command)} --status 1 > inspection")
                    output = (shell.home / "inspection").read_text()
                    self.assertNotIn("DUMMY_SECRET", output)
                    metadata = json.loads(output.split("\n", 1)[1])
                    self.assertIn(metadata["category"], set(CATEGORIES) | {"unclassified"})
                    self.assertNotIn("command", metadata)
        self.assertFalse((shell.home / "SHOULD_NOT_EXIST").exists())

    def test_old_and_invalid_ipc_frames_are_rejected(self):
        server = FakeOllama()
        shell = self.shell(f'[ollama]\nhost = "{server.host}"\n')
        with server:
            fd = os.open(next(shell.home.glob("chadlike.*/events")), os.O_WRONLY | os.O_NONBLOCK)
            try:
                frames = [b"command\t0\t1\tcurl DUMMY_SECRET\0",
                          b"metadata_v1\t0\t1\tcurl DUMMY_SECRET\0",
                          b"metadata_v1\t256\t1\tcurl\0",
                          b"metadata_v1\t0\tnan\tcurl\0",
                          b"metadata_v1\tbad\t1\tcurl\0",
                          b"broken\0", b"metadata_v1\t0\t1\tgit push\0"]
                os.write(fd, b"".join(frames))
                shell.read_until(b"The remote now shares responsibility for this code.")
                self.assertEqual(len(server.requests), 1)
                self.assertNotIn("DUMMY_SECRET", json.dumps(server.requests))
            finally:
                os.close(fd)

    def test_repeated_unload_reload_preserves_single_session(self):
        shell = self.shell("ai_enabled = false\n")
        for _ in range(8):
            shell.command("chadlike-off")
            self.assertEqual(list(shell.home.glob("chadlike.*")), [])
            shell.command(f"source {shlex.quote(str(ROOT / 'chadlike.plugin.zsh'))}")
            output = shell.command('print -r -- "HOOKS:${#precmd_functions} LOADED:$_CHADLIKE_LOADED"')
            self.assertIn(b"HOOKS:1 LOADED:1", output)
            self.assertEqual(len(list(shell.home.glob("chadlike.*"))), 1)

    def test_abrupt_parent_death_removes_session(self):
        shell = self.shell("ai_enabled = false\n")
        self.addCleanup(shell.temp.cleanup)
        shell.command("true")
        directory = next(shell.home.glob("chadlike.*"))
        os.kill(shell.pid, signal.SIGKILL)
        os.waitpid(shell.pid, 0)
        os.close(shell.fd)
        shell.fd = None
        deadline = time.monotonic() + 3
        while directory.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(directory.exists(), "Orphan helper left its session directory behind")

    def test_corrupt_config_stays_silent_without_network(self):
        server = FakeOllama()
        shell = self.shell(f'enabled = false\ninclude_command_in_ai_context = false\n'
                           f'[ollama]\nhost = "{server.host}"\nbroken = [',
                           setup='curl() { return 0; }')
        with server:
            output = shell.command("curl DUMMY_SECRET")
            time.sleep(0.15)
            output += shell.command("true")
            self.assertEqual(server.requests, [])
            self.assertNotIn(b"chad:", output)
