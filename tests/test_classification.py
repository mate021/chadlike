import unittest

from chadlike import CATEGORIES, classify, classify_metadata, command_kind, event_prompt


# Shared with the live Zsh tests. Commands are classified as data, never executed.
EXTENDED_CASES = {
    **{f"apt {sub} DUMMY_SECRET": f"apt {sub}" for sub in (
        "install", "reinstall", "remove", "purge", "update", "upgrade", "full-upgrade",
        "autoremove", "clean", "autoclean")},
    "sudo -u root apt -y -o DUMMY_SECRET -c /private/config install pkg": "apt install",
    "env TOKEN=DUMMY_SECRET command /usr/bin/apt --target-release stable upgrade": "apt upgrade",
    **{f"{name} DUMMY_SECRET": name for name in (
        "fastfetch", "neofetch", "hyfetch", "screenfetch", "btop", "htop", "top")},
    "sudo chmod -R 700 /private/DUMMY_SECRET": "chmod",
    "sudo chown -R DUMMY_SECRET:private /private/file": "chown",
    **{command: "sudo rm -rf" for command in (
        "sudo rm -rf DUMMY_SECRET", "sudo rm -fr DUMMY_SECRET", "sudo rm -r -f DUMMY_SECRET",
        "sudo rm -f -r DUMMY_SECRET", "sudo rm -Rfv DUMMY_SECRET",
        "sudo rm --recursive --force DUMMY_SECRET", "sudo rm -r --force DUMMY_SECRET",
        "sudo rm --recursive -f DUMMY_SECRET", "sudo rm DUMMY_SECRET -r -f",
        "sudo rm -rf -- DUMMY_SECRET", "sudo -u root /bin/rm -r -f DUMMY_SECRET",
        "TOKEN=DUMMY_SECRET command /usr/bin/sudo -n -uroot env FOO=bar /bin/rm -rf target",
        "sudo -- rm -rf DUMMY_SECRET", "sudo -p '-v' rm -rf DUMMY_SECRET")},
    **{command: "rm" for command in (
        "rm -rf DUMMY_SECRET", "env USER=root rm -rf DUMMY_SECRET",
        "sudo rm -r DUMMY_SECRET", "sudo rm -f DUMMY_SECRET", "sudo rm DUMMY_SECRET",
        "sudo rm -- -rf DUMMY_SECRET", "sudo rm -r -- -f DUMMY_SECRET",
        "sudo rm -f -- -r DUMMY_SECRET", "sudo rm DUMMY_SECRET-rf",
        "rm -rf DUMMY_SECRET sudo", "sudo rm DUMMY_SECRET-r -f",
        "sudo rm --interactive=rf DUMMY_SECRET",
        "sudo -p '-rf' rm DUMMY_SECRET", "sudo -u DUMMY_SECRET-rf rm -r target",
        "sudo rm --preserve-root --interactive=never DUMMY_SECRET")},
    **{command: None for command in (
        "apt list", "apt search DUMMY_SECRET", "apt show DUMMY_SECRET", "apt --version",
        "echo sudo rm -rf DUMMY_SECRET", "sudo echo rm -rf DUMMY_SECRET",
        "sudo -l rm -rf DUMMY_SECRET", "sudo -lv rm -rf DUMMY_SECRET",
        "sudo --validate rm -rf DUMMY_SECRET", "sudo -e rm -rf DUMMY_SECRET",
        "sudo --edit rm -rf DUMMY_SECRET", "sudo rm -rf --help",
        "sudo -V rm -rf DUMMY_SECRET", "command -pv sudo rm -rf DUMMY_SECRET",
        "command -p -v sudo rm -rf DUMMY_SECRET", "sudo command -V rm -rf DUMMY_SECRET",
        "sudo env --split-string=echo rm -rf DUMMY_SECRET",
        "sudo sh -c 'rm -rf DUMMY_SECRET'", "sudo rm -rf DUMMY_SECRET | cat",
        "true && sudo rm -rf DUMMY_SECRET", "sudo rm -rf $(echo DUMMY_SECRET)",
        "fastfetch > DUMMY_SECRET", "top --help", "chown --version", "chmod --help",
        "echo fastfetch", "echo btop", "echo chmod")},
    "sudo rmdir -rf DUMMY_SECRET": "rmdir",
}
for manager in ("pacman", "yay", "paru"):
    for options, normalized in (
        ("-S DUMMY_SECRET", "-S"), ("--sync DUMMY_SECRET", "-S"),
        ("--needed --noconfirm -S DUMMY_SECRET", "-S"),
        ("-U /private/DUMMY_SECRET.pkg.tar.zst", "-U"), ("--upgrade DUMMY_SECRET", "-U"),
        ("-Rns DUMMY_SECRET", "-R"), ("--remove --recursive DUMMY_SECRET", "-R"),
        ("-Syu", "-Syu"), ("-Syyu", "-Syu"), ("-S -y -u", "-Syu"),
        ("-Sy", "-Syu"), ("-Su", "-Syu"), ("--sync --refresh --sysupgrade", "-Syu"),
        ("-Scc", "-Sc"), ("-S -c", "-Sc"), ("--sync --clean", "-Sc"), ("-Syc", "-Sc"),
        ("--config -Syu -R DUMMY_SECRET", "-R"), ("-r /private/DUMMY_SECRET -Syu", "-Syu"),
        ("-b-Syu -R DUMMY_SECRET", "-R"), ("-Sb -R -u", "-Syu"),
        ("--ignore DUMMY_SECRET -Syu", "-Syu"), ("--config=DUMMY_SECRET -S", "-S"),
        ("-S -- -Syu", "-S"), ("-S DUMMY_SECRET -u", "-Syu"),
    ):
        EXTENDED_CASES[f"sudo -u root {manager} {options}"] = f"{manager} {normalized}"
    for options in ("", "-Q", "-Qdtq", "-Ss DUMMY_SECRET", "-Si DUMMY_SECRET", "-Sl",
                    "-Sg", "--sync --search DUMMY_SECRET", "-Sp DUMMY_SECRET", "-Suw",
                    "-S --print-format=%n", "-Rh", "-SV", "-S --help", "-SR DUMMY_SECRET",
                    "--config -Syu", "-- -Syu", "-Ps", "-Yc", "-S --downloadonly"):
        EXTENDED_CASES[f"{manager} {options}"] = None


class ClassificationTests(unittest.TestCase):
    def test_extended_commands_and_metadata(self):
        for command, category in EXTENDED_CASES.items():
            with self.subTest(command=command):
                expected = (CATEGORIES[category], category) if category else None
                self.assertEqual(command_kind(command), expected)
                for status in (0, 1, 130):
                    event = classify(command, status, 60)
                    self.assertEqual(event, classify_metadata(category or "unclassified", status, 60))
                    self.assertNotIn("DUMMY_SECRET", repr(event))
                    self.assertNotIn("DUMMY_SECRET", event_prompt(event))
                    if category and status != 130:
                        self.assertEqual(event.kind, CATEGORIES[category])
                    if status == 130:
                        self.assertEqual(event.kind, "command_interrupted")

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
        groups["package_install"] += ["apt install vim", "pacman -S vim", "yay -U local.pkg.tar.zst"]
        groups["package_remove"] += ["apt purge vim", "paru -Rns vim"]
        groups["package_update"] += ["apt update", "pacman -Syu", "yay -S -u", "paru -Sy"]
        groups["package_cleanup"] += ["apt autoremove", "pacman -Scc", "yay --sync --clean"]
        groups.update({
            "sudo_rm_rf": ["sudo rm -r -f target", "sudo rm -fr target"],
            "system_info": ["fastfetch", "neofetch", "hyfetch", "screenfetch"],
            "permissions_change": ["chmod 700 file"], "ownership_change": ["chown user file"],
            "monitor_exit": ["btop", "htop", "top"],
        })
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
