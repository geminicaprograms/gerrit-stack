#!/usr/bin/env python3
"""gerrit-rest.py - minimal Gerrit REST client; the MCP fallback used by gerrit-stack.

Stdlib only. Every response has the XSSI prefix ()]}') stripped and is parsed as JSON.

  gerrit-rest.py [--host URL] [--json|--table] [--anonymous] [-v] <cmd> ...

Commands
  related <change>                  GET  /changes/{c}/revisions/current/related
  comments <change> [--unresolved]  GET  /changes/{c}/comments
  review <change> --message M [--comment FILE:LINE:MSG]... [--in-reply-to ID] [--resolved]
                                    POST /changes/{c}/revisions/current/review  (never sends labels)
  rebase-chain <change> [--base X]  POST /changes/{c}/rebase:chain
  topic <change> <topic>            PUT  /changes/{c}/topic
  hashtags <change> --add T... [--remove T]...
                                    POST /changes/{c}/hashtags
  submitted-together <change>       GET  /changes/{c}/submitted_together?o=NON_VISIBLE_CHANGES
  detail <change>                   GET  /changes/{c}/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT
  query <q> [--limit N]             GET  /changes/?q=...&n=N&o=CURRENT_REVISION
  review-metrics --owner U --since YYYY-MM-DD [--project P] [--limit N]
                                    per-change review metrics as JSON rows + a summary of medians

<change> may be a change number, a full Change-Id (I + 40 hex), project~branch~Change-Id,
or a Gerrit URL (.../c/<project>/+/<n>[/...], .../+/<n>, or legacy .../#/c/<n>/).

Host resolution: --host, else $GERRIT_HOST, else `git config gerrit-stack.host`, else derived
from an http(s) remote.origin.url (project path and /a/ stripped); otherwise exit 2.
Auth: the host's entry in ~/.netrc ($NETRC overrides the path) -> HTTP Basic + "/a/" prefix
on every request; no entry, unreadable file, or --anonymous -> anonymous, no prefix.

Output: --table (default for everything except review-metrics, which defaults to JSON lines)
or --json (raw response, pretty-printed). -v prints "<METHOD> <URL>" to stderr.
Exit codes: 0 ok, 1 HTTP/network/response error, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import base64
import json
import netrc
import os
import re
import socket
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

XSSI_PREFIX = ")]}'"
TIMEOUT_S = 30
USER_AGENT = "gerrit-stack/gerrit-rest"
EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2
METRICS_OPTIONS = ("MESSAGES", "DETAILED_ACCOUNTS", "ALL_REVISIONS", "CURRENT_COMMIT")
COMMENT_COLUMNS = ("path", "line", "author", "unresolved", "id", "in_reply_to", "message")

NUMBER_RE = re.compile(r"^\d+$")
CHANGE_ID_RE = re.compile(r"^I[0-9a-fA-F]{40}$")
URL_CHANGE_RE = re.compile(r"/\+/(\d+)(?:[/?#]|$)")
URL_LEGACY_RE = re.compile(r"#/c/(\d+)(?:[/?]|$)")
HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
LOCAL_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UsageError(Exception):
    """Bad arguments or unresolvable configuration (exit 2)."""


class RestError(Exception):
    """Network failure or undecodable response (exit 1)."""


class HttpError(RestError):
    """Non-2xx HTTP response (exit 1)."""

    def __init__(self, code: int, reason: str, body: str) -> None:
        super().__init__(f"HTTP {code} {reason}")
        self.code, self.reason, self.body = code, reason, body


# ---------------------------------------------------------------- change refs


def parse_change_ref(raw: str) -> str:
    """Normalise a change reference into the {change-id} path segment Gerrit accepts."""
    ref = (raw or "").strip()
    if not ref:
        raise UsageError("empty change reference")
    if NUMBER_RE.match(ref) or CHANGE_ID_RE.match(ref):
        return ref
    if "://" in ref or ref.startswith("/"):
        match = URL_CHANGE_RE.search(ref) or URL_LEGACY_RE.search(ref)
        if match:
            return match.group(1)
        raise UsageError(f"no change number found in URL: {raw}")
    if "~" in ref:
        parts = ref.split("~")
        if len(parts) not in (2, 3) or not all(parts):
            raise UsageError(f"expected project~branch~Change-Id, got: {raw}")
        return "~".join(urllib.parse.quote(part, safe="") for part in parts)
    raise UsageError(
        f"unrecognised change reference {raw!r}: use a change number, a full Change-Id, "
        "project~branch~Change-Id, or a Gerrit change URL"
    )


def ref_matches(ref: str, change: Mapping[str, Any]) -> bool:
    """True when a RelatedChangeAndCommitInfo entry is the change the user asked for."""
    number = change.get("_change_number")
    if NUMBER_RE.match(ref):
        return number is not None and str(number) == ref
    wanted = urllib.parse.unquote(ref.split("~")[-1])
    change_id = change.get("change_id") or ""
    return bool(change_id) and change_id == wanted


# ---------------------------------------------------------------- host + auth


def git_config(key: str, cwd: Optional[str] = None) -> Optional[str]:
    """`git config --get <key>`; None on any failure (no git, not a repo, key unset)."""
    try:
        proc = subprocess.run(
            ["git", "config", "--get", key],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def host_from_remote_url(url: Optional[str]) -> Optional[str]:
    """http(s)://[user@]host[:port]/[prefix/]a/project -> http(s)://host[:port]/[prefix]."""
    if not url or not HTTP_SCHEME_RE.match(url.strip()):
        return None
    parts = urllib.parse.urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    netloc = parts.hostname + (f":{port}" if port else "")
    path = parts.path
    marker = path.find("/a/")
    if marker >= 0:
        path = path[:marker]
    elif path.endswith("/a"):
        path = path[:-2]
    else:
        path = ""  # cannot tell a server prefix from the project path: assume the root
    return f"{parts.scheme.lower()}://{netloc}{path.rstrip('/')}"


def normalise_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not HTTP_SCHEME_RE.match(host):
        scheme = "http" if LOCAL_HOST_RE.match(host) else "https"
        host = f"{scheme}://{host}"
    return host


def resolve_host(
    explicit: Optional[str],
    env: Mapping[str, str],
    cwd: Optional[str] = None,
    config: Optional[Callable[..., Optional[str]]] = None,
) -> str:
    """--host -> $GERRIT_HOST -> git config gerrit-stack.host -> http(s) remote.origin.url."""
    lookup = config or git_config
    candidates: Iterable[Callable[[], Optional[str]]] = (
        lambda: explicit,
        lambda: env.get("GERRIT_HOST"),
        lambda: lookup("gerrit-stack.host", cwd),
    )
    for get in candidates:
        value = get()
        if value and value.strip():
            return normalise_host(value)
    derived = host_from_remote_url(lookup("remote.origin.url", cwd))
    if derived:
        return derived
    raise UsageError(
        "no Gerrit host: pass --host URL, set GERRIT_HOST, or run "
        "`git config gerrit-stack.host <url>` (remote.origin.url is used only when it is http(s))"
    )


def netrc_auth(host: str, env: Mapping[str, str]) -> Optional[tuple[str, str]]:
    """(login, password) for the host's hostname from $NETRC or ~/.netrc; None when absent."""
    hostname = urllib.parse.urlsplit(host).hostname
    if not hostname:
        return None
    home = env.get("HOME") or os.path.expanduser("~")
    path = env.get("NETRC") or os.path.join(home, ".netrc")
    try:
        entry = netrc.netrc(path).authenticators(hostname)
    except (OSError, netrc.NetrcParseError, ValueError):
        return None
    if not entry:
        return None
    login, _account, password = entry
    if not login or not password:
        return None
    return login, password


# ---------------------------------------------------------------- HTTP client


def strip_xssi(text: str) -> str:
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):]
        if text.startswith("\n"):
            text = text[1:]
    return text


def decode_response(raw: bytes) -> Any:
    text = strip_xssi(raw.decode("utf-8", errors="replace"))
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RestError(f"response is not JSON ({exc}): {text[:300]!r}") from None


class Client:
    def __init__(
        self,
        host: str,
        auth: Optional[tuple[str, str]] = None,
        verbose: bool = False,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self.host = host.rstrip("/")
        self.auth = auth
        self.verbose = verbose
        self.timeout = timeout

    @property
    def prefix(self) -> str:
        return "/a" if self.auth else ""

    def url(self, path: str, query: Optional[Iterable[tuple[str, Any]]] = None) -> str:
        url = f"{self.host}{self.prefix}{path}"
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(list(query))
        return url

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: Optional[Iterable[tuple[str, Any]]] = None,
    ) -> Any:
        url = self.url(path, query)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        if self.auth:
            login, password = self.auth
            token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        if self.verbose:
            print(f"{method} {url}", file=sys.stderr)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                snippet = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - body is best effort
                snippet = ""
            raise HttpError(exc.code, str(exc.reason), snippet.strip()[:300]) from None
        except (urllib.error.URLError, socket.timeout, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RestError(f"network error for {method} {url}: {reason}") from None
        return decode_response(raw)


# ---------------------------------------------------------------- rendering


def fmt_account(acc: Optional[Mapping[str, Any]]) -> str:
    if not acc:
        return "?"
    return (
        acc.get("name")
        or acc.get("username")
        or acc.get("email")
        or str(acc.get("_account_id", "?"))
    )


def one_line(text: Optional[str], limit: int = 80) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def change_row(change: Mapping[str, Any]) -> str:
    return (
        f"{str(change.get('_number', '?')):>7}  {str(change.get('status', '?')):<9} "
        f"{change.get('project', '?')}/{change.get('branch', '?')}  {change.get('subject', '')}"
    )


def render_related(data: Any, ref: str) -> str:
    changes = (data or {}).get("changes") or []
    if not changes:
        return "(no related changes)"
    lines = [f"related: {len(changes)} change(s), newest first (* = {urllib.parse.unquote(ref)})"]
    for change in changes:
        mark = "*" if ref_matches(ref, change) else " "
        number = change.get("_change_number")
        rev, cur = change.get("_revision_number"), change.get("_current_revision_number")
        patchset = f"ps{rev}/{cur}" if rev is not None and cur is not None else ""
        subject = (change.get("commit") or {}).get("subject", "")
        lines.append(
            f"{mark} {str(number if number is not None else '?'):>7}  {patchset:<8} "
            f"{str(change.get('status', '?')):<9} {subject}"
        )
    return "\n".join(lines)


def filter_comments(data: Any, unresolved_only: bool) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for path, items in (data or {}).items():
        kept = [c for c in items if not unresolved_only or c.get("unresolved")]
        if kept:
            out[path] = kept
    return out


def flatten_comments(data: Any) -> list[dict[str, Any]]:
    rows = []
    for path, items in (data or {}).items():
        for comment in items:
            rows.append(
                {
                    "path": path,
                    "line": comment.get("line"),
                    "author": fmt_account(comment.get("author")),
                    "unresolved": bool(comment.get("unresolved")),
                    "id": comment.get("id") or "",
                    "in_reply_to": comment.get("in_reply_to") or "",
                    "updated": comment.get("updated") or "",
                    "message": comment.get("message") or "",
                }
            )
    rows.sort(key=lambda r: (r["path"], r["line"] or 0, r["updated"]))
    return rows


def render_comments(data: Any) -> str:
    rows = flatten_comments(data)
    if not rows:
        return "(no comments)"
    lines = [" | ".join(COMMENT_COLUMNS)]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    row["path"],
                    str(row["line"]) if row["line"] is not None else "-",
                    row["author"],
                    "yes" if row["unresolved"] else "no",
                    row["id"] or "-",
                    row["in_reply_to"] or "-",
                    one_line(row["message"]),
                ]
            )
        )
    return "\n".join(lines)


def render_rebase_chain(data: Any) -> str:
    info = data or {}
    changes = info.get("rebased_changes") or []
    lines = [f"rebased: {len(changes)} change(s)"] + [change_row(c) for c in changes]
    if info.get("contains_git_conflicts"):
        lines.append("warning: the rebase contains git conflicts")
    return "\n".join(lines)


def render_submitted_together(data: Any) -> str:
    if isinstance(data, list):
        changes, hidden = data, 0
    else:
        info = data or {}
        changes, hidden = info.get("changes") or [], int(info.get("non_visible_changes") or 0)
    head = f"submitted together: {len(changes)} change(s)"
    if hidden:
        head += f", {hidden} not visible to you"
    return "\n".join([head] + [change_row(c) for c in changes])


def render_detail(data: Any) -> str:
    change = data or {}
    lines = [
        f"change {change.get('_number', '?')}  {change.get('project', '?')}/{change.get('branch', '?')}  "
        f"{change.get('status', '?')}",
        f"subject:  {change.get('subject', '')}",
        f"owner:    {fmt_account(change.get('owner'))}   updated: {change.get('updated', '?')}",
    ]
    if change.get("topic"):
        lines.append(f"topic:    {change['topic']}")
    if change.get("hashtags"):
        lines.append("hashtags: " + ", ".join(change["hashtags"]))
    rev = change.get("current_revision")
    if rev:
        info = (change.get("revisions") or {}).get(rev) or {}
        lines.append(
            f"revision: {rev[:10]} (ps {info.get('_number', '?')})   Change-Id: {change.get('change_id', '?')}"
        )
    for label, info in (change.get("labels") or {}).items():
        votes = [
            f"{'+' if vote['value'] > 0 else ''}{vote['value']} ({fmt_account(vote)})"
            for vote in info.get("all") or []
            if vote.get("value")
        ]
        lines.append(f"label:    {label}: " + (", ".join(votes) if votes else "(no votes)"))
    for role, accounts in (change.get("reviewers") or {}).items():
        names = ", ".join(fmt_account(a) for a in accounts) or "(none)"
        lines.append(f"{role.lower() + ':':<10}{names}")
    return "\n".join(lines)


def render_query(data: Any) -> str:
    changes = data or []
    if not changes:
        return "(no changes)"
    return "\n".join(change_row(c) for c in changes)


def emit(args: argparse.Namespace, data: Any, render: Callable[[Any], str]) -> None:
    if args.fmt == "json":
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(render(data))


# ---------------------------------------------------------------- review input


def parse_comment_spec(spec: str) -> tuple[str, Optional[int], str]:
    """FILE:LINE:MESSAGE -> (path, line or None for a file-level comment, message)."""
    parts = spec.split(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[2]:
        raise UsageError(f"--comment expects FILE:LINE:MESSAGE, got {spec!r}")
    path, line_text, message = parts
    if line_text in ("", "0"):
        return path, None, message
    try:
        line = int(line_text)
    except ValueError:
        raise UsageError(f"--comment line must be an integer, got {line_text!r}") from None
    if line < 0:
        raise UsageError(f"--comment line must not be negative, got {line}")
    return path, line, message


def build_review_input(
    message: Optional[str],
    comment_specs: Iterable[str],
    in_reply_to: Optional[str] = None,
    resolved: bool = False,
) -> dict[str, Any]:
    """ReviewInput with message + comments only. Labels are never set by this tool."""
    body: dict[str, Any] = {}
    if message:
        body["message"] = message
    comments: dict[str, list[dict[str, Any]]] = {}
    for spec in comment_specs:
        path, line, text = parse_comment_spec(spec)
        entry: dict[str, Any] = {"message": text, "unresolved": not resolved}
        if line is not None:
            entry["line"] = line
        if in_reply_to:
            entry["in_reply_to"] = in_reply_to
        comments.setdefault(path, []).append(entry)
    if comments:
        body["comments"] = comments
    if in_reply_to and not comments:
        raise UsageError("--in-reply-to needs at least one --comment FILE:LINE:MSG to attach the reply to")
    if not body:
        raise UsageError("nothing to post: give --message and/or --comment")
    return body


# ---------------------------------------------------------------- review metrics


def parse_gerrit_ts(ts: Optional[str]) -> Optional[datetime]:
    """Gerrit timestamps look like '2026-09-29 10:11:12.000000000' (UTC)."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hours_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    a, b = parse_gerrit_ts(start), parse_gerrit_ts(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600, 2)


def first_review_at(change: Mapping[str, Any]) -> Optional[str]:
    """Date of the earliest message by a non-owner that is not autogenerated."""
    owner_id = (change.get("owner") or {}).get("_account_id")
    first = None
    for msg in change.get("messages") or []:
        if (msg.get("tag") or "").startswith("autogenerated:"):
            continue
        author = msg.get("author") or msg.get("real_author")
        if not author or author.get("_account_id") == owner_id:
            continue
        date = msg.get("date")
        if date and (first is None or date < first):
            first = date
    return first


def change_metrics(client: Client, change: Mapping[str, Any]) -> dict[str, Any]:
    number = change.get("_number")
    try:
        comments = client.request("GET", f"/changes/{number}/comments") or {}
        comment_count: Optional[int] = sum(len(v) for v in comments.values())
    except HttpError:
        comment_count = None
    try:
        related = client.request("GET", f"/changes/{number}/revisions/current/related") or {}
        in_chain: Optional[bool] = len(related.get("changes") or []) > 1
    except HttpError:
        in_chain = None
    created, submitted, first = change.get("created"), change.get("submitted"), first_review_at(change)
    return {
        "number": number,
        "subject": change.get("subject"),
        "status": change.get("status"),
        "created": created,
        "updated": change.get("updated"),
        "submitted": submitted,
        "insertions": change.get("insertions", 0),
        "deletions": change.get("deletions", 0),
        "patchsets": len(change.get("revisions") or {}),
        "comments": comment_count,
        "first_review_at": first,
        "time_to_first_review_h": hours_between(created, first),
        "time_to_merge_h": hours_between(created, submitted),
        "in_chain": in_chain,
    }


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return round(float(statistics.median(present)), 2) if present else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def block(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(subset),
            "merged": sum(1 for r in subset if r.get("status") == "MERGED"),
            "median_lines": _median((r["insertions"] or 0) + (r["deletions"] or 0) for r in subset),
            "median_patchsets": _median(r["patchsets"] for r in subset),
            "median_comments": _median(r["comments"] for r in subset),
            "median_time_to_first_review_h": _median(r["time_to_first_review_h"] for r in subset),
            "median_time_to_merge_h": _median(r["time_to_merge_h"] for r in subset),
        }

    return {
        "all": block(rows),
        "chain": block([r for r in rows if r["in_chain"] is True]),
        "solo": block([r for r in rows if r["in_chain"] is False]),
    }


def render_metrics_table(rows: list[dict[str, Any]], summary: Mapping[str, Any]) -> str:
    def cell(value: Any) -> str:
        return "-" if value is None else str(value)

    lines = ["number  status     ps  comments  +/-          ttfr_h   ttm_h  chain  subject"]
    for r in rows:
        chain = "-" if r["in_chain"] is None else ("yes" if r["in_chain"] else "no")
        lines.append(
            f"{str(r['number']):>6}  {str(r['status']):<9} {r['patchsets']:>3}  {cell(r['comments']):>8}  "
            f"{('+' + str(r['insertions']) + '/-' + str(r['deletions'])):<12} {cell(r['time_to_first_review_h']):>6} "
            f"{cell(r['time_to_merge_h']):>7}  {chain:<5}  {one_line(r['subject'], 60)}"
        )
    for name in ("all", "chain", "solo"):
        stats = summary[name]
        lines.append(
            f"summary {name}: " + " ".join(f"{k}={cell(v)}" for k, v in stats.items())
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- commands


def cmd_related(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    data = client.request("GET", f"/changes/{ref}/revisions/current/related")
    emit(args, data, lambda d: render_related(d, ref))
    return EXIT_OK


def cmd_comments(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    data = client.request("GET", f"/changes/{ref}/comments")
    emit(args, filter_comments(data, args.unresolved), render_comments)
    return EXIT_OK


def cmd_review(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    body = build_review_input(args.message, args.comment or [], args.in_reply_to, args.resolved)
    data = client.request("POST", f"/changes/{ref}/revisions/current/review", body)
    count = sum(len(v) for v in body.get("comments", {}).values())
    emit(
        args,
        data if data is not None else {},
        lambda _d: f"review posted on {args.change}: message={'yes' if 'message' in body else 'no'} "
        f"comments={count} unresolved={'no' if args.resolved else 'yes'}",
    )
    return EXIT_OK


def cmd_rebase_chain(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    body = {"base": args.base} if args.base else {}
    data = client.request("POST", f"/changes/{ref}/rebase:chain", body)
    emit(args, data, render_rebase_chain)
    return EXIT_OK


def cmd_topic(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    data = client.request("PUT", f"/changes/{ref}/topic", {"topic": args.topic})
    emit(args, data, lambda d: f"topic: {d if d else '(removed)'}")
    return EXIT_OK


def cmd_hashtags(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    body: dict[str, list[str]] = {}
    if args.add:
        body["add"] = args.add
    if args.remove:
        body["remove"] = args.remove
    if not body:
        raise UsageError("hashtags: give at least one --add TAG or --remove TAG")
    data = client.request("POST", f"/changes/{ref}/hashtags", body)
    emit(args, data, lambda d: "hashtags: " + (", ".join(d) if d else "(none)"))
    return EXIT_OK


def cmd_submitted_together(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    data = client.request(
        "GET", f"/changes/{ref}/submitted_together", query=[("o", "NON_VISIBLE_CHANGES")]
    )
    emit(args, data, render_submitted_together)
    return EXIT_OK


def cmd_detail(client: Client, args: argparse.Namespace) -> int:
    ref = parse_change_ref(args.change)
    data = client.request(
        "GET", f"/changes/{ref}/detail", query=[("o", "CURRENT_REVISION"), ("o", "CURRENT_COMMIT")]
    )
    emit(args, data, render_detail)
    return EXIT_OK


def cmd_query(client: Client, args: argparse.Namespace) -> int:
    if not args.q.strip():
        raise UsageError("query: empty query string")
    data = client.request(
        "GET", "/changes/", query=[("q", args.q), ("n", args.limit), ("o", "CURRENT_REVISION")]
    )
    emit(args, data, render_query)
    return EXIT_OK


def cmd_review_metrics(client: Client, args: argparse.Namespace) -> int:
    if not DATE_RE.match(args.since):
        raise UsageError(f"--since expects YYYY-MM-DD, got {args.since!r}")
    q = f"owner:{args.owner} since:{args.since}"
    if args.project:
        q += f" project:{args.project}"
    query = [("q", q), ("n", args.limit)] + [("o", o) for o in METRICS_OPTIONS]
    changes = client.request("GET", "/changes/", query=query) or []
    rows = [change_metrics(client, change) for change in changes]
    summary = summarise(rows)
    if args.fmt == "json":
        print(json.dumps({"query": q, "changes": rows, "summary": summary}, indent=2, ensure_ascii=False))
    elif args.fmt == "table":
        print(render_metrics_table(rows, summary))
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        print(json.dumps({"summary": summary}, ensure_ascii=False))
    return EXIT_OK


COMMANDS: dict[str, Callable[[Client, argparse.Namespace], int]] = {
    "related": cmd_related,
    "comments": cmd_comments,
    "review": cmd_review,
    "rebase-chain": cmd_rebase_chain,
    "topic": cmd_topic,
    "hashtags": cmd_hashtags,
    "submitted-together": cmd_submitted_together,
    "detail": cmd_detail,
    "query": cmd_query,
    "review-metrics": cmd_review_metrics,
}


# ---------------------------------------------------------------- CLI


def _add_common(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """Global options. On subparsers they default to SUPPRESS so they only override when given."""
    default: Any = argparse.SUPPRESS if suppress else None
    flag_default: Any = argparse.SUPPRESS if suppress else False
    parser.add_argument(
        "--host", default=default, metavar="URL",
        help="Gerrit base URL (else $GERRIT_HOST, git config gerrit-stack.host, remote.origin.url)",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="fmt", action="store_const", const="json", default=default,
                     help="print the raw JSON response")
    fmt.add_argument("--table", dest="fmt", action="store_const", const="table", default=default,
                     help="print a compact table (default)")
    parser.add_argument("--anonymous", action="store_true", default=flag_default,
                        help="ignore ~/.netrc; no Authorization header, no /a/ prefix")
    parser.add_argument("-v", "--verbose", action="store_true", default=flag_default,
                        help="print '<METHOD> <URL>' for every request to stderr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gerrit-rest.py",
        description="Minimal Gerrit REST client (MCP fallback for gerrit-stack).",
        epilog=(
            "examples:\n"
            "  gerrit-rest.py related 123\n"
            "  gerrit-rest.py --json comments https://gerrit.example.com/c/demo/+/123 --unresolved\n"
            "  gerrit-rest.py review 123 --message 'Done.' --comment 'src/a.py:10:Done, renamed.' \\\n"
            "      --in-reply-to <comment-id> --resolved\n"
            "  gerrit-rest.py rebase-chain 125\n"
            "  gerrit-rest.py topic 123 my-topic\n"
            "  gerrit-rest.py hashtags 123 --add stack-a --remove old\n"
            "  gerrit-rest.py query 'owner:self status:open' --limit 10\n"
            "  gerrit-rest.py review-metrics --owner admin --since 2026-09-01 --project demo-plugin\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(parser, suppress=False)
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common, suppress=True)
    sub = parser.add_subparsers(dest="cmd", metavar="<cmd>", required=True)

    def add(name: str, text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=text, description=text, parents=[common])

    change_help = "change number, Change-Id, project~branch~Change-Id, or Gerrit URL"

    p = add("related", "list the relation chain of a change (revisions/current/related)")
    p.add_argument("change", help=change_help)

    p = add("comments", "list published comments, flattened one per row")
    p.add_argument("change", help=change_help)
    p.add_argument("--unresolved", action="store_true", help="only unresolved comments")

    p = add("review", "post a review message and/or inline comments (never labels)")
    p.add_argument("change", help=change_help)
    p.add_argument("-m", "--message", required=True, help="review message (cover text)")
    p.add_argument("--comment", action="append", metavar="FILE:LINE:MSG",
                   help="inline comment; LINE 0 = file-level; repeatable")
    p.add_argument("--in-reply-to", metavar="ID", help="comment id the --comment(s) reply to")
    p.add_argument("--resolved", action="store_true",
                   help="mark the posted comment(s) resolved (default: unresolved)")

    p = add("rebase-chain", "rebase the whole chain ending at the change (rebase:chain)")
    p.add_argument("change", help=change_help)
    p.add_argument("--base", metavar="REV", help="revision to rebase onto (default: branch tip)")

    p = add("topic", "set the change's topic (empty string removes it)")
    p.add_argument("change", help=change_help)
    p.add_argument("topic")

    p = add("hashtags", "add and/or remove hashtags")
    p.add_argument("change", help=change_help)
    p.add_argument("--add", action="append", metavar="TAG", help="hashtag to add; repeatable")
    p.add_argument("--remove", action="append", metavar="TAG", help="hashtag to remove; repeatable")

    p = add("submitted-together", "changes that would be submitted together with the change")
    p.add_argument("change", help=change_help)

    p = add("detail", "change detail with current revision + commit")
    p.add_argument("change", help=change_help)

    p = add("query", "search changes (Gerrit query syntax)")
    p.add_argument("q", help="query, e.g. 'owner:self status:open'")
    p.add_argument("--limit", type=int, default=25, metavar="N", help="max results (default 25)")

    p = add("review-metrics", "per-change review metrics for an owner since a date")
    p.add_argument("--owner", required=True, metavar="U", help="owner account (username/email/self)")
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD", help="changes modified since")
    p.add_argument("--project", metavar="P", help="restrict to a project")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="max changes (default 50)")
    return parser


def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    environment = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage/help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        host = resolve_host(args.host, environment)
        auth = None if args.anonymous else netrc_auth(host, environment)
        client = Client(host, auth, verbose=args.verbose)
        return COMMANDS[args.cmd](client, args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except HttpError as exc:
        print(f"HTTP {exc.code} {exc.reason}", file=sys.stderr)
        if exc.body:
            print(exc.body, file=sys.stderr)
        return EXIT_ERROR
    except RestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
