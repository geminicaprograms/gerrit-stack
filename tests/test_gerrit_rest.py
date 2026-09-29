#!/usr/bin/env python3
"""Unit tests for scripts/gerrit-rest.py.

The script is loaded via importlib (its filename has a dash). HTTP-level tests run
against a stub http.server bound to 127.0.0.1:0 that records every request and serves
canned JSON behind the Gerrit XSSI prefix.

Run from the repo root:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "gerrit-rest.py"
XSSI = ")]}'\n"
CHANGE_ID = "I" + "0123456789abcdef" * 2 + "01234567"  # 40 hex chars


def load_script():
    spec = importlib.util.spec_from_file_location("gerrit_rest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gr = load_script()


# ---------------------------------------------------------------- stub Gerrit


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "json": json.loads(body) if body else None,
            }
        )
        for method, pattern, status, payload in self.server.routes:
            if method == self.command and re.fullmatch(pattern, self.path):
                break
        else:
            out = f"Not found: {self.path}".encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        out = b"" if payload is None else (XSSI + json.dumps(payload)).encode("utf-8")
        self.send_response(status)
        if out:
            self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PUT = do_DELETE = _serve

    def log_message(self, *_args) -> None:  # keep test output quiet
        pass


class StubServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        self.server.requests = []
        self.server.routes = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.host = f"http://127.0.0.1:{self.port}"
        self.tmp = tempfile.mkdtemp(prefix="gerrit-rest-test-")
        self.no_netrc = os.path.join(self.tmp, "netrc-does-not-exist")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def route(self, method: str, path: str, payload=None, status: int = 200, regex: bool = False) -> None:
        self.server.routes.append((method, path if regex else re.escape(path), status, payload))

    def write_netrc(self, machine: str = "127.0.0.1", login: str = "admin", password: str = "s3cret",
                    text: str | None = None) -> str:
        path = os.path.join(self.tmp, "netrc")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text if text is not None else f"machine {machine} login {login} password {password}\n")
        os.chmod(path, 0o600)
        return path

    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str, str]:
        environment = {"NETRC": self.no_netrc, "HOME": self.tmp}
        environment.update(env or {})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = gr.main(list(argv), env=environment)
            except SystemExit as exc:  # pragma: no cover - main() swallows argparse exits
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    @property
    def requests(self) -> list:
        return self.server.requests

    @property
    def last(self) -> dict:
        return self.server.requests[-1]


# ---------------------------------------------------------------- pure helpers


class ChangeRefTests(unittest.TestCase):
    def test_number_passes_through(self):
        self.assertEqual(gr.parse_change_ref("123"), "123")
        self.assertEqual(gr.parse_change_ref("  42 "), "42")

    def test_full_change_id_passes_through(self):
        self.assertEqual(gr.parse_change_ref(CHANGE_ID), CHANGE_ID)

    def test_url_forms(self):
        self.assertEqual(gr.parse_change_ref("https://gerrit.example.com/c/foo/bar/+/456"), "456")
        self.assertEqual(gr.parse_change_ref("https://gerrit.example.com/c/foo/+/456/3/src/x.py"), "456")
        self.assertEqual(gr.parse_change_ref("http://localhost:8080/c/demo-plugin/+/7?tab=comments"), "7")
        self.assertEqual(gr.parse_change_ref("/+/789"), "789")
        self.assertEqual(gr.parse_change_ref("/c/demo/+/789/"), "789")
        self.assertEqual(gr.parse_change_ref("https://gerrit.example.com/#/c/42/"), "42")

    def test_triplet_is_url_encoded_per_component(self):
        self.assertEqual(
            gr.parse_change_ref(f"demo-plugin~master~{CHANGE_ID}"),
            f"demo-plugin~master~{CHANGE_ID}",
        )
        self.assertEqual(
            gr.parse_change_ref(f"plugins/foo~refs/heads/main~{CHANGE_ID}"),
            f"plugins%2Ffoo~refs%2Fheads%2Fmain~{CHANGE_ID}",
        )

    def test_rejects_garbage(self):
        for bad in ("", "   ", "nonsense", "https://gerrit.example.com/dashboard/self", "a~~b"):
            with self.subTest(bad=bad), self.assertRaises(gr.UsageError):
                gr.parse_change_ref(bad)


class XssiTests(unittest.TestCase):
    def test_strips_prefix_with_newline(self):
        self.assertEqual(gr.decode_response(b")]}'\n{\"a\": 1}"), {"a": 1})

    def test_tolerates_prefix_without_newline_and_no_prefix(self):
        self.assertEqual(gr.decode_response(b")]}'[1, 2]"), [1, 2])
        self.assertEqual(gr.decode_response(b"{\"b\": 2}"), {"b": 2})

    def test_empty_body_is_none(self):
        self.assertIsNone(gr.decode_response(b""))
        self.assertIsNone(gr.decode_response(b")]}'\n"))

    def test_non_json_raises_rest_error(self):
        with self.assertRaises(gr.RestError):
            gr.decode_response(b")]}'\n<html>oops</html>")


class HostResolutionTests(unittest.TestCase):
    @staticmethod
    def config_with(values: dict):
        """Fake `git config --get` backed by a dict of real key names."""
        return lambda key, cwd=None: values.get(key)

    def test_explicit_beats_env_and_config(self):
        cfg = self.config_with({"gerrit-stack.host": "http://cfg"})
        self.assertEqual(gr.resolve_host("http://flag/", {"GERRIT_HOST": "http://env"}, config=cfg), "http://flag")

    def test_env_beats_git_config(self):
        cfg = self.config_with({"gerrit-stack.host": "http://cfg"})
        self.assertEqual(gr.resolve_host(None, {"GERRIT_HOST": "https://env"}, config=cfg), "https://env")

    def test_git_config_host_beats_remote(self):
        cfg = self.config_with({"gerrit-stack.host": "http://cfg", "remote.origin.url": "https://origin/a/p"})
        self.assertEqual(gr.resolve_host(None, {}, config=cfg), "http://cfg")

    def test_derived_from_http_origin(self):
        cfg = self.config_with({"remote.origin.url": "https://user@gerrit.example.com:8443/a/plugins/foo"})
        self.assertEqual(gr.resolve_host(None, {}, config=cfg), "https://gerrit.example.com:8443")

    def test_non_http_origin_is_an_error(self):
        cfg = self.config_with({"remote.origin.url": "ssh://user@gerrit.example.com:29418/plugins/foo"})
        with self.assertRaises(gr.UsageError):
            gr.resolve_host(None, {}, config=cfg)

    def test_nothing_configured_is_an_error(self):
        with self.assertRaises(gr.UsageError) as ctx:
            gr.resolve_host(None, {}, config=lambda key, cwd=None: None)
        self.assertIn("--host", str(ctx.exception))

    def test_host_from_remote_url(self):
        self.assertEqual(gr.host_from_remote_url("https://h/a/proj"), "https://h")
        self.assertEqual(gr.host_from_remote_url("https://h/gerrit/a/proj/sub"), "https://h/gerrit")
        self.assertEqual(gr.host_from_remote_url("http://h:8080/proj"), "http://h:8080")
        self.assertEqual(gr.host_from_remote_url("https://h/a"), "https://h")
        self.assertIsNone(gr.host_from_remote_url("git@h:proj.git"))
        self.assertIsNone(gr.host_from_remote_url(None))

    def test_normalise_host_adds_scheme(self):
        self.assertEqual(gr.normalise_host("localhost:8080/"), "http://localhost:8080")
        self.assertEqual(gr.normalise_host("gerrit.example.com"), "https://gerrit.example.com")
        self.assertEqual(gr.normalise_host("HTTP://x/"), "HTTP://x")

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_real_git_config_lookup(self):
        repo = tempfile.mkdtemp(prefix="gerrit-rest-git-")
        self.addCleanup(shutil.rmtree, repo, True)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def git(*args):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

        git("init", "-q")
        git("remote", "add", "origin", "https://gerrit.example.com/a/demo-plugin")
        self.assertEqual(gr.resolve_host(None, {}, cwd=repo), "https://gerrit.example.com")
        git("config", "gerrit-stack.host", "http://localhost:8080")
        self.assertEqual(gr.resolve_host(None, {}, cwd=repo), "http://localhost:8080")
        self.assertIsNone(gr.git_config("gerrit-stack.nope", cwd=repo))


class ReviewInputTests(unittest.TestCase):
    def test_nested_comments_grouped_by_path(self):
        body = gr.build_review_input(
            "Done.",
            ["src/a.py:10:nit: rename", "src/a.py:20:praise: nice", "src/b.py:0:question: why?"],
            in_reply_to="cid-1",
        )
        self.assertEqual(
            body,
            {
                "message": "Done.",
                "comments": {
                    "src/a.py": [
                        {"message": "nit: rename", "unresolved": True, "line": 10, "in_reply_to": "cid-1"},
                        {"message": "praise: nice", "unresolved": True, "line": 20, "in_reply_to": "cid-1"},
                    ],
                    "src/b.py": [
                        {"message": "question: why?", "unresolved": True, "in_reply_to": "cid-1"},
                    ],
                },
            },
        )
        self.assertNotIn("labels", body)

    def test_resolved_flag_sets_unresolved_false(self):
        body = gr.build_review_input("Fixed.", ["/COMMIT_MSG:7:Done."], resolved=True)
        self.assertFalse(body["comments"]["/COMMIT_MSG"][0]["unresolved"])
        self.assertNotIn("in_reply_to", body["comments"]["/COMMIT_MSG"][0])

    def test_message_only(self):
        self.assertEqual(gr.build_review_input("Thanks", []), {"message": "Thanks"})

    def test_bad_specs(self):
        for spec in ("nopath", "a.py:x:msg", "a.py:1:", ":1:msg", "a.py:-3:msg"):
            with self.subTest(spec=spec), self.assertRaises(gr.UsageError):
                gr.build_review_input("m", [spec])
        with self.assertRaises(gr.UsageError):
            gr.build_review_input("m", [], in_reply_to="cid")
        with self.assertRaises(gr.UsageError):
            gr.build_review_input("", [])


# ---------------------------------------------------------------- auth + wiring


class AuthTests(StubServerCase):
    def test_netrc_match_adds_prefix_and_basic_header(self):
        netrc_path = self.write_netrc()
        self.route("GET", "/a/changes/123/revisions/current/related", {"changes": []})
        code, out, err = self.run_cli("--host", self.host, "--table", "related", "123",
                                      env={"NETRC": netrc_path})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["path"], "/a/changes/123/revisions/current/related")
        expected = "Basic " + base64.b64encode(b"admin:s3cret").decode("ascii")
        self.assertEqual(self.last["headers"].get("authorization"), expected)
        self.assertIn("(no related changes)", out)

    def test_no_netrc_means_anonymous(self):
        self.route("GET", "/changes/123/revisions/current/related", {"changes": []})
        code, _out, err = self.run_cli("--host", self.host, "related", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["path"], "/changes/123/revisions/current/related")
        self.assertNotIn("authorization", self.last["headers"])

    def test_netrc_for_other_machine_is_ignored(self):
        netrc_path = self.write_netrc(machine="gerrit.example.com")
        self.route("GET", "/changes/123/revisions/current/related", {"changes": []})
        code, _out, _err = self.run_cli("--host", self.host, "related", "123", env={"NETRC": netrc_path})
        self.assertEqual(code, 0)
        self.assertEqual(self.last["path"], "/changes/123/revisions/current/related")
        self.assertNotIn("authorization", self.last["headers"])

    def test_anonymous_flag_overrides_netrc(self):
        netrc_path = self.write_netrc()
        self.route("GET", "/changes/123/revisions/current/related", {"changes": []})
        code, _out, _err = self.run_cli("--host", self.host, "--anonymous", "related", "123",
                                        env={"NETRC": netrc_path})
        self.assertEqual(code, 0)
        self.assertEqual(self.last["path"], "/changes/123/revisions/current/related")
        self.assertNotIn("authorization", self.last["headers"])

    def test_unparseable_netrc_is_ignored(self):
        netrc_path = self.write_netrc(text="machine 127.0.0.1 login admin password\n  garbage %% here\nmachine")
        self.assertIsNone(gr.netrc_auth(self.host, {"NETRC": netrc_path}))
        self.assertIsNone(gr.netrc_auth(self.host, {"NETRC": self.no_netrc}))
        self.assertEqual(gr.netrc_auth(self.host, {"NETRC": self.write_netrc()}), ("admin", "s3cret"))

    def test_env_host_is_used_when_no_flag(self):
        self.route("GET", "/changes/5/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT", {"_number": 5})
        code, _out, err = self.run_cli("detail", "5", env={"GERRIT_HOST": self.host + "/"})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["path"], "/changes/5/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT")

    def test_flag_host_beats_env_host(self):
        self.route("GET", "/changes/5/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT", {"_number": 5})
        code, _out, _err = self.run_cli("--host", self.host, "detail", "5",
                                        env={"GERRIT_HOST": "http://127.0.0.1:1"})
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)

    def test_verbose_prints_method_and_url(self):
        self.route("GET", "/changes/123/revisions/current/related", {"changes": []})
        code, _out, err = self.run_cli("--host", self.host, "-v", "related", "123")
        self.assertEqual(code, 0)
        self.assertIn(f"GET {self.host}/changes/123/revisions/current/related", err)


class ErrorTests(StubServerCase):
    def test_http_404_exits_1_with_message(self):
        code, out, err = self.run_cli("--host", self.host, "related", "999")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("HTTP 404 Not Found", err)
        self.assertIn("Not found: /changes/999/revisions/current/related", err)

    def test_http_error_body_is_truncated_to_300_chars(self):
        self.route("GET", "/changes/1/revisions/current/related", ["x" * 1000], status=409)
        code, _out, err = self.run_cli("--host", self.host, "related", "1")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 409", err)
        body_line = [line for line in err.splitlines() if line.startswith(")]}'") or line.startswith("[")]
        self.assertTrue(body_line and len(body_line[-1]) <= 300, err)

    def test_network_error_exits_1(self):
        dead = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        dead_port = dead.server_address[1]
        dead.server_close()
        code, _out, err = self.run_cli("--host", f"http://127.0.0.1:{dead_port}", "related", "1")
        self.assertEqual(code, 1)
        self.assertIn("network error", err)

    def test_usage_errors_exit_2(self):
        code, _out, err = self.run_cli("--host", self.host, "related", "not-a-change")
        self.assertEqual(code, 2)
        self.assertIn("unrecognised change reference", err)
        code, _out, _err = self.run_cli("--host", self.host)
        self.assertEqual(code, 2)
        code, _out, err = self.run_cli("--host", self.host, "hashtags", "1")
        self.assertEqual(code, 2)
        self.assertIn("--add", err)
        code, _out, err = self.run_cli("--host", self.host, "review-metrics", "--owner", "a", "--since", "yesterday")
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)
        self.assertEqual(len(self.requests), 0)

    def test_no_host_anywhere_exits_2(self):
        original = gr.git_config
        gr.git_config = lambda key, cwd=None: None
        try:
            code, _out, err = self.run_cli("related", "1")
        finally:
            gr.git_config = original
        self.assertEqual(code, 2)
        self.assertIn("no Gerrit host", err)


# ---------------------------------------------------------------- subcommands


RELATED = {
    "changes": [
        {"project": "demo", "change_id": "I" + "c" * 40, "_change_number": 124, "_revision_number": 1,
         "_current_revision_number": 1, "status": "NEW", "commit": {"commit": "c" * 40, "subject": "third"}},
        {"project": "demo", "change_id": CHANGE_ID, "_change_number": 123, "_revision_number": 2,
         "_current_revision_number": 2, "status": "NEW", "commit": {"commit": "b" * 40, "subject": "second"}},
        {"project": "demo", "change_id": "I" + "a" * 40, "_change_number": 122, "_revision_number": 1,
         "_current_revision_number": 1, "status": "MERGED", "commit": {"commit": "a" * 40, "subject": "first"}},
    ]
}

COMMENTS = {
    "src/a.py": [
        {"id": "c2", "patch_set": 1, "line": 20, "updated": "2026-09-02 10:00:00.000000000",
         "message": "praise: nice", "unresolved": False, "author": {"_account_id": 2, "name": "Rena"}},
        {"id": "c1", "patch_set": 1, "line": 10, "updated": "2026-09-01 10:00:00.000000000",
         "message": "suggestion: rename this\nto something clearer " + "x" * 100, "unresolved": True,
         "author": {"_account_id": 2, "name": "Rena"}},
        {"id": "c3", "patch_set": 1, "line": 10, "in_reply_to": "c1", "updated": "2026-09-03 10:00:00.000000000",
         "message": "Done.", "unresolved": False, "author": {"_account_id": 1, "name": "Admin"}},
    ],
    "/COMMIT_MSG": [
        {"id": "c4", "patch_set": 1, "updated": "2026-09-01 09:00:00.000000000", "message": "nit: subject",
         "unresolved": True, "author": {"_account_id": 2, "username": "rena"}},
    ],
}


class SubcommandTests(StubServerCase):
    def test_related_table_marks_requested_change(self):
        self.route("GET", "/changes/123/revisions/current/related", RELATED)
        code, out, err = self.run_cli("--host", self.host, "--table", "related", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["method"], "GET")
        self.assertIn("related: 3 change(s)", out)
        lines = out.splitlines()
        self.assertTrue(lines[2].startswith("*") and "123" in lines[2] and "second" in lines[2], out)
        self.assertTrue(lines[1].startswith(" ") and "124" in lines[1], out)
        self.assertIn("MERGED", lines[3])

    def test_related_accepts_url_and_change_id(self):
        self.route("GET", f"/changes/demo~master~{CHANGE_ID}/revisions/current/related", RELATED)
        code, out, _err = self.run_cli("--host", self.host, "--table", "related", f"demo~master~{CHANGE_ID}")
        self.assertEqual(code, 0)
        self.assertTrue([line for line in out.splitlines() if line.startswith("*") and "123" in line], out)
        self.route("GET", "/changes/123/revisions/current/related", RELATED)
        code, _out, _err = self.run_cli("--host", self.host, "related", "https://h/c/demo/+/123")
        self.assertEqual(code, 0)
        self.assertEqual(self.last["path"], "/changes/123/revisions/current/related")

    def test_related_json_prints_raw(self):
        self.route("GET", "/changes/123/revisions/current/related", RELATED)
        code, out, _err = self.run_cli("--host", self.host, "--json", "related", "123")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), RELATED)
        code, out, _err = self.run_cli("--host", self.host, "related", "123", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), RELATED)

    def test_default_output_is_json(self):
        """No --json/--table given: the interface contract says JSON to stdout by default."""
        self.route("GET", "/changes/123/revisions/current/related", RELATED)
        code, out, _err = self.run_cli("--host", self.host, "related", "123")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), RELATED)
        self.route("PUT", "/changes/123/topic", "stack-a")
        code, out, _err = self.run_cli("--host", self.host, "topic", "123", "stack-a")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), "stack-a")

    def test_comments_rows(self):
        self.route("GET", "/changes/123/comments", COMMENTS)
        code, out, err = self.run_cli("--host", self.host, "--table", "comments", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["method"], "GET")
        lines = out.splitlines()
        self.assertEqual(lines[0], "path | line | author | unresolved | id | in_reply_to | message")
        self.assertEqual(lines[1], "/COMMIT_MSG | - | rena | yes | c4 | - | nit: subject")
        self.assertTrue(lines[2].startswith("src/a.py | 10 | Rena | yes | c1 | - | suggestion: rename this to"), lines[2])
        self.assertLessEqual(len(lines[2].split(" | ")[-1]), 80)
        self.assertEqual(lines[3], "src/a.py | 10 | Admin | no | c3 | c1 | Done.")
        self.assertEqual(lines[4], "src/a.py | 20 | Rena | no | c2 | - | praise: nice")

    def test_comments_unresolved_filter_and_json(self):
        self.route("GET", "/changes/123/comments", COMMENTS)
        code, out, _err = self.run_cli("--host", self.host, "--table", "comments", "123", "--unresolved")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 3)
        self.assertNotIn("Done.", out)
        code, out, _err = self.run_cli("--host", self.host, "--json", "comments", "123", "--unresolved")
        data = json.loads(out)
        self.assertEqual(sorted(data), ["/COMMIT_MSG", "src/a.py"])
        self.assertEqual([c["id"] for c in data["src/a.py"]], ["c1"])

    def test_review_posts_message_and_nested_comments_without_labels(self):
        self.route("POST", "/changes/123/revisions/current/review", {"labels": {}})
        code, out, err = self.run_cli(
            "--host", self.host, "--table", "review", "123", "--message", "Done.",
            "--comment", "src/a.py:10:nit: rename", "--comment", "src/a.py:20:praise: nice",
            "--comment", "src/b.py:0:question: why?", "--in-reply-to", "c1",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["method"], "POST")
        self.assertEqual(self.last["path"], "/changes/123/revisions/current/review")
        self.assertIn("application/json", self.last["headers"]["content-type"])
        body = self.last["json"]
        self.assertNotIn("labels", body)
        self.assertEqual(body["message"], "Done.")
        self.assertEqual(
            body["comments"],
            {
                "src/a.py": [
                    {"line": 10, "message": "nit: rename", "unresolved": True, "in_reply_to": "c1"},
                    {"line": 20, "message": "praise: nice", "unresolved": True, "in_reply_to": "c1"},
                ],
                "src/b.py": [{"message": "question: why?", "unresolved": True, "in_reply_to": "c1"}],
            },
        )
        self.assertIn("review posted on 123: message=yes comments=3 unresolved=yes", out)

    def test_review_resolved_flag(self):
        self.route("POST", "/changes/123/revisions/current/review", {})
        code, _out, _err = self.run_cli("--host", self.host, "review", "123", "-m", "Fixed.",
                                        "--comment", "src/a.py:10:Done.", "--resolved")
        self.assertEqual(code, 0)
        entry = self.last["json"]["comments"]["src/a.py"][0]
        self.assertFalse(entry["unresolved"])
        self.assertNotIn("labels", self.last["json"])

    def test_review_requires_message(self):
        code, _out, _err = self.run_cli("--host", self.host, "review", "123", "--comment", "a.py:1:x")
        self.assertEqual(code, 2)
        self.assertEqual(len(self.requests), 0)

    def test_rebase_chain(self):
        payload = {"rebased_changes": [{"_number": 123, "status": "NEW", "project": "demo", "branch": "master",
                                        "subject": "second"}], "contains_git_conflicts": False}
        self.route("POST", "/changes/123/rebase:chain", payload)
        code, out, err = self.run_cli("--host", self.host, "--table", "rebase-chain", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.last["method"], self.last["path"]), ("POST", "/changes/123/rebase:chain"))
        self.assertEqual(self.last["json"], {})
        self.assertIn("rebased: 1 change(s)", out)
        code, _out, _err = self.run_cli("--host", self.host, "rebase-chain", "123", "--base", "abc123")
        self.assertEqual(code, 0)
        self.assertEqual(self.last["json"], {"base": "abc123"})

    def test_topic(self):
        self.route("PUT", "/changes/123/topic", "stack-a")
        code, out, err = self.run_cli("--host", self.host, "--table", "topic", "123", "stack-a")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.last["method"], self.last["path"]), ("PUT", "/changes/123/topic"))
        self.assertEqual(self.last["json"], {"topic": "stack-a"})
        self.assertEqual(out.strip(), "topic: stack-a")

    def test_topic_removal_handles_empty_response(self):
        self.route("PUT", "/changes/123/topic", None, status=204)
        code, out, _err = self.run_cli("--host", self.host, "--table", "topic", "123", "")
        self.assertEqual(code, 0)
        self.assertEqual(self.last["json"], {"topic": ""})
        self.assertEqual(out.strip(), "topic: (removed)")

    def test_hashtags(self):
        self.route("POST", "/changes/123/hashtags", ["a", "b"])
        code, out, err = self.run_cli("--host", self.host, "--table", "hashtags", "123", "--add", "a", "--add", "b",
                                      "--remove", "old")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.last["method"], self.last["path"]), ("POST", "/changes/123/hashtags"))
        self.assertEqual(self.last["json"], {"add": ["a", "b"], "remove": ["old"]})
        self.assertEqual(out.strip(), "hashtags: a, b")
        code, _out, _err = self.run_cli("--host", self.host, "hashtags", "123", "--remove", "old")
        self.assertEqual(self.last["json"], {"remove": ["old"]})

    def test_submitted_together(self):
        payload = {"changes": [{"_number": 123, "status": "NEW", "project": "demo", "branch": "master",
                                "subject": "second"}], "non_visible_changes": 1}
        self.route("GET", "/changes/123/submitted_together?o=NON_VISIBLE_CHANGES", payload)
        code, out, err = self.run_cli("--host", self.host, "--table", "submitted-together", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["method"], "GET")
        self.assertEqual(self.last["path"], "/changes/123/submitted_together?o=NON_VISIBLE_CHANGES")
        self.assertIn("submitted together: 1 change(s), 1 not visible", out)
        self.assertIn("demo/master  second", out)

    def test_detail(self):
        rev = "b" * 40
        payload = {
            "_number": 123, "project": "demo", "branch": "master", "status": "NEW", "subject": "second",
            "change_id": CHANGE_ID, "topic": "stack-a", "hashtags": ["h1"], "updated": "2026-09-02 10:00:00.0",
            "owner": {"_account_id": 1, "name": "Admin"}, "current_revision": rev,
            "revisions": {rev: {"_number": 2}},
            "labels": {"Code-Review": {"all": [{"_account_id": 2, "name": "Rena", "value": 1}]},
                       "Verified": {"all": []}},
            "reviewers": {"REVIEWER": [{"_account_id": 2, "name": "Rena"}]},
        }
        self.route("GET", "/changes/123/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT", payload)
        code, out, err = self.run_cli("--host", self.host, "--table", "detail", "123")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["path"], "/changes/123/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT")
        self.assertIn("change 123  demo/master  NEW", out)
        self.assertIn("revision: bbbbbbbbbb (ps 2)", out)
        self.assertIn("Code-Review: +1 (Rena)", out)
        self.assertIn("Verified: (no votes)", out)
        self.assertIn("reviewer: Rena", out)
        code, out, _err = self.run_cli("--host", self.host, "--json", "detail", "123")
        self.assertEqual(json.loads(out), payload)

    def test_query(self):
        payload = [{"_number": 7, "status": "NEW", "project": "demo", "branch": "master", "subject": "s"}]
        self.route("GET", "/changes/?q=owner%3Aself+status%3Aopen&n=5&o=CURRENT_REVISION", payload)
        code, out, err = self.run_cli("--host", self.host, "--table", "query", "owner:self status:open",
                                      "--limit", "5")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.last["path"], "/changes/?q=owner%3Aself+status%3Aopen&n=5&o=CURRENT_REVISION")
        self.assertIn("      7  NEW       demo/master  s", out)
        self.route("GET", "/changes/?q=x&n=25&o=CURRENT_REVISION", [])
        code, out, _err = self.run_cli("--host", self.host, "--table", "query", "x")
        self.assertEqual(code, 0)
        self.assertEqual(self.last["path"], "/changes/?q=x&n=25&o=CURRENT_REVISION")
        self.assertEqual(out.strip(), "(no changes)")


# ---------------------------------------------------------------- review-metrics


def _change(number, created, status, submitted=None, insertions=0, deletions=0, revisions=1, messages=()):
    return {
        "_number": number, "subject": f"change {number}", "status": status, "created": created,
        "updated": submitted or created, "submitted": submitted, "insertions": insertions,
        "deletions": deletions, "owner": {"_account_id": 1000000, "name": "Admin"},
        "revisions": {f"{number}{i}" * 8: {"_number": i + 1} for i in range(revisions)},
        "messages": list(messages),
    }


def _msg(account, date, tag=None, message="Patch Set 1: Code-Review+1"):
    msg = {"author": {"_account_id": account}, "date": date, "message": message}
    if tag:
        msg["tag"] = tag
    return msg


METRICS_CHANGES = [
    _change(101, "2026-09-01 10:00:00.000000000", "MERGED", submitted="2026-09-02 10:00:00.000000000",
            insertions=30, deletions=5, revisions=2, messages=[
                _msg(1000000, "2026-09-01 10:00:00.000000000", tag="autogenerated:gerrit:newPatchSet"),
                _msg(1000002, "2026-09-01 10:30:00.000000000", tag="autogenerated:ci", message="Build ok"),
                _msg(1000001, "2026-09-01 13:00:00.000000000"),
                _msg(1000001, "2026-09-01 12:00:00.000000000"),
                _msg(1000000, "2026-09-01 11:00:00.000000000", message="owner reply"),
            ]),
    _change(102, "2026-09-03 00:00:00.000000000", "NEW", insertions=10, deletions=0, revisions=1, messages=[
        _msg(1000000, "2026-09-03 00:00:00.000000000", tag="autogenerated:gerrit:newPatchSet"),
        _msg(1000000, "2026-09-03 01:00:00.000000000", message="self note"),
    ]),
    _change(103, "2026-09-04 00:00:00.000000000", "MERGED", submitted="2026-09-04 06:00:00.000000000",
            insertions=100, deletions=20, revisions=3, messages=[
                _msg(1000001, "2026-09-04 01:00:00.000000000"),
            ]),
]


class ReviewMetricsTests(StubServerCase):
    def setUp(self):
        super().setUp()
        self.route("GET", r"/changes/\?q=.*", METRICS_CHANGES, regex=True)
        self.route("GET", "/changes/101/comments", {"a.py": [{"id": "1"}, {"id": "2"}]})
        self.route("GET", "/changes/102/comments", {})
        self.route("GET", "/changes/103/comments", {"a.py": [{"id": "1"}], "b.py": [{"id": "2"}, {"id": "3"}, {"id": "4"}]})
        self.route("GET", "/changes/101/revisions/current/related", {"changes": [{"_change_number": 101}, {"_change_number": 100}]})
        self.route("GET", "/changes/102/revisions/current/related", {"changes": []})
        self.route("GET", "/changes/103/revisions/current/related",
                   {"changes": [{"_change_number": 103}, {"_change_number": 102}, {"_change_number": 101}]})

    def test_query_path_and_request_budget(self):
        code, _out, err = self.run_cli("--host", self.host, "review-metrics", "--owner", "admin",
                                       "--since", "2026-01-01", "--project", "demo")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.requests[0]["path"],
            "/changes/?q=owner%3Aadmin+since%3A2026-01-01+project%3Ademo&n=50"
            "&o=MESSAGES&o=DETAILED_ACCOUNTS&o=ALL_REVISIONS&o=CURRENT_COMMIT",
        )
        self.assertEqual(len(self.requests), 1 + 2 * len(METRICS_CHANGES))
        paths = sorted(r["path"] for r in self.requests[1:])
        self.assertEqual(paths.count("/changes/101/comments"), 1)
        self.assertEqual(paths.count("/changes/103/revisions/current/related"), 1)

    def test_rows_and_summary(self):
        code, out, err = self.run_cli("--host", self.host, "review-metrics", "--owner", "admin",
                                      "--since", "2026-01-01", "--limit", "10")
        self.assertEqual(code, 0, err)
        self.assertIn("&n=10&", self.requests[0]["path"])
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), len(METRICS_CHANGES) + 1)
        rows = [json.loads(line) for line in lines[:-1]]
        summary = json.loads(lines[-1])["summary"]

        r101, r102, r103 = rows
        self.assertEqual(
            {k: r101[k] for k in ("number", "status", "insertions", "deletions", "patchsets", "comments", "in_chain")},
            {"number": 101, "status": "MERGED", "insertions": 30, "deletions": 5, "patchsets": 2,
             "comments": 2, "in_chain": True},
        )
        self.assertEqual(r101["first_review_at"], "2026-09-01 12:00:00.000000000")
        self.assertEqual(r101["time_to_first_review_h"], 2.0)
        self.assertEqual(r101["time_to_merge_h"], 24.0)
        self.assertEqual(r101["submitted"], "2026-09-02 10:00:00.000000000")

        self.assertIsNone(r102["first_review_at"])
        self.assertIsNone(r102["time_to_first_review_h"])
        self.assertIsNone(r102["time_to_merge_h"])
        self.assertIsNone(r102["submitted"])
        self.assertEqual((r102["comments"], r102["patchsets"], r102["in_chain"]), (0, 1, False))

        self.assertEqual((r103["time_to_first_review_h"], r103["time_to_merge_h"]), (1.0, 6.0))
        self.assertEqual((r103["comments"], r103["patchsets"], r103["in_chain"]), (4, 3, True))

        self.assertEqual(
            summary["all"],
            {"count": 3, "merged": 2, "median_lines": 35.0, "median_patchsets": 2.0, "median_comments": 2.0,
             "median_time_to_first_review_h": 1.5, "median_time_to_merge_h": 15.0},
        )
        self.assertEqual(summary["chain"]["count"], 2)
        self.assertEqual(summary["chain"]["median_time_to_merge_h"], 15.0)
        self.assertEqual(summary["solo"]["count"], 1)
        self.assertIsNone(summary["solo"]["median_time_to_merge_h"])
        self.assertEqual(summary["solo"]["median_lines"], 10.0)
        for row in rows:
            self.assertEqual(
                sorted(row),
                sorted(["number", "subject", "status", "created", "updated", "submitted", "insertions",
                        "deletions", "patchsets", "comments", "first_review_at", "time_to_first_review_h",
                        "time_to_merge_h", "in_chain"]),
            )

    def test_json_and_table_modes(self):
        code, out, _err = self.run_cli("--host", self.host, "--json", "review-metrics", "--owner", "admin",
                                       "--since", "2026-01-01")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["query"], "owner:admin since:2026-01-01")
        self.assertEqual(len(doc["changes"]), 3)
        self.assertEqual(doc["summary"]["all"]["count"], 3)
        code, out, _err = self.run_cli("--host", self.host, "--table", "review-metrics", "--owner", "admin",
                                       "--since", "2026-01-01")
        self.assertEqual(code, 0)
        self.assertIn("summary all: count=3 merged=2", out)
        self.assertIn("   101  MERGED      2         2  +30/-5          2.0    24.0  yes", out)

    def test_hours_helpers(self):
        self.assertEqual(gr.hours_between("2026-01-01 00:00:00.000000000", "2026-01-01 01:30:00.000000000"), 1.5)
        self.assertIsNone(gr.hours_between("2026-01-01 00:00:00", None))
        self.assertIsNone(gr.hours_between("garbage", "2026-01-01 00:00:00"))
        self.assertEqual(gr.summarise([])["all"]["count"], 0)
        self.assertIsNone(gr.summarise([])["all"]["median_lines"])


if __name__ == "__main__":
    unittest.main()
