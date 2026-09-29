#!/usr/bin/env python3
"""gerrit-rest-stub.py — a tiny canned Gerrit REST server for eval fixtures.

Serves the handful of endpoints the gerrit-review / gerrit-stack skills reach
through `scripts/gerrit-rest.py` (the MCP fallback), on 127.0.0.1 and a free
port, with Gerrit's XSSI prefix `)]}'\\n` and both authenticated (`/a/...`)
and anonymous paths:

  GET  /changes/<n>/revisions/current/related   relation chain 1 -> 2 -> 3
  GET  /changes/<n>/comments                    one unresolved thread by rena
  GET  /changes/<n>/detail | /changes/<n>       ChangeInfo (no `labels` key)
  GET  /changes/?q=...                          the three changes
  GET  /config/server/version                   "3.14.4"
  GET  /accounts/self                           the eval author
  POST /changes/<n>/revisions/<r>/review        recorded -> review-posts.jsonl
  PUT  /changes/<n>/topic                       recorded -> review-posts.jsonl
  POST /changes/<n>/hashtags                    recorded -> review-posts.jsonl
  PUT  /changes/<n>/revisions/<r>/drafts, POST …/drafts:publish  recorded too

Options:
  --port N          listen port (0 = pick a free one; default 0)
  --dir DIR         where to write .stub-port, .stub-pid, review-posts.jsonl
                    (default: cwd)
  --change-ids A,B,C  Change-Ids for changes 1..3 (default: synthetic)
  --subjects S1|S2|S3 subjects for changes 1..3 (pipe separated)
  --project NAME    project name (default: demo)

Runs until killed (SIGTERM/SIGINT -> clean exit). stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

XSSI = ")]}'\n"

DEFAULT_CHANGE_IDS = [
    "I1111111111111111111111111111111111111111",
    "I2222222222222222222222222222222222222222",
    "I3333333333333333333333333333333333333333",
]
DEFAULT_SUBJECTS = [
    "feat: add greet.conf with the greeting prefix",
    "feat: read the greeting prefix from greet.conf",
    "test: cover a configured greeting prefix",
]
COMMITS = [
    "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
    "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2",
    "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3",
]
ROOT_COMMIT = "0000000000000000000000000000000000000001"

RENA = {"_account_id": 1000001, "name": "Rena Reviewer", "email": "rena@example.com", "username": "rena"}
AUTHOR = {"_account_id": 1000000, "name": "Eval Author", "email": "eval@example.com", "username": "admin"}

REVIEW_COMMENT = (
    "issue (blocking): the prefix is read on every call; cache it and add a test for the default"
)


class Canned:
    def __init__(self, project: str, change_ids: list[str], subjects: list[str]):
        self.project = project
        self.change_ids = change_ids
        self.subjects = subjects

    def _number(self, token: str) -> int | None:
        """Change identifier -> number 1..3 (accepts numbers, Change-Ids, project~branch~Id)."""
        token = token.split("~")[-1]
        if token.isdigit():
            n = int(token)
            return n if 1 <= n <= len(self.change_ids) else None
        for i, cid in enumerate(self.change_ids):
            if token == cid:
                return i + 1
        return None

    def change_info(self, n: int) -> dict:
        i = n - 1
        return {
            "id": f"{self.project}~master~{self.change_ids[i]}",
            "project": self.project,
            "branch": "master",
            "change_id": self.change_ids[i],
            "subject": self.subjects[i],
            "status": "NEW",
            "created": "2026-09-29 10:00:00.000000000",
            "updated": "2026-09-29 12:00:00.000000000",
            "insertions": 6,
            "deletions": 2,
            "unresolved_comment_count": 1 if n == 2 else 0,
            "has_review_started": True,
            "owner": AUTHOR,
            "current_revision": COMMITS[i],
            "revisions": {
                COMMITS[i]: {
                    "kind": "REWORK",
                    "_number": 1,
                    "created": "2026-09-29 10:00:00.000000000",
                    "uploader": AUTHOR,
                    "ref": f"refs/changes/0{n}/{n}/1",
                    "commit": self.commit_info(n),
                }
            },
            "_number": n,
        }

    def commit_info(self, n: int) -> dict:
        i = n - 1
        parent = ROOT_COMMIT if i == 0 else COMMITS[i - 1]
        parent_subject = "chore: initial import" if i == 0 else self.subjects[i - 1]
        return {
            "commit": COMMITS[i],
            "parents": [{"commit": parent, "subject": parent_subject}],
            "author": {"name": AUTHOR["name"], "email": AUTHOR["email"], "date": "2026-09-29 10:00:00.000000000", "tz": 0},
            "committer": {"name": AUTHOR["name"], "email": AUTHOR["email"], "date": "2026-09-29 10:00:00.000000000", "tz": 0},
            "subject": self.subjects[i],
            "message": f"{self.subjects[i]}\n\nChange-Id: {self.change_ids[i]}\n",
        }

    def related(self, n: int) -> dict:
        # Gerrit lists the chain newest first; every entry carries the commit + parents.
        changes = []
        for k in range(len(self.change_ids), 0, -1):
            info = self.commit_info(k)
            changes.append(
                {
                    "project": self.project,
                    "change_id": self.change_ids[k - 1],
                    "commit": info,
                    "_change_number": k,
                    "_revision_number": 1,
                    "_current_revision_number": 1,
                    "status": "NEW",
                }
            )
        return {"changes": changes}

    def comments(self, n: int) -> dict:
        if n != 2:
            return {}
        return {
            "greet.sh": [
                {
                    "id": "c0ffee01-rena-0001",
                    "patch_set": 1,
                    "line": 3,
                    "range": {"start_line": 3, "start_character": 0, "end_line": 3, "end_character": 60},
                    "message": REVIEW_COMMENT,
                    "author": RENA,
                    "updated": "2026-09-29 12:00:00.000000000",
                    "unresolved": True,
                    "commit_id": COMMITS[1],
                    "change_message_id": "msg-rena-0001",
                }
            ]
        }

    def query(self, q: str) -> list:
        return [self.change_info(n) for n in range(1, len(self.change_ids) + 1)]


class Handler(BaseHTTPRequestHandler):
    canned: Canned
    out_dir: str
    lock = threading.Lock()

    # ---- helpers ------------------------------------------------------
    def log_message(self, fmt, *args):  # quiet by default; stderr on STUB_VERBOSE
        if os.environ.get("STUB_VERBOSE"):
            sys.stderr.write("stub: " + (fmt % args) + "\n")

    def _send(self, status: int, payload=None, raw: str | None = None):
        body = raw if raw is not None else XSSI + json.dumps(payload)
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _path_parts(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/a/"):
            path = path[2:]
        parts = [p for p in path.split("/") if p]
        return parts, parse_qs(parsed.query)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw

    def _record(self, parts, body):
        rec = {"ts": time.time(), "method": self.command, "path": "/" + "/".join(parts), "body": body}
        with self.lock:
            with open(os.path.join(self.out_dir, "review-posts.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    # ---- verbs --------------------------------------------------------
    def do_GET(self):
        parts, query = self._path_parts()
        c = self.canned
        if parts == ["config", "server", "version"]:
            return self._send(200, "3.14.4")
        if parts == ["accounts", "self"]:
            return self._send(200, AUTHOR)
        if parts and parts[0] == "changes":
            if len(parts) == 1:
                return self._send(200, c.query((query.get("q") or [""])[0]))
            n = c._number(parts[1])
            if n is None:
                return self._send(404, raw="Not found: " + parts[1])
            rest = parts[2:]
            if rest == [] or rest == ["detail"]:
                return self._send(200, c.change_info(n))
            if rest == ["comments"]:
                return self._send(200, c.comments(n))
            if rest == ["drafts"]:
                return self._send(200, {})
            if rest == ["submitted_together"]:
                return self._send(200, [c.change_info(k) for k in range(1, n + 1)])
            if rest == ["topic"]:
                return self._send(200, "")
            if rest == ["hashtags"]:
                return self._send(200, [])
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "related":
                return self._send(200, c.related(n))
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "commit":
                return self._send(200, c.commit_info(n))
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "comments":
                return self._send(200, c.comments(n))
        return self._send(404, raw="Not found: " + self.path)

    def do_POST(self):
        parts, _ = self._path_parts()
        body = self._read_body()
        if len(parts) >= 2 and parts[0] == "changes":
            n = self.canned._number(parts[1])
            if n is None:
                return self._send(404, raw="Not found: " + parts[1])
            rest = parts[2:]
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "review":
                self._record(parts, body)
                return self._send(200, {"ready": True})
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "drafts:publish":
                self._record(parts, body)
                return self._send(204, raw="")
            if rest == ["hashtags"]:
                self._record(parts, body)
                add = (body or {}).get("add", []) if isinstance(body, dict) else []
                return self._send(200, list(add))
            if rest == ["rebase:chain"]:
                self._record(parts, body)
                return self._send(200, {"rebased_changes": [self.canned.change_info(k) for k in range(1, n + 1)]})
        return self._send(404, raw="Not found: " + self.path)

    def do_PUT(self):
        parts, _ = self._path_parts()
        body = self._read_body()
        if len(parts) >= 2 and parts[0] == "changes":
            n = self.canned._number(parts[1])
            if n is None:
                return self._send(404, raw="Not found: " + parts[1])
            rest = parts[2:]
            if rest == ["topic"]:
                self._record(parts, body)
                topic = body.get("topic", "") if isinstance(body, dict) else ""
                return self._send(200, topic)
            if len(rest) == 3 and rest[0] == "revisions" and rest[2] == "drafts":
                self._record(parts, body)
                draft = dict(body) if isinstance(body, dict) else {}
                draft.setdefault("id", "draft-%d" % int(time.time() * 1000))
                return self._send(201, draft)
        return self._send(404, raw="Not found: " + self.path)

    def do_DELETE(self):
        parts, _ = self._path_parts()
        if len(parts) >= 3 and parts[0] == "changes" and parts[2] == "topic":
            self._record(parts, None)
            return self._send(204, raw="")
        return self._send(404, raw="Not found: " + self.path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--change-ids", default=",".join(DEFAULT_CHANGE_IDS))
    ap.add_argument("--subjects", default="|".join(DEFAULT_SUBJECTS))
    ap.add_argument("--project", default="demo")
    args = ap.parse_args(argv)

    change_ids = [c.strip() for c in args.change_ids.split(",") if c.strip()] or DEFAULT_CHANGE_IDS
    subjects = [s.strip() for s in args.subjects.split("|") if s.strip()] or DEFAULT_SUBJECTS
    while len(subjects) < len(change_ids):
        subjects.append(DEFAULT_SUBJECTS[len(subjects) % len(DEFAULT_SUBJECTS)])
    while len(change_ids) < 3:
        change_ids.append(DEFAULT_CHANGE_IDS[len(change_ids)])
    change_ids = change_ids[:3]
    subjects = subjects[:3]

    out_dir = os.path.abspath(args.dir)
    os.makedirs(out_dir, exist_ok=True)
    Handler.canned = Canned(args.project, change_ids, subjects)
    Handler.out_dir = out_dir

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = server.server_address[1]
    with open(os.path.join(out_dir, ".stub-port"), "w", encoding="utf-8") as fh:
        fh.write(str(port) + "\n")
    with open(os.path.join(out_dir, ".stub-pid"), "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()) + "\n")
    open(os.path.join(out_dir, "review-posts.jsonl"), "a", encoding="utf-8").close()

    def stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"gerrit-rest-stub: http://127.0.0.1:{port} (pid {os.getpid()}, dir {out_dir})", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
