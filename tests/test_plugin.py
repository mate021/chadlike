"""Plugin lifecycle checks, plus the real Oh My Zsh loader when available."""

import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
import unittest

from test_shell import ROOT, Shell

OMZ = Path(os.environ.get("CHADLIKE_TEST_OMZ", str(Path.home() / ".oh-my-zsh")))


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chadlike-plugin-")
        self.addCleanup(self.temp.cleanup)
        self.plugin = Path(self.temp.name) / "custom with spaces/plugins/chadlike"
        (self.plugin / "config").mkdir(parents=True)
        for name in ("chadlike.plugin.zsh", "chadlike.py", "config/config.example.toml"):
            shutil.copyfile(ROOT / name, self.plugin / name)

    def shell(self, **kwargs):
        shell = Shell("ai_enabled = false\n", plugin_dir=self.plugin, **kwargs)
        self.addCleanup(shell.close)
        return shell

    def test_relocated_plugin_and_cli_without_installed_executable(self):
        shell = self.shell()
        self.assertIn(b"chadlike 0.1.0", shell.command("chadlike --version"))
        self.assertIn(b'"event": "git_push"', shell.command("chadlike inspect 'git push'"))
        output = shell.command('print -r -- "ROOT:$_CHADLIKE_ROOT PID:$_CHADLIKE_PID"')
        self.assertIn(str(self.plugin).encode(), output)
        self.assertRegex(output, rb"PID:\d+\r\n")
        pid = re.search(rb"PID:(\d+)\r\n", output)[1]
        shell.command(f"source {shlex.quote(str(self.plugin / 'chadlike.plugin.zsh'))}")
        self.assertIn(b"SAME_PID:" + pid, shell.command('print -r -- "SAME_PID:$_CHADLIKE_PID"'))
        self.assertFalse((shell.home / ".local/bin/chadlike").exists())
        self.assertFalse((self.plugin / "__pycache__").exists())

    def test_preserves_rc_config_and_prompt(self):
        shell = self.shell(setup='typeset -g TEST_PATH_BEFORE=$PATH')
        before_rc = (shell.home / ".zshrc").read_bytes()
        before_config = (shell.home / "config.toml").read_bytes()
        shell.command("chadlike config")
        shell.command(f"source {shlex.quote(str(self.plugin / 'chadlike.plugin.zsh'))}")
        output = shell.command('print -r -- "RIGHT:$RPROMPT"')
        self.assertIn(b"RIGHT:right", output)
        self.assertIn(b"STATUS:0", output)
        output = shell.command('[[ $PATH == $TEST_PATH_BEFORE ]]; print -r -- "PATH_PRESERVED:$?"')
        self.assertIn(b"PATH_PRESERVED:0", output)
        self.assertEqual((shell.home / ".zshrc").read_bytes(), before_rc)
        self.assertEqual((shell.home / "config.toml").read_bytes(), before_config)

    def test_unload_reload_and_removal_preserve_config(self):
        shell = self.shell()
        output = shell.command('print -r -- "SESSION:$_CHADLIKE_DIR PID:$_CHADLIKE_PID"')
        match = re.search(rb"SESSION:(.+) PID:(\d+)\r\n", output)
        self.assertIsNotNone(match)
        directory, pid = Path(match[1].decode()), int(match[2])
        shell.command("chadlike-off")
        shell.command("chadlike-off")
        self.assertFalse(directory.exists())
        output = shell.command('print -r -- "HOOKS:${precmd_functions} LOADED:${_CHADLIKE_LOADED:-no}"')
        self.assertIn(b"HOOKS: LOADED:no", output)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("Plugin helper survived unload")
        shell.command(f"source {shlex.quote(str(self.plugin / 'chadlike.plugin.zsh'))}")
        self.assertIn(b"LOADED:1", shell.command('print -r -- "LOADED:$_CHADLIKE_LOADED"'))
        shell.command("chadlike-off")
        shutil.rmtree(self.plugin)
        self.assertTrue((shell.home / "config.toml").exists())
        self.assertIn(b"STATUS:1", shell.command("false"))

    @unittest.skipUnless((OMZ / "oh-my-zsh.sh").is_file(), "Set CHADLIKE_TEST_OMZ to an Oh My Zsh checkout")
    def test_real_oh_my_zsh_loads_custom_plugin(self):
        shell = self.shell(ohmyzsh=OMZ)
        self.assertIn(b"chadlike 0.1.0", shell.command("chadlike --version"))
        output = shell.command('print -r -- "LOADED:$_CHADLIKE_LOADED ROOT:$_CHADLIKE_ROOT"')
        self.assertIn(b"LOADED:1", output)
        self.assertIn(str(self.plugin).encode(), output)
        self.assertIn(b"STATUS:1", shell.command("false"))

    @unittest.skipUnless((OMZ / "oh-my-zsh.sh").is_file(), "Set CHADLIKE_TEST_OMZ to an Oh My Zsh checkout")
    def test_real_oh_my_zsh_without_plugin_leaves_no_resources(self):
        shell = self.shell(ohmyzsh=OMZ, enabled=False)
        output = shell.command('print -r -- "LOADED:${_CHADLIKE_LOADED:-no}"')
        self.assertIn(b"LOADED:no", output)
        self.assertEqual(list(shell.home.glob("chadlike.*")), [])
