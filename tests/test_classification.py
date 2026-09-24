import unittest

from chadlike import classify, command_kind


class ClassificationTests(unittest.TestCase):
    def test_all_commands(self):
        groups = {
            "package_install": ["dnf install vim", "sudo dnf install vim", "flatpak install app"],
            "package_remove": ["dnf remove vim", "flatpak uninstall app"],
            "package_update": ["dnf upgrade", "flatpak update"],
            "package_cleanup": ["dnf autoremove", "flatpak uninstall --unused"],
            "flatpak_exit": ["flatpak run app"], "ssh": ["ssh host"],
            "directory_create": ["mkdir directory"], "delete": ["rm file", "rmdir directory"],
            "copy": ["cp a b"], "move": ["mv a b"], "network": ["curl url", "wget url"],
            "archive": ["tar xf file", "zip file a", "unzip file"],
            "editor_exit": ["nano file", "vim file", "nvim file"],
            "service_start": ["systemctl start x"], "service_stop": ["systemctl stop x"],
            "service_restart": ["systemctl restart x", "sudo systemctl restart x"],
            "docker": [f"docker compose {s}" for s in ("up", "down", "start", "stop", "restart", "pull", "build")],
            "clear": ["clear"],
        }
        groups.update({f"git_{s}": [f"git {s}"] for s in ("clone", "pull", "commit", "push")})
        for kind, commands in groups.items():
            for command in commands:
                for status in (0, 1):
                    with self.subTest(command=command, status=status):
                        event = classify(command, status, 1)
                        self.assertEqual(event.kind, kind)
                        self.assertEqual(event.result, "failure" if status else "success")
                        self.assertEqual(event.exit_code, status)

    def test_wrappers_and_options(self):
        for command in ("env FOO=bar curl url", "TOKEN=secret command curl url", "sudo -u root /usr/bin/curl url"):
            self.assertEqual(command_kind(command)[0], "network")
        for command in ("command git pull", "git -C /tmp pull", "sudo -u root git -c x=y pull"):
            self.assertEqual(command_kind(command)[0], "git_pull")
        self.assertEqual(command_kind("docker --context local compose -f x.yml up -d")[0], "docker")
        self.assertEqual(command_kind("dnf --releasever 44 -y install vim")[0], "package_install")

    def test_negative_and_compound(self):
        for command in ("echo rm", "confirm", "git status", "git --version pull", "docker restart x", "echo 'git push'", "command -v git", "sudo -l dnf", "echo ok; rm x", "curl url | head", "false && git push", "echo $(rm x)", "'", "", "curl url > file"):
            with self.subTest(command=command):
                self.assertIsNone(classify(command, 0, 1))

    def test_precedence(self):
        self.assertEqual(classify("git push", 130, 60).kind, "command_interrupted")
        self.assertEqual(classify("git push", 1, 60).kind, "git_push")
        self.assertEqual(classify("sleep 60", 1, 60).kind, "long_command")
        self.assertEqual(classify("unknown", 1, 1).kind, "generic_failure")
        self.assertIsNone(classify("true", 0, 1))
        self.assertEqual(classify("sleep 5", 0, 5, 4).kind, "long_command")

    def test_help_and_version_do_not_report_operations(self):
        for command in ("mkdir --help", "curl --help", "curl --version",
                        "sudo cp --help", "env LC_ALL=C wget --version",
                        "git push --help", "docker compose up --help"):
            with self.subTest(command=command):
                self.assertIsNone(classify(command, 0, 1))
        # After --, these are literal operands rather than informational options.
        self.assertEqual(command_kind("mkdir -- --help"), ("directory_create", "mkdir"))
        self.assertEqual(command_kind("rm -- --version"), ("delete", "rm"))

    def test_sudo_options_stop_at_wrapped_command(self):
        for command in ("sudo curl -v https://example.com", "sudo -u root curl -v url",
                        "sudo -- curl -v url", "sudo -p '-v' curl url"):
            with self.subTest(command=command):
                self.assertEqual(command_kind(command), ("network", "curl"))
        for command in ("sudo -v", "sudo -lv curl url", "sudo -u root --list curl url"):
            with self.subTest(command=command):
                self.assertIsNone(command_kind(command))

    def test_quoted_and_escaped_punctuation_is_an_argument(self):
        for command in ("mkdir ';'", 'mkdir "|"', r"mkdir \;", "mkdir 'a;b'",
                        "mkdir '$(literal)'", r'mkdir "\$(literal)"'):
            with self.subTest(command=command):
                self.assertEqual(command_kind(command), ("directory_create", "mkdir"))
        for command in ("mkdir x; rm x", "mkdir x|cat", 'mkdir "$(echo x)"',
                        "mkdir `echo x`", "mkdir x > out", 'mkdir "x"; rm x'):
            with self.subTest(command=command):
                self.assertIsNone(command_kind(command))

    def test_130_is_not_proof_of_ctrl_c(self):
        event = classify("return 130", 130, 0)
        self.assertEqual(event.result, "possible_interruption")
        self.assertEqual(event.interruption, "possible_sigint_status_130")
        self.assertEqual(classify("sleep 5", 130, 1, interrupted=True).result, "interrupted")
