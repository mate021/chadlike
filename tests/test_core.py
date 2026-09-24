import http.server
import contextlib
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from chadlike import AIError, Engine, Event, Fallback, classify, classify_metadata, event_prompt, generate, load_config, main, safe_text, safe_reply


class FakeOllama:
    def __init__(self, body=None, status=200, delay=0, drip=None):
        self.body = {"message": {"content": "The remote now shares responsibility for this code."}, "done": True} if body is None else body
        self.status, self.delay, self.requests = status, delay, []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                time.sleep(owner.delay)
                body = owner.body if isinstance(owner.body, bytes) else json.dumps(owner.body).encode()
                try:
                    if drip:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                        if drip == "body":
                            self.wfile.write(f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode())
                            pieces = [bytes([byte]) for byte in body]
                        elif drip == "trailers":
                            self.wfile.write(b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n")
                            pieces = [b"X-Trailer: value\r\n"] * 40
                        else:
                            pieces = [b"X-Header: value\r\n"] * 40
                        for piece in pieces:
                            time.sleep(0.02)
                            self.wfile.write(piece)
                        self.wfile.write(b"\r\n")
                        return
                    self.send_response(owner.status)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Join delayed request handlers on close before later PTY tests fork.
        self.server.daemon_threads = False
        self.host = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.toml"

    def test_missing_defaults(self):
        config = load_config(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(config["ollama"]["model"], "qwen3:1.7b")
        self.assertIs(config["ollama"]["think"], False)
        self.assertEqual(config["long_command_seconds"], 30)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_custom_and_preserved(self):
        contents = '''prefix = "buddy: "
long_command_seconds = 5
[ollama]
model = "custom"
host = "http://localhost:9999"
think = false
temperature = 0.4
[personality]
prompt = "A different personality."
[fallback.git_push]
messages = ["Code bonk."]
'''
        self.path.write_text(contents)
        config = load_config(self.path)
        self.assertEqual(config["prefix"], "buddy: ")
        self.assertEqual(config["long_command_seconds"], 5)
        self.assertEqual(config["ollama"]["host"], "http://localhost:9999")
        self.assertEqual(config["ollama"]["model"], "custom")
        self.assertEqual(config["ollama"]["temperature"], 0.4)
        self.assertEqual(config["personality"]["prompt"], "A different personality.")
        self.assertEqual(config["fallback"]["git_push"]["messages"], ["Code bonk."])
        self.assertEqual(self.path.read_text(), contents)

    def test_malformed_config_and_types(self):
        for content, enabled in (('bad = [', False), ('enabled = "bad"\n[ollama]\ntimeout_seconds = "forever"', False), ('[fallback.git_push]\nmessages = [42, ""]', True), ('long_command_seconds = nan', True)):
            self.path.write_text(content)
            config = load_config(self.path)
            self.assertIs(config["enabled"], enabled)
            self.assertIsInstance(config["ollama"]["timeout_seconds"], float)
            self.assertTrue(config["fallback"]["git_push"]["messages"])


    def test_privacy_configuration_fails_closed(self):
        for content in ('enabled = false\ninclude_command_in_ai_context = false\nbroken = [',
                        'ai_enabled = "false"', 'include_command_in_ai_context = "false"'):
            with self.subTest(content=content):
                self.path.write_text(content)
                config = load_config(self.path)
                self.assertFalse(config["enabled"])
                self.assertFalse(config["ai_enabled"])
                self.assertFalse(config["include_command_in_ai_context"])
                with patch("chadlike.http.client.HTTPConnection") as connection:
                    with self.assertRaises(AIError):
                        generate(config, Event("network", "curl", "success", 0, 1))
                    connection.assert_not_called()

    def test_unreadable_configuration_and_legacy_opt_in(self):
        self.path.mkdir()
        config = load_config(self.path)
        self.assertFalse(config["enabled"])
        self.assertFalse(config["ai_enabled"])
        self.path.rmdir()
        self.path.write_text('include_command_in_ai_context = true')
        self.assertFalse(load_config(self.path)["include_command_in_ai_context"])


class PrivacyTests(unittest.TestCase):
    def test_secrets(self):
        commands = [
            'curl -H "Authorization: Bearer TOPSECRET" https://example.com',
            "curl --password TOPSECRET", "curl --token=TOPSECRET", "curl --api-key TOPSECRET",
            "TOKEN=TOPSECRET command curl url", "curl https://user:TOPSECRET@example.com",
            "PASSWORD='TOPSECRET' curl url", 'curl --password "TOPSECRET"',
            "curl -u user:TOPSECRET url", "curl 'https://example.com?token=TOPSECRET&x=2'",
            '''curl --data '{"password":"TOPSECRET"}' https://example.com''',
            "curl -b session=TOPSECRET url", "AWS_ACCESS_KEY_ID=TOPSECRET curl url",
            "curl ftp://user:TOPSECRET@example.com/file", "redis-cli -a TOPSECRET ping",
            "psql postgresql://user:TOPSECRET@example.com/db",
            "git commit -m 'TOPSECRET acquisition plans'", "cp /private/TOPSECRET-medical.txt backup/",
            'PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nTOPSECRET\n-----END PRIVATE KEY-----" false',
            '''TOKEN='prefix'"TOPSECRET" curl url''', r'PASSWORD=prefix\ TOPSECRET curl url',
        ]
        for command in commands:
            with self.subTest(command=command):
                event = classify(command, 1, 40)
                self.assertNotIn("TOPSECRET", repr(event))
                metadata = json.loads(event_prompt(event).split("\n", 1)[1])
                self.assertNotIn("command", metadata)
                self.assertNotIn("TOPSECRET", json.dumps(metadata))

    def test_metadata_only(self):
        with self.assertRaises(AIError):
            event_prompt(Event("network", "sensitive", "success", 0, 1))
        self.assertIsNone(classify_metadata("curl -b session=TOPSECRET", 1, 40))

    def test_inspect_and_argument_errors_do_not_echo_secrets(self):
        for args in (["inspect", "curl -b session=TOPSECRET"],
                     ["inspect", "curl", "--status", "TOPSECRET"],
                     ["TOPSECRET"]):
            output = io.StringIO()
            with self.subTest(args=args), patch.object(sys, "argv", ["chadlike", *args]), \
                    patch("chadlike.load_config", return_value={"long_command_seconds": 30}), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    status = main()
                except SystemExit as error:
                    status = error.code
                self.assertEqual(status, 2)
                self.assertNotIn("TOPSECRET", output.getvalue())

    def test_attached_credentials_do_not_reach_ai_request(self):
        for option in ('--user=user:TOPSECRET', '-uuser:TOPSECRET',
                       '--proxy-user=user:TOPSECRET', '--user="user:TOPSECRET"',
                       '-u"user:TOPSECRET"'):
            with self.subTest(option=option):
                event = classify(f"curl {option} https://example.com", 0, 1)
                self.assertNotIn("TOPSECRET", event_prompt(event))


class OutputTests(unittest.TestCase):
    def test_credential_shaped_replies_are_rejected(self):
        for text in ("Your key is TOPSECRET.", "password=TOPSECRET", "X-Api-Key: TOPSECRET",
                     "Cookie: session=TOPSECRET", "-----BEGIN PRIVATE KEY-----",
                     "postgresql://user:TOPSECRET@example.com/db", "sk-" + "a1" * 20):
            with self.subTest(text=text):
                self.assertIsNone(safe_reply(text))
        self.assertEqual(safe_reply("The remote now shares responsibility for this code."),
                         "The remote now shares responsibility for this code.")

    def test_valid_and_controls(self):
        self.assertEqual(safe_text("  Hello\nworld.  "), "Hello world.")
        self.assertEqual(safe_text("\x1b[31mRed\x1b[0m\x07\u202e."), "Red.")
        self.assertEqual(safe_text("\x1b]0;evil title\x07Okay."), "Okay.")
        self.assertIsNone(safe_text(""))
        self.assertIsNone(safe_text("x" * 181))
        self.assertIsNone(safe_text(None))


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = load_config(Path(self.temp.name) / "config.toml")
        self.config["minimum_comment_interval_seconds"] = 0
        self.event = classify("TOKEN=TOPSECRET git push", 0, 1)

    @staticmethod
    def stop_engine(engine):
        # Join test workers before later PTY tests fork an interactive shell.
        engine.close()
        engine.thread.join(3)

    def test_valid_request_and_response(self):
        with FakeOllama() as server:
            self.config["ollama"]["host"] = server.host
            result = generate(self.config, self.event)
            self.assertEqual(result, "The remote now shares responsibility for this code.")
            path, request = server.requests[0]
            self.assertEqual(path, "/api/chat")
            self.assertIs(request["think"], False)
            self.assertIs(request["stream"], False)
            self.assertEqual(request["options"]["num_predict"], 40)
            self.assertNotIn("TOPSECRET", json.dumps(request))
            self.assertEqual(request["messages"][0]["content"], self.config["personality"]["prompt"])

    def test_http_failures_and_bad_output(self):
        cases = [(b"not json", 200), ({"message": {"content": ""}, "done": True}, 200),
                 ({"message": {"content": "x" * 181}, "done": True}, 200),
                 ({"error": "model missing"}, 200), ({"error": "model missing"}, 404),
                 ({}, 500), ([], 200), ({"message": None, "done": True}, 200),
                 ({"message": {"content": "incomplete"}, "done": False}, 200)]
        for body, status in cases:
            with self.subTest(body=body, status=status), FakeOllama(body, status) as server:
                self.config["ollama"]["host"] = server.host
                with self.assertRaises(AIError):
                    generate(self.config, self.event)

    def test_secret_headers_do_not_reach_ai_request(self):
        with FakeOllama() as server:
            self.config["ollama"]["host"] = server.host
            for header in ("X-Api-Key", "api-key", "x-API_key", "X-Auth-Token",
                           "Authorization", "Proxy-Authorization"):
                for option in (f'-H "{header}: TOPSECRET"',
                               f"--header='{header}:TOPSECRET'",
                               f"--header='{header}: TOPSECRET with spaces'"):
                    with self.subTest(option=option):
                        event = classify(f"curl {option} https://example.com", 0, 1)
                        generate(self.config, event)
                        self.assertNotIn("TOPSECRET", json.dumps(server.requests[-1]))

    def test_dns_timeout_is_bounded_and_recovers_without_late_requests(self):
        release = threading.Event()
        resolver_threads = []
        original_resolve = socket.getaddrinfo

        def blocked_resolve(*args, **kwargs):
            resolver_threads.append(threading.current_thread())
            release.wait(0.8)
            return original_resolve(*args, **kwargs)

        with FakeOllama() as server:
            self.config["ollama"].update(host=server.host, timeout_seconds=0.05)
            with patch("socket.getaddrinfo", side_effect=blocked_resolve):
                engine = None
                try:
                    started = time.monotonic()
                    for _ in range(4):
                        with self.assertRaises(AIError):
                            generate(self.config, self.event)
                    self.assertLess(time.monotonic() - started, 0.4)
                    self.assertEqual(len(resolver_threads), 1)
                    messages = []
                    engine = Engine(self.config, messages.append)
                    engine.submit(self.event)
                    deadline = time.monotonic() + 0.4
                    while not messages and time.monotonic() < deadline:
                        engine.poll()
                        time.sleep(0.01)
                    self.assertTrue(messages, "A stuck resolver must still allow fallback")
                    self.assertIn(messages[0][len(self.config["prefix"]):],
                                  self.config["fallback"]["git_push"]["messages"])
                finally:
                    release.set()
                    if engine:
                        self.stop_engine(engine)
                    for thread in set(resolver_threads):
                        if thread is not threading.current_thread():
                            thread.join(2)
            self.assertEqual(server.requests, [], "Timed-out DNS must not send a late request")
            self.config["ollama"]["timeout_seconds"] = 1
            self.assertTrue(generate(self.config, self.event))
            self.assertEqual(len(server.requests), 1)

    def test_timeout(self):
        with FakeOllama(delay=0.3) as server:
            self.config["ollama"].update(host=server.host, timeout_seconds=0.05)
            with self.assertRaises(AIError):
                generate(self.config, self.event)

    def test_deadline_includes_dripping_headers_body_and_trailers(self):
        for drip in ("headers", "body", "trailers"):
            with self.subTest(drip=drip), FakeOllama(drip=drip) as server:
                self.config["ollama"].update(host=server.host, timeout_seconds=0.12)
                started = time.monotonic()
                with self.assertRaises(AIError):
                    generate(self.config, self.event)
                self.assertLess(time.monotonic() - started, 0.5)

    def test_refused_and_invalid_endpoint(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.config["ollama"]["host"] = f"http://127.0.0.1:{sock.getsockname()[1]}"
            with self.assertRaises(AIError):
                generate(self.config, self.event)
        for host in ("file:///etc/passwd", "not a url", "http://user:pass@localhost", "http://localhost:bad", "http://localhost/elsewhere"):
            self.config["ollama"]["host"] = host
            with self.assertRaises(AIError):
                generate(self.config, self.event)

    def test_fallback_categories_and_no_repeat(self):
        fallback = Fallback(self.config)
        previous = None
        for _ in range(10):
            current = fallback.choose(self.event)
            self.assertNotEqual(current, previous)
            self.assertIn(current, self.config["fallback"]["git_push"]["messages"])
            previous = current
        failure = Event("package_install", "dnf", "failure", 1, 1)
        self.assertIn(fallback.choose(failure), self.config["fallback"]["generic_failure"]["messages"])

    def test_disabled_and_queue_overflow_nonblocking(self):
        messages = []
        self.config["ai_enabled"] = False
        engine = Engine(self.config, messages.append)
        self.addCleanup(self.stop_engine, engine)
        engine.submit(self.event)
        self.assertTrue(messages)
        messages.clear()
        self.config["ai_enabled"] = True
        gate = threading.Event()
        self.addCleanup(gate.set)
        with patch("chadlike.generate", side_effect=lambda *_: gate.wait(2) or "A joke."):
            started = time.monotonic()
            for _ in range(100):
                engine.submit(self.event)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertLessEqual(engine.requests.qsize(), 3)
            self.assertTrue(messages)
            self.assertTrue(engine.thread.is_alive())
            gate.set()

    def test_engine_backend_errors_use_fallback(self):
        for body, status, delay in [(b"broken", 200, 0), ({"message": {"content": ""}, "done": True}, 200, 0), ({"message": {"content": "Your key is TOPSECRET."}, "done": True}, 200, 0), ({}, 404, 0), ({}, 200, 0.15)]:
            with self.subTest(body=body, status=status, delay=delay), FakeOllama(body, status, delay) as server:
                self.config["ollama"].update(host=server.host, timeout_seconds=0.05)
                messages = []
                engine = Engine(self.config, messages.append)
                try:
                    engine.submit(self.event)
                    deadline = time.monotonic() + 1
                    while not messages and time.monotonic() < deadline:
                        engine.poll()
                        time.sleep(0.01)
                    self.assertTrue(messages)
                    self.assertIn(messages[0][len(self.config["prefix"]):], self.config["fallback"]["git_push"]["messages"])
                finally:
                    self.stop_engine(engine)

    def test_rate_limit_stale_and_dead_thread(self):
        self.config["ai_enabled"] = False
        self.config["minimum_comment_interval_seconds"] = 10
        messages = []
        engine = Engine(self.config, messages.append)
        engine.submit(self.event)
        engine.submit(self.event)
        self.assertEqual(len(messages), 1)
        engine.results.put((self.event, time.monotonic() - 100, "Stale", ""))
        engine.poll()
        self.assertEqual(len(messages), 1)
        engine.close()
        engine.thread.join(1)
        self.config["ai_enabled"] = True
        self.config["minimum_comment_interval_seconds"] = 0
        engine.submit(self.event)
        self.assertEqual(len(messages), 2)
