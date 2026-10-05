#!/usr/bin/env python3
"""evals/run.py — standalone runner for the gerrit-stack eval and benchmark cases.

Runs the official `claude plugin eval` case format (evals/<case>/prompt.md +
case.yaml + graders/*.md) with `claude -p --output-format stream-json`, one
temporary workspace per run, git hooks ON (the official sandbox disables them
and cannot grant Bash on machines with Docker Desktop symlinks — see
evals/README.md), and three arms for the benchmark:

  with      --plugin-dir <repo> (gerrit-stack) + gerrit-mcp enabled
  mcp-only  no --plugin-dir, gerrit-mcp enabled
  without   no --plugin-dir, gerrit-mcp disabled for the duration

Case kinds (case.yaml `kind:`), one session per run in every kind:

  implement  (default) fixture → session → graders, chain metrics; optional push (--push-to)
  rework     fixture builds a seeded chain → seed push → reviewer step as `rena` (Code-Review -1 +
             one unresolved comment, via REST) → session → leftover commit → second push → read-back
  review     fixture builds a seeded chain → seed push → reviewing session → read-back (published
             comments/votes are violations, Gerrit drafts are fine)

rework and review need --push-to. The runner never submits and never votes except rena's -1.

Sandbox: every `claude` child gets an allowlisted environment (PATH HOME USER SHELL TMPDIR LANG
LC_* TERM, EVAL_*, GERRIT_HOST, GERRIT_STACK_TRACE, ENABLE_CLAUDEAI_MCP_SERVERS=false), stdin
/dev/null and a pinned --model; the session's init record is checked against the arm's allowlist
(isolation.json) and a failed check fails the run.

Outputs <out-dir>/aggregate-result.json (official schemaVersion 1), <out-dir>/runs/
<case>[@<variant>]/<arm>/<n>/{trace.jsonl,hook-trace.log,chain-metrics.json,last-message.md,
command.txt,isolation.json,conventions.json,push.log} plus review.json + rework-metrics.json
(rework) or review-metrics.json (review), <out-dir>/report.md and a summary table on stdout.
Exit 0 = overall score >= threshold, 1 = below, 2 = partial (cost ceiling) or usage error.

stdlib only; `python3 evals/run.py --help` for options.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures
import datetime as _dt
import difflib
import fnmatch
import json
import netrc
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill", "AskUserQuestion"]
DEFAULT_MAX_TURNS = 30
DEFAULT_TIMEOUT = 600
ARMS = ("with", "without", "mcp-only")
GRADER_TYPES = ("regex", "tool_used", "tool_order", "file_exists", "llm", "baseline")
KINDS = ("implement", "rework", "review")
VARIANTS = ("natural", "nudged")
REVIEWER = "rena"
REVIEW_MESSAGE = "Reviewed as rena"
CONVENTIONAL_RE = re.compile(r"(?:^|[\s|>*_`(\"'])(praise|nitpick|suggestion|issue|todo|question|thought|chore|note)( \([^)]*\))?:", re.M)
MCP_TOOL_RULE = "mcp__plugin_gerrit_gerrit"  # permission rule: all tools of the gerrit-mcp plugin's server
RUNNER_COMMIT_NOTE = "(left uncommitted by the agent; committed by the benchmark runner)"
DEFAULT_REWORK_MESSAGE = "issue (blocking): please address this before it can be merged."
DEFAULT_MODEL = "claude-opus-5-5"  # pinned: results must not depend on the account's default model
DEFAULT_VERIFY_CMD = "bash tools/quick-check.sh"
CHAIN_METRICS_TIMEOUT = 1800  # --verify-cmd builds every commit of the chain
ENV_PASS = ("PATH", "HOME", "USER", "SHELL", "TMPDIR", "LANG", "TERM")  # plus LC_*
ENV_RUNNER = ("EVAL_PLUGIN_ROOT", "GERRIT_HOST", "GERRIT_STACK_TRACE")  # plus the case's EVAL_*
ARM_PLUGINS = {"with": ("gerrit", "gerrit-stack"), "mcp-only": ("gerrit",), "without": ()}
ARM_MCP_SERVERS = {"with": ("plugin:gerrit:gerrit",), "mcp-only": ("plugin:gerrit:gerrit",), "without": ()}
MCP_DENIED_HINT = "haven't granted"  # tool_result text of an MCP call refused for lack of permission
CONVENTIONAL_COMMIT_RE = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: .+")

VERBOSE = False
_LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    if VERBOSE:
        with _LOG_LOCK:
            sys.stderr.write(f"[run.py] {msg}\n")
            sys.stderr.flush()


def say(msg: str) -> None:
    """stdout line, serialised across worker threads."""
    with _LOG_LOCK:
        print(msg, flush=True)


def warn(msg: str) -> None:
    sys.stderr.write(f"[run.py] warning: {msg}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------
# YAML subset parser (scalars, quoted strings, flow lists/maps, block lists,
# nested maps, `|`/`>` block scalars). Enough for prompt.md frontmatter,
# case.yaml and grader frontmatter; never for arbitrary YAML.
# --------------------------------------------------------------------------

class YamlError(ValueError):
    pass


_NUM_RE = re.compile(r"^-?(?:\d+|\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "" or s in ("~", "null", "Null", "NULL"):
        return None
    if s[0] in "\"'":
        return _parse_quoted(s)[0]
    if s[0] == "[" or s[0] == "{":
        return _parse_flow(s)
    if s in ("true", "True", "TRUE", "yes", "Yes", "on"):
        return True
    if s in ("false", "False", "FALSE", "no", "No", "off"):
        return False
    if _NUM_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return float(s)
    return s


def _parse_quoted(s: str) -> tuple[str, int]:
    """Parse a quoted string at s[0]; return (value, index after closing quote)."""
    q = s[0]
    i = 1
    out = []
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0"}
            if n in mapping:
                out.append(mapping[n])
                i += 2
                continue
            if n == "x" and i + 3 < len(s):
                out.append(chr(int(s[i + 2:i + 4], 16)))
                i += 4
                continue
            if n == "u" and i + 5 < len(s):
                out.append(chr(int(s[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(n)
            i += 2
            continue
        if q == "'" and c == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        if q == '"' and c == '"':
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise YamlError(f"unterminated quoted string: {s}")


def _split_flow_items(body: str) -> list[str]:
    """Split the inside of [..] / {..} on top-level commas (respects quotes/nesting)."""
    items, depth, cur, i = [], 0, [], 0
    while i < len(body):
        c = body[i]
        if c in "\"'":
            _, end = _parse_quoted(body[i:])
            cur.append(body[i:i + end])
            i += end
            continue
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        if c == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur)
    if tail.strip():
        items.append(tail)
    return [it.strip() for it in items if it.strip()]


def _parse_flow(s: str) -> Any:
    s = s.strip()
    if s.startswith("["):
        if not s.endswith("]"):
            raise YamlError(f"unterminated flow list: {s}")
        return [parse_scalar(it) for it in _split_flow_items(s[1:-1])]
    if s.startswith("{"):
        if not s.endswith("}"):
            raise YamlError(f"unterminated flow map: {s}")
        out: dict[str, Any] = {}
        for it in _split_flow_items(s[1:-1]):
            k, v = _split_key(it)
            out[k] = parse_scalar(v)
        return out
    return parse_scalar(s)


def _split_key(line: str) -> tuple[str, str]:
    """'key: value' -> (key, value); the key may be quoted."""
    if line and line[0] in "\"'":
        key, end = _parse_quoted(line)
        rest = line[end:].lstrip()
        if not rest.startswith(":"):
            raise YamlError(f"expected ':' after key in: {line}")
        return key, rest[1:].strip()
    m = re.match(r"^([^:#]+?)\s*:(?:\s+(.*)|$)", line)
    if not m:
        raise YamlError(f"expected 'key: value' in: {line}")
    return m.group(1).strip(), (m.group(2) or "").strip()


def _strip_comment(line: str) -> str:
    """Remove a trailing ' # comment' outside quotes."""
    in_q = None
    i = 0
    while i < len(line):
        c = line[i]
        if in_q:
            if c == "\\" and in_q == '"':
                i += 2
                continue
            if c == in_q:
                in_q = None
        elif c in "\"'":
            in_q = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def parse_yaml(text: str) -> Any:
    lines = []
    for raw in text.splitlines():
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            lines.append(None)  # keep positions for block scalars
        else:
            lines.append(raw.rstrip("\n"))
    value, _ = _parse_block(lines, 0, -1)
    return value if value is not None else {}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _next_content(lines: list, i: int) -> int:
    while i < len(lines) and lines[i] is None:
        i += 1
    return i


def _block_scalar(lines: list, i: int, parent_indent: int, style: str) -> tuple[str, int]:
    i = _next_content(lines, i)
    if i >= len(lines) or _indent(lines[i]) <= parent_indent:
        return "", i
    base = _indent(lines[i])
    out = []
    while i < len(lines):
        ln = lines[i]
        if ln is None:
            out.append("")
            i += 1
            continue
        if _indent(ln) < base:
            break
        out.append(ln[base:])
        i += 1
    while out and out[-1] == "":
        out.pop()
    text = "\n".join(out)
    if style == ">":
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text + "\n", i


def _parse_block(lines: list, i: int, parent_indent: int) -> tuple[Any, int]:
    i = _next_content(lines, i)
    if i >= len(lines):
        return None, i
    ind = _indent(lines[i])
    if ind <= parent_indent:
        return None, i
    if lines[i].lstrip().startswith("- ") or lines[i].strip() == "-":
        return _parse_list(lines, i, ind)
    return _parse_map(lines, i, ind)


def _parse_value_or_block(lines: list, i: int, ind: int, value: str) -> tuple[Any, int]:
    value = _strip_comment(value)
    if value in ("|", ">", "|-", ">-", "|+"):
        text, j = _block_scalar(lines, i + 1, ind, value[0])
        if value.endswith("-"):
            text = text.rstrip("\n")
        return text, j
    if value == "":
        nested, j = _parse_block(lines, i + 1, ind)
        return (nested if nested is not None else None), j
    return parse_scalar(value), i + 1


def _parse_map(lines: list, i: int, ind: int) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    while i < len(lines):
        ln = lines[i]
        if ln is None:
            i += 1
            continue
        if _indent(ln) < ind:
            break
        if _indent(ln) > ind:
            raise YamlError(f"unexpected indent at line: {ln!r}")
        body = ln[ind:]
        if body.startswith("- "):
            raise YamlError(f"list item where a key was expected: {ln!r}")
        key, value = _split_key(body)
        out[key], i = _parse_value_or_block(lines, i, ind, value)
    return out, i


def _parse_list(lines: list, i: int, ind: int) -> tuple[list, int]:
    out: list[Any] = []
    while i < len(lines):
        ln = lines[i]
        if ln is None:
            i += 1
            continue
        if _indent(ln) < ind:
            break
        body = ln[ind:]
        if not (body.startswith("- ") or body == "-"):
            if _indent(ln) == ind:
                break
            raise YamlError(f"unexpected line inside list: {ln!r}")
        item = body[2:].strip() if body != "-" else ""
        item = _strip_comment(item)
        if item == "":
            nested, i = _parse_block(lines, i + 1, ind)
            out.append(nested)
            continue
        # "- key: value" -> map item; its remaining keys sit at indent+2 on the next lines
        if item[0] not in "\"'[{" and re.match(r"^[^:#]+?\s*:(\s|$)", item):
            sub_ind = ind + 2
            saved = lines[i]
            lines[i] = " " * sub_ind + item  # view the item as the first map line
            try:
                m, j = _parse_map(lines, i, sub_ind)
            finally:
                lines[i] = saved
            out.append(m)
            i = j
            continue
        out.append(parse_scalar(item))
        i += 1
    return out, i


def split_frontmatter(text: str) -> tuple[dict, str]:
    """'---\\n<yaml>\\n---\\n<body>' -> (dict, body). No frontmatter -> ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text
    for k in range(1, len(lines)):
        if lines[k].strip() == "---":
            fm = parse_yaml("\n".join(lines[1:k]))
            body = "\n".join(lines[k + 1:])
            return (fm if isinstance(fm, dict) else {}), body.strip("\n")
    raise YamlError("unterminated frontmatter")


# --------------------------------------------------------------------------
# Case model
# --------------------------------------------------------------------------

class Grader:
    def __init__(self, name: str, front: dict, body: str, path: str = ""):
        self.name = name
        self.type = str(front.get("type", "")).strip()
        self.weight = float(front.get("weight", 1.0) or 0.0)
        self.arm = front.get("arm")
        self.spec = front
        self.body = body
        self.path = path
        if self.type not in GRADER_TYPES:
            raise ValueError(f"grader {name}: unknown type {self.type!r}")


def load_graders(gdir: str) -> list[Grader]:
    out: list[Grader] = []
    if os.path.isdir(gdir):
        for fn in sorted(os.listdir(gdir)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(gdir, fn), encoding="utf-8") as fh:
                front, body = split_frontmatter(fh.read())
            out.append(Grader(fn[:-3], front, body, os.path.join(gdir, fn)))
    return out


class Case:
    def __init__(self, path: str):
        self.dir = os.path.abspath(path)
        self.name = os.path.basename(self.dir.rstrip("/"))
        with open(os.path.join(self.dir, "prompt.md"), encoding="utf-8") as fh:
            self.front, self.prompt = split_frontmatter(fh.read())
        self.name = str(self.front.get("name") or self.name)
        self.scaffold_script: Optional[str] = None
        self.cfg: dict = {}
        cy = os.path.join(self.dir, "case.yaml")
        if os.path.exists(cy):
            with open(cy, encoding="utf-8") as fh:
                cfg = parse_yaml(fh.read()) or {}
            self.cfg = cfg if isinstance(cfg, dict) else {}
            ctx = self.cfg.get("context") or {}
            if isinstance(ctx, dict) and ctx.get("scaffold_script"):
                self.scaffold_script = os.path.join(self.dir, str(ctx["scaffold_script"]))
        # benchmark keys (ignored by the official runner): kind, concerns, nudges, rework, review
        self.case_yaml: Optional[str] = cy if os.path.exists(cy) else None
        kind = str(self.cfg.get("kind") or "implement").strip()
        if kind not in KINDS:
            raise ValueError(f"case {self.name}: unknown kind {kind!r} in {cy} (choose from {', '.join(KINDS)})")
        self.kind = kind
        self.has_concerns = bool(self.cfg.get("concerns"))
        rework = self.cfg.get("rework")
        self.rework: dict = rework if isinstance(rework, dict) else {}
        review = self.cfg.get("review")
        self.review: dict = review if isinstance(review, dict) else {}
        nudges = self.cfg.get("nudges")
        self.nudges: dict = nudges if isinstance(nudges, dict) else {}
        self.graders: list[Grader] = load_graders(os.path.join(self.dir, "graders"))

    def _block(self, name: str, spec: dict) -> dict:
        if not spec or not spec.get("target_subject"):
            raise ValueError(f"case {self.name}: {os.path.join(self.dir, 'case.yaml')} needs a "
                             f"'{name}:' block with 'target_subject' (kind: {self.kind})")
        return spec

    def rework_spec(self) -> dict:
        """The `rework:` block (target_subject, file, line, message, may_change, nudge)."""
        return self._block("rework", self.rework)

    def review_spec(self) -> dict:
        """The `review:` block (target_subject, planted)."""
        return self._block("review", self.review)

    def nudge(self) -> Optional[str]:
        """The line the nudged variant appends: implement → nudges.stage1, rework → rework.nudge,
        review → none."""
        v = None
        if self.kind == "implement":
            v = self.nudges.get("stage1")
        elif self.kind == "rework":
            v = self.rework.get("nudge")
        return str(v).strip() if v else None

    @property
    def runs(self) -> int:
        return int(self.front.get("runs") or 1)

    @property
    def max_turns(self) -> int:
        return int(self.front.get("max_turns") or DEFAULT_MAX_TURNS)

    @property
    def timeout_seconds(self) -> int:
        return int(self.front.get("timeout_seconds") or DEFAULT_TIMEOUT)

    @property
    def allowed_tools(self) -> list[str]:
        tools = self.front.get("allowed_tools")
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]
        return list(tools) if tools else list(DEFAULT_ALLOWED_TOOLS)

    @property
    def model(self) -> Optional[str]:
        m = self.front.get("model")
        return str(m) if m else None

    @property
    def env(self) -> dict[str, str]:
        env = self.front.get("env") or {}
        out = {}
        if isinstance(env, dict):
            for k, v in env.items():
                if not re.match(r"^EVAL_[A-Z0-9_]*$", str(k)):
                    warn(f"{self.name}: env key {k!r} ignored (must match EVAL_[A-Z0-9_]*)")
                    continue
                out[str(k)] = "" if v is None else str(v)
        return out


def discover_cases(eval_dir: str, globs: list[str]) -> list[Case]:
    cases = []
    if not os.path.isdir(eval_dir):
        return cases
    for name in sorted(os.listdir(eval_dir)):
        d = os.path.join(eval_dir, name)
        if not os.path.isdir(d) or not os.path.exists(os.path.join(d, "prompt.md")):
            continue
        if globs and not any(fnmatch.fnmatch(name, g) for g in globs):
            continue
        cases.append(Case(d))
    return cases


# --------------------------------------------------------------------------
# Trace (stream-json) parsing
# --------------------------------------------------------------------------

class ToolCall:
    def __init__(self, index: int, name: str, input: Any, call_id: str):
        self.index = index
        self.name = name
        self.input = input
        self.id = call_id
        self.result: Optional[str] = None
        self.is_error = False

    def input_json(self) -> str:
        return json.dumps(self.input, separators=(",", ":"), ensure_ascii=False, sort_keys=False)


class Trace:
    def __init__(self) -> None:
        self.tool_calls: list[ToolCall] = []
        self.assistant_texts: list[str] = []
        self.result: dict[str, Any] = {}
        self.init: dict[str, Any] = {}
        self.line_count = 0
        self.bad_lines = 0

    @property
    def last_message(self) -> str:
        r = self.result.get("result")
        if isinstance(r, str) and r.strip():
            return r
        return self.assistant_texts[-1] if self.assistant_texts else ""

    @property
    def num_turns(self) -> Optional[int]:
        v = self.result.get("num_turns")
        return int(v) if isinstance(v, (int, float)) else None

    @property
    def cost_usd(self) -> float:
        v = self.result.get("total_cost_usd")
        return float(v) if isinstance(v, (int, float)) else 0.0


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(str(blk.get("text", "")))
            elif isinstance(blk, str):
                parts.append(blk)
        return "\n".join(parts)
    return "" if content is None else json.dumps(content)


def parse_trace_lines(lines) -> Trace:
    tr = Trace()
    by_id: dict[str, ToolCall] = {}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        tr.line_count += 1
        try:
            msg = json.loads(raw)
        except ValueError:
            tr.bad_lines += 1
            continue
        if not isinstance(msg, dict):
            continue
        t = msg.get("type")
        if t == "system" and msg.get("subtype") == "init":
            tr.init = msg
        elif t == "assistant":
            content = (msg.get("message") or {}).get("content") or []
            if isinstance(content, str):
                tr.assistant_texts.append(content)
                continue
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use":
                    tc = ToolCall(len(tr.tool_calls), str(blk.get("name", "")), blk.get("input"), str(blk.get("id", "")))
                    tr.tool_calls.append(tc)
                    by_id[tc.id] = tc
                elif blk.get("type") == "text" and str(blk.get("text", "")).strip():
                    tr.assistant_texts.append(str(blk["text"]))
        elif t == "user":
            content = (msg.get("message") or {}).get("content") or []
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        tc = by_id.get(str(blk.get("tool_use_id", "")))
                        if tc is not None:
                            tc.result = _result_text(blk.get("content"))
                            tc.is_error = bool(blk.get("is_error"))
        elif t == "result":
            tr.result = msg
    return tr


def parse_trace_file(path: str) -> Trace:
    if not os.path.exists(path):
        return Trace()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_trace_lines(fh)


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

class GradeContext:
    def __init__(self, trace: Trace, trace_text: str, workspace: Optional[str], changed_files: list[str],
                 judge: Optional[Callable[[str], tuple[bool, str, float]]] = None):
        self.trace = trace
        self.trace_text = trace_text
        self.workspace = workspace
        self.changed_files = changed_files
        self.judge = judge
        self.judge_cost = 0.0

    def files_text(self) -> str:
        if not self.workspace:
            return ""
        parts = []
        for rel in self.changed_files:
            p = os.path.join(self.workspace, rel)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    parts.append(f"### {rel}\n{fh.read(200_000)}\n")
            except OSError:
                continue
        return "".join(parts)

    def target_text(self, target: Any) -> tuple[str, str]:
        """Resolve a grader target -> (text, description)."""
        if target is None or target == "last_message":
            return self.trace.last_message, "last_message"
        if target == "trace":
            return self.trace_text, "trace"
        if target == "files":
            return self.files_text(), "files"
        if isinstance(target, dict) and target.get("source") == "file":
            rel = str(target.get("path", ""))
            if not self.workspace:
                return "", f"file {rel} (no workspace)"
            p = os.path.join(self.workspace, rel)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    return fh.read(), f"file {rel}"
            except OSError:
                return "", f"file {rel} (missing)"
        raise ValueError(f"unknown target {target!r}")


def _re_flags(flags: Any) -> int:
    out = 0
    if not flags:
        return out
    if isinstance(flags, list):
        flags = "".join(str(f) for f in flags)
    for ch in str(flags):
        out |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X}.get(ch, 0)
    return out


def _grader_result(g: Grader, passed: Optional[bool], detail: str, skipped: bool = False) -> dict:
    return {"name": g.name, "type": g.type, "passed": passed, "weight": g.weight, "detail": detail, "skipped": skipped}


def grade_regex(g: Grader, ctx: GradeContext) -> dict:
    pattern = str(g.spec.get("pattern", ""))
    if not pattern:
        return _grader_result(g, False, "regex grader without pattern")
    text, where = ctx.target_text(g.spec.get("target"))
    rx = re.compile(pattern, _re_flags(g.spec.get("flags")))
    matches = rx.findall(text)
    count = len(matches)
    mode = str(g.spec.get("match", "contains"))
    if mode == "contains":
        passed = count > 0
    elif mode == "not_contains":
        passed = count == 0
    elif mode.startswith("count:"):
        want = int(mode.split(":", 1)[1])
        passed = count == want
        return _grader_result(g, passed, f"{count} match(es) of /{pattern}/ in {where}, want {want}")
    else:
        return _grader_result(g, False, f"unknown match mode {mode!r}")
    sample = ""
    if count and mode == "not_contains":
        m = rx.search(text)
        sample = " first: " + repr(text[max(0, m.start() - 40): m.end() + 40])[:200]
    return _grader_result(g, passed, f"{count} match(es) of /{pattern}/ in {where} ({mode}){sample}")


def _match_call(tc: ToolCall, tool: str, input_match: Optional[str]) -> bool:
    if tc.name != tool:
        return False
    if input_match:
        return re.search(str(input_match), tc.input_json()) is not None
    return True


def grade_tool_used(g: Grader, ctx: GradeContext) -> dict:
    tool = str(g.spec.get("tool", ""))
    input_match = g.spec.get("input_match")
    lo = int(g.spec.get("min", 1) if g.spec.get("min") is not None else 1)
    hi = g.spec.get("max")
    hi = int(hi) if hi is not None else None
    hits = [tc for tc in ctx.trace.tool_calls if _match_call(tc, tool, input_match)]
    n = len(hits)
    passed = n >= lo and (hi is None or n <= hi)
    rng = f">= {lo}" if hi is None else f"in [{lo}, {hi}]"
    sample = f" e.g. {hits[0].input_json()[:120]}" if hits else ""
    return _grader_result(g, passed, f"{n} call(s) to {tool}" + (f" matching /{input_match}/" if input_match else "") + f", want {rng}{sample}")


def _order_spec(spec: Any) -> tuple[str, Optional[str]]:
    if isinstance(spec, dict):
        return str(spec.get("tool", "")), spec.get("input_match")
    return str(spec), None


def grade_tool_order(g: Grader, ctx: GradeContext) -> dict:
    b_tool, b_match = _order_spec(g.spec.get("before"))
    a_tool, a_match = _order_spec(g.spec.get("after"))
    calls = ctx.trace.tool_calls
    before_idx = [tc.index for tc in calls if _match_call(tc, b_tool, b_match)]
    after_idx = [tc.index for tc in calls if _match_call(tc, a_tool, a_match)]
    if not after_idx:
        passed = bool(before_idx)
        return _grader_result(g, passed, f"{a_tool} never used; {b_tool} used {len(before_idx)}x")
    if not before_idx:
        return _grader_result(g, False, f"{a_tool} used at #{after_idx[0]} but {b_tool} never used")
    passed = before_idx[0] < after_idx[0]
    return _grader_result(g, passed, f"first {b_tool} at #{before_idx[0]}, first {a_tool} at #{after_idx[0]}")


def grade_file_exists(g: Grader, ctx: GradeContext) -> dict:
    rel = str(g.spec.get("path", ""))
    want = g.spec.get("exists", True)
    want = True if want is None else bool(want)
    if not ctx.workspace:
        return _grader_result(g, False, "no workspace")
    exists = os.path.exists(os.path.join(ctx.workspace, rel))
    return _grader_result(g, exists == want, f"{rel} exists={exists}, want {want}")


def _truncate(s: str, n: int) -> str:
    s = s.replace("\r", "")
    return s if len(s) <= n else s[:n] + f"… [+{len(s) - n} chars]"


JUDGE_FOCUS_VALUES = ("all", "trace", "tools", "last_message", "files")


def judge_prompt(rubric: str, focus: Any, ctx: GradeContext, max_calls: int = 120) -> str:
    focus = str(focus or "all")
    hint = ""
    if focus not in JUDGE_FOCUS_VALUES:
        # Free-text focus (e.g. "commit structure"): keep it as a hint for the judge and show
        # the full evidence; a typo must never silently strip the evidence.
        hint, focus = focus, "all"
    parts = [
        "You are grading one run of an automated coding-agent evaluation. Decide whether the run satisfies "
        "the rubric below, judging only on the evidence shown. Do not use any tools.",
        "",
        "## Rubric",
        rubric.strip() or "(empty rubric)",
        *(["", f"Focus on: {hint}"] if hint else []),
        "",
        "## Evidence",
    ]
    if focus in ("all", "trace", "tools"):
        parts.append(f"### Tool calls in order ({len(ctx.trace.tool_calls)} total)")
        calls = ctx.trace.tool_calls
        shown = calls[:max_calls]
        for tc in shown:
            res = "" if tc.result is None else " -> " + _truncate(tc.result.strip(), 240)
            parts.append(f"{tc.index + 1}. {tc.name} {_truncate(tc.input_json(), 500)}{res}")
        if len(calls) > max_calls:
            parts.append(f"… {len(calls) - max_calls} more call(s) omitted")
        if not calls:
            parts.append("(no tool calls)")
        parts.append("")
    if focus in ("all", "last_message", "trace"):
        parts.append("### Final assistant message")
        parts.append(_truncate(ctx.trace.last_message.strip(), 8000) or "(empty)")
        parts.append("")
    if focus == "files":
        parts.append("### Files created or modified by the run")
        parts.append(_truncate(ctx.files_text(), 12000) or "(none)")
        parts.append("")
    parts.append("Reason briefly (at most 8 lines), then output exactly one final line containing only PASS or FAIL.")
    return "\n".join(parts)


def grade_llm(g: Grader, ctx: GradeContext) -> dict:
    if ctx.judge is None:
        return _grader_result(g, None, "no judge available (dry run)", skipped=True)
    prompt = judge_prompt(g.body, g.spec.get("focus"), ctx)
    passed, detail, cost = ctx.judge(prompt)
    ctx.judge_cost += cost
    return _grader_result(g, passed, detail)


def grade(g: Grader, ctx: GradeContext, arm: str) -> dict:
    if g.arm and str(g.arm) != arm:
        return _grader_result(g, None, f"only for arm {g.arm}", skipped=True)
    if g.type == "baseline":
        warn(f"grader {g.name}: type 'baseline' is not supported by evals/run.py; skipped")
        return _grader_result(g, None, "baseline graders are skipped by run.py", skipped=True)
    fn = {"regex": grade_regex, "tool_used": grade_tool_used, "tool_order": grade_tool_order,
          "file_exists": grade_file_exists, "llm": grade_llm}[g.type]
    try:
        return fn(g, ctx)
    except Exception as exc:  # a broken grader never aborts the suite
        return _grader_result(g, False, f"grader error: {exc}")


def score_graders(results: list[dict]) -> float:
    total = sum(r["weight"] for r in results if not r.get("skipped"))
    if total <= 0:
        return 0.0
    got = sum(r["weight"] for r in results if not r.get("skipped") and r.get("passed"))
    return round(got / total, 4)


# --------------------------------------------------------------------------
# claude invocations
# --------------------------------------------------------------------------

def find_mcp_plugin_dir() -> Optional[str]:
    """Newest installed gerrit-mcp plugin checkout (~/.claude/plugins/cache/gerrit-mcp/gerrit/<hash>)."""
    base = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache", "gerrit-mcp", "gerrit")
    try:
        dirs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    except OSError:
        return None
    return os.path.join(base, dirs[-1]) if dirs else None


def build_claude_cmd(prompt: str, case: Case, arm: str, plugin_dir: Optional[str], model: Optional[str],
                     max_turns: Optional[int] = None, mcp_plugin_dir: Optional[str] = None) -> list[str]:
    # --model is always passed (explicit, else the case's, else DEFAULT_MODEL): a scrubbed session
    # would otherwise fall back to whatever the account defaults to.
    # --allowedTools is variadic: it swallows every following bare argument, so it must be
    # followed by another option (--max-turns is always present) before the prompt goes last.
    # --setting-sources project,local keeps the user's own CLAUDE.md, hooks and user-scope
    # plugins out of the run, so every arm sees only what the case declares; the official
    # gerrit-mcp plugin is therefore loaded explicitly for the arms that want it.
    tools = list(case.allowed_tools)
    if arm in ("with", "mcp-only") and mcp_plugin_dir and MCP_TOOL_RULE not in tools:
        tools.append(MCP_TOOL_RULE)  # every tool of the plugin's `gerrit` MCP server
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project,local",
           "--allowedTools", ",".join(tools),
           "--max-turns", str(max_turns or case.max_turns)]
    cmd += ["--model", model or case.model or DEFAULT_MODEL]
    if arm in ("with", "mcp-only") and mcp_plugin_dir:
        cmd += ["--plugin-dir", mcp_plugin_dir]
    if arm == "with" and plugin_dir:
        cmd += ["--plugin-dir", plugin_dir]
    cmd.append(prompt)
    return cmd


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)


def run_process(cmd: list[str], cwd: str, env: dict, timeout: float, stdout_path: str, stderr_path: str) -> tuple[int, bool]:
    """Run cmd in its own process group; kill the group on timeout. -> (rc, timed_out)."""
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                start_new_session=True)
        try:
            proc.wait(timeout=timeout)
            return proc.returncode, False
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return proc.returncode if proc.returncode is not None else -9, True



def _extract_verdict(text: str) -> str:
    """Return 'PASS' or 'FAIL' when one of the last three non-empty lines is exactly that
    token (after stripping markdown emphasis/punctuation); '' otherwise."""
    lines = [ln.strip().strip("*`._:-").upper() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines[-3:]):
        if ln in ("PASS", "FAIL"):
            return ln
        if ln.startswith(("PASS", "FAIL")) and len(ln) <= 12:
            return ln[:4]
    return ""

def make_judge(model: str, votes: int, cwd: str, log_dir: str) -> Callable[[str], tuple[bool, str, float]]:
    counter = {"n": 0}

    def judge(prompt: str) -> tuple[bool, str, float]:
        verdicts, cost, details = [], 0.0, []
        for v in range(max(1, votes)):
            counter["n"] += 1
            base = os.path.join(log_dir, f"judge-{counter['n']:02d}")
            with open(base + ".prompt.md", "w", encoding="utf-8") as fh:
                fh.write(prompt)
            cmd = ["claude", "-p", "--setting-sources", "project,local", "--model", model, "--max-turns", "1", "--output-format", "json",
                   "--no-session-persistence", prompt]
            env = sandbox_env()  # same scrubbed environment as the sessions, no hook trace
            rc, timed_out = run_process(cmd, cwd, env, 300, base + ".json", base + ".stderr")
            text, c = "", 0.0
            try:
                with open(base + ".json", encoding="utf-8", errors="replace") as fh:
                    data = json.loads(fh.read() or "{}")
                if isinstance(data, list):
                    data = next((d for d in data if isinstance(d, dict) and d.get("type") == "result"), {})
                text = str(data.get("result") or "")
                c = float(data.get("total_cost_usd") or 0.0)
            except (ValueError, OSError):
                pass
            cost += c
            last = _extract_verdict(text)
            if not last:
                # Judge did not end with PASS/FAIL: retry once with a stricter format demand
                # instead of scoring a formatting slip as a real FAIL.
                counter["n"] += 1
                base2 = os.path.join(log_dir, f"judge-{counter['n']:02d}-retry")
                strict = prompt + "\n\nFORMAT REMINDER: your reply MUST end with a final line that is exactly PASS or FAIL (nothing else on that line)."
                cmd2 = cmd[:-1] + [strict]
                rc, timed_out = run_process(cmd2, cwd, env, 300, base2 + ".json", base2 + ".stderr")
                try:
                    with open(base2 + ".json", encoding="utf-8", errors="replace") as fh:
                        data = json.loads(fh.read() or "{}")
                    if isinstance(data, list):
                        data = next((d for d in data if isinstance(d, dict) and d.get("type") == "result"), {})
                    text = str(data.get("result") or "")
                    cost += float(data.get("total_cost_usd") or 0.0)
                except (ValueError, OSError):
                    text = ""
                last = _extract_verdict(text)
            verdict = last.startswith("PASS")
            if not last:
                details.append(f"vote {v + 1}: no PASS/FAIL verdict after retry (rc={rc}, timed_out={timed_out}) -> FAIL")
            else:
                reason = " ".join(text.strip().splitlines()[:-1]).strip()
                details.append(f"vote {v + 1}: {last[:4]} — {_truncate(reason, 300)}")
            verdicts.append(verdict)
        passed = sum(verdicts) * 2 > len(verdicts)
        return passed, "; ".join(details), cost

    return judge


def snapshot(ws: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, ws)] = (st.st_size, st.st_mtime_ns)
    return out


def changed_since(before: dict, after: dict) -> list[str]:
    return sorted(p for p, sig in after.items() if before.get(p) != sig)


def kill_stub(ws: str) -> None:
    pid_file = os.path.join(ws, ".stub-pid")
    if not os.path.exists(pid_file):
        return
    try:
        with open(pid_file, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, signal.SIGTERM)
        log(f"killed stub pid {pid}")
    except (ValueError, OSError):
        pass



def _git(ws: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", ws, *args], capture_output=True, text=True)


def git_out(ws: str, *args: str) -> str:
    r = _git(ws, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def parse_push_output(text: str) -> dict:
    """Change numbers from a `git push … refs/for/…` transcript: {"new": [...], "updated": [...], "all": [...]}.
    Gerrit prints one `remote:   <url>/c/<project>/+/<n> <subject> [NEW|UPDATED|…]` line per change."""
    new, updated, everything = [], [], set()
    for m in re.finditer(r"/c/[^/\s]+/\+/(\d+)([^\n]*)", text):
        n = int(m.group(1))
        everything.add(n)
        tail = m.group(2).upper()
        if "[NEW]" in tail:
            new.append(n)
        elif "[UPDATED]" in tail or "UPDATED" in tail:
            updated.append(n)
    return {"new": sorted(set(new)), "updated": sorted(set(updated)), "all": sorted(everything)}


def push_workspace_for_review_ex(ws: str, url: str, hashtags: list[str], log_path: str) -> dict:
    """Rebase the workspace's commits onto the target project's master and push them to
    refs/for/master with the given hashtags. The rebase base is `merge-base review/master HEAD`
    when the histories are related (a second push of the same workspace) and the fixture root
    otherwise (first push: the fixture history is unrelated to the demo project).
    Returns {"numbers": [...], "new": [...], "updated": [...], "ok": bool, "error": str|None,
    "head": sha|None, "base": sha|None}."""
    out: dict[str, Any] = {"numbers": [], "new": [], "updated": [], "ok": False, "error": None, "head": None, "base": None}
    with open(log_path, "a", encoding="utf-8") as lg:
        _git(ws, "remote", "remove", "review")
        _git(ws, "remote", "add", "review", url)
        if _git(ws, "fetch", "-q", "review", "master").returncode != 0:
            lg.write(f"fetch {url} failed\n"); out["error"] = "fetch failed"; return out
        base = git_out(ws, "merge-base", "review/master", "HEAD")
        if not base:
            root = _git(ws, "rev-list", "--max-parents=0", "HEAD").stdout.split()
            if not root:
                out["error"] = "no commits"; return out
            base = root[-1]
        if _git(ws, "rev-list", "--count", f"{base}..HEAD").stdout.strip() in ("", "0"):
            lg.write("nothing to push (no commits beyond the base)\n"); out["error"] = "nothing to push"; return out
        r = _git(ws, "-c", "sequence.editor=true", "rebase", "-q", "--onto", "review/master", base, "HEAD")
        if r.returncode != 0:
            _git(ws, "rebase", "--abort"); lg.write("rebase onto review/master failed:\n" + r.stderr)
            out["error"] = "rebase onto review/master failed"; return out
        out["base"] = git_out(ws, "rev-parse", "review/master")
        out["head"] = git_out(ws, "rev-parse", "HEAD")
        refspec = "HEAD:refs/for/master%" + ",".join("t=" + h for h in hashtags)
        r = _git(ws, "push", "review", refspec)
        lg.write(r.stdout + r.stderr)
        parsed = parse_push_output(r.stdout + r.stderr)
        out["new"], out["updated"] = parsed["new"], parsed["updated"]
        if r.returncode != 0:
            out["error"] = f"push exited {r.returncode}"
            if "no new changes" in (r.stdout + r.stderr):
                out["error"] = "no new changes"
            return out
        out["numbers"] = parsed["all"]
        out["ok"] = True
        return out


def push_hashtags(case_name: str, arm: str, run_id: str, variant: str = "natural", rep: int = 1) -> list[str]:
    """bench-<case>-<arm>, run-<id>, var-<variant>, rep-<n>: one query per run, one per batch."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)
    return [f"bench-{case_name}-{arm}", f"run-{safe}", f"var-{variant}", f"rep-{rep}"]


def chain_commits(ws: str, base: Optional[str] = None) -> list[dict]:
    """The workspace chain bottom-to-top: [{sha, change_id, subject, lines}]. Base = the given sha,
    else `merge-base review/master HEAD`, else `merge-base origin/master HEAD`, else the fixture
    root (excluded)."""
    if not base:
        base = git_out(ws, "merge-base", "review/master", "HEAD")
    if not base:
        base = git_out(ws, "merge-base", "origin/master", "HEAD")
    if not base:
        roots = git_out(ws, "rev-list", "--max-parents=0", "HEAD").split()
        base = roots[-1] if roots else ""
    if not base:
        return []
    out = []
    for sha in git_out(ws, "rev-list", "--reverse", f"{base}..HEAD").split():
        body = git_out(ws, "show", "-s", "--format=%B", sha)
        cids = re.findall(r"^Change-Id:\s*(I[0-9a-f]{40})\s*$", body, re.M)
        out.append({"sha": sha, "change_id": cids[-1] if cids else None,
                    "subject": body.splitlines()[0] if body else "", "lines": commit_lines(ws, sha)})
    return out


def commit_lines(ws: str, sha: str) -> int:
    total = 0
    for ln in git_out(ws, "show", "--numstat", "--format=", sha).splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2:
            for p in parts[:2]:
                if p.isdigit():
                    total += int(p)
    return total


def sync_origin_with_review(ws: str) -> None:
    """The seed push rebased the workspace onto review/master (the demo Gerrit's history),
    so the fixture's local bare remote would no longer share a base with HEAD and every chain
    tool would count Gerrit's root commits as part of the chain. Point origin's master at
    review/master (a bare repo we own) and refresh origin/master."""
    if _git(ws, "rev-parse", "--verify", "-q", "refs/remotes/review/master").returncode != 0:
        return
    _git(ws, "push", "-q", "--force", "origin", "refs/remotes/review/master:refs/heads/master")
    _git(ws, "fetch", "-q", "origin")


def tag_chain(ws: str, chain: list[dict], prefix: str = "refs/bench/seed") -> None:
    for i, c in enumerate(chain, 1):
        _git(ws, "update-ref", f"{prefix}/{i}", c["sha"])

def workspace_verify_cmd(ws: str) -> str:
    return git_out(ws, "config", "--get", "gerrit-stack.verify-cmd") or DEFAULT_VERIFY_CMD


def run_chain_metrics(plugin_root: str, ws: str, hook_trace: str, out_path: str, case: Optional["Case"] = None) -> None:
    """scripts/chain-metrics.sh --json with --verify-cmd (the workspace's gerrit-stack.verify-cmd) and,
    for cases with a `concerns:` map, --concerns <case.yaml>. An older script that rejects the new flags
    (non-zero exit) is retried without --concerns and then without both."""
    script = os.path.join(plugin_root, "scripts", "chain-metrics.sh")
    if not os.path.exists(script):
        return
    base = ["bash", script, "--json", "--hook-trace", hook_trace]
    verify = ["--verify-cmd", workspace_verify_cmd(ws)]
    concerns = ["--concerns", case.case_yaml] if case is not None and case.has_concerns and case.case_yaml else []
    attempts: list[list[str]] = []
    for extra in (verify + concerns, verify, []):
        if extra not in attempts:
            attempts.append(extra)
    timed_out = False
    for extra in attempts:
        if timed_out and "--verify-cmd" in extra:
            continue
        try:
            rc = subprocess.run(base + extra + [ws], cwd=plugin_root, capture_output=True, text=True,
                                timeout=CHAIN_METRICS_TIMEOUT, env=fixture_env(), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            log(f"chain-metrics timed out after {CHAIN_METRICS_TIMEOUT}s ({' '.join(extra[:1]) or 'no extra flags'})")
            timed_out = True
            continue
        except OSError as exc:
            log(f"chain-metrics failed: {exc}")
            return
        if rc.returncode != 0:
            log(f"chain-metrics exited {rc.returncode} with {extra[::2] or 'no extra flags'}: {rc.stderr.strip()[:200]}")
            continue
        try:
            data = json.loads(rc.stdout)
        except ValueError:
            log("chain-metrics printed non-JSON output; ignored")
            continue
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return


# --------------------------------------------------------------------------
# Sandbox (tier 1): scrubbed environment, isolation fingerprint, capability
# --------------------------------------------------------------------------

def sandbox_env(extra: Optional[dict] = None, parent: Optional[dict] = None) -> dict:
    """The environment of a `claude` child (sessions and the judge): PATH HOME USER SHELL TMPDIR LANG
    TERM and LC_* from the parent, plus the runner's own EVAL_* / EVAL_PLUGIN_ROOT / GERRIT_HOST /
    GERRIT_STACK_TRACE from `extra`, plus ENABLE_CLAUDEAI_MCP_SERVERS=false (no account connectors).
    Nothing else crosses over — in particular no CLAUDE_* of a parent Claude Code session."""
    parent = os.environ if parent is None else parent
    env = {k: v for k, v in parent.items() if k in ENV_PASS or k.startswith("LC_")}
    for k, v in (extra or {}).items():
        if k in ENV_RUNNER or re.match(r"^EVAL_[A-Z0-9_]*$", str(k)):
            env[str(k)] = "" if v is None else str(v)
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    return env


def fixture_env(extra: Optional[dict] = None, parent: Optional[dict] = None) -> dict:
    """Fixtures and build tools keep the parent's environment (JAVA_HOME, proxies, …) except CLAUDE*."""
    parent = os.environ if parent is None else parent
    env = {k: v for k, v in parent.items() if not k.startswith("CLAUDE")}
    for k, v in (extra or {}).items():
        env[str(k)] = "" if v is None else str(v)
    return env


def env_summary(env: dict) -> str:
    """Names only for what came from the parent, name=value for what the runner set (no secrets there)."""
    inherited = sorted(k for k in env if k in ENV_PASS or k.startswith("LC_"))
    own = sorted(k for k in env if k not in inherited)
    return " ".join(inherited) + " + " + " ".join(f"{k}={env[k]}" for k in own)


def _names(items: Any) -> list[str]:
    out = []
    for it in items if isinstance(items, list) else []:
        name = it.get("name") if isinstance(it, dict) else it
        if isinstance(name, str) and name:
            out.append(name)
    return out


def _plugin_names(items: Any) -> list[str]:
    """Plugin names without a `@marketplace` / `@inline` suffix."""
    return [n.split("@", 1)[0] for n in _names(items)]


def _plugin_allowed(name: str, expected: set) -> bool:
    return name in expected or name.startswith("cc-plugin-")


def check_isolation(init: dict, arm: str) -> dict:
    """Compare a session's init record with the arm's allowlist -> isolation.json content.
    Plugins: the arm's plugins plus any cc-plugin-*; MCP servers: exactly ARM_MCP_SERVERS (a server
    that failed to connect still counts as loaded); namespaced skills/agents (`plugin:name`) only
    from allowed plugins. `missing` lists what the arm should have had and did not."""
    exp_plugins = set(ARM_PLUGINS.get(arm, ()))
    exp_mcp = set(ARM_MCP_SERVERS.get(arm, ()))
    plugins = _plugin_names(init.get("plugins"))
    servers = init.get("mcp_servers") if isinstance(init.get("mcp_servers"), list) else []
    mcp = _names(servers)
    skills = _names(init.get("skills"))
    agents = _names(init.get("agents"))

    def foreign(names: list[str]) -> list[str]:
        return sorted({n for n in names if ":" in n and not _plugin_allowed(n.split(":", 1)[0], exp_plugins)})

    unexpected = {
        "plugins": sorted({p for p in plugins if not _plugin_allowed(p, exp_plugins)}),
        "mcp_servers": sorted({m for m in mcp if m not in exp_mcp}),
        "skills": foreign(skills),
        "agents": foreign(agents),
    }
    missing: dict[str, Any] = {"plugins": sorted(exp_plugins - set(plugins)), "mcp_servers": sorted(exp_mcp - set(mcp))}
    if not init:
        missing = {"plugins": [], "mcp_servers": [], "init": True}
    ok = bool(init) and not any(unexpected.values()) and not missing["plugins"] and not missing["mcp_servers"]
    return {
        "ok": ok,
        "arm": arm,
        "unexpected": unexpected,
        "missing": missing,
        "fingerprint": {
            "plugins": sorted(set(plugins)), "mcp_servers": sorted(set(mcp)), "skills": sorted(set(skills)),
            "agents": sorted(set(agents)), "model": init.get("model"),
            "claude_code_version": init.get("claude_code_version"),
            "mcp_server_status": {s.get("name"): s.get("status") for s in servers if isinstance(s, dict) and s.get("name")},
        },
    }


def isolation_error(iso: dict) -> Optional[str]:
    """`isolation: unexpected …` / `isolation: missing …` for a failed check, None when ok."""
    if iso.get("ok"):
        return None
    parts = []
    unexpected = [f"{k}={v}" for k, v in (iso.get("unexpected") or {}).items() if v]
    if unexpected:
        parts.append("unexpected " + ", ".join(unexpected))
    missing = iso.get("missing") or {}
    if missing.get("init"):
        parts.append("no init record")
    lacking = [f"{k}={v}" for k, v in missing.items() if k != "init" and v]
    if lacking:
        parts.append("missing " + ", ".join(lacking))
    return "isolation: " + "; ".join(parts or ["check failed"])


def capability_counts(trace: Trace, hook_trace_path: str) -> dict:
    """How often the arm's distinguishing capabilities were exercised: MCP tool calls (and how many
    were refused for lack of permission), Skill calls, lines the plugin's hooks wrote to the trace."""
    mcp = [tc for tc in trace.tool_calls if tc.name.startswith("mcp__")]
    return {
        "mcp_calls": len(mcp),
        "mcp_denied": sum(1 for tc in mcp if MCP_DENIED_HINT in (tc.result or "")),
        "skill_calls": sum(1 for tc in trace.tool_calls if tc.name == "Skill"),
        "hook_lines": hook_trace_counts(hook_trace_path)["lines"],
    }


# --------------------------------------------------------------------------
# One session
# --------------------------------------------------------------------------

def new_run_record(run_dir: str, started: _dt.datetime) -> dict:
    return {
        "score": 0.0, "passed": False, "turns": None, "costUsd": 0.0, "judgeCostUsd": 0.0,
        "durationSeconds": 0.0, "startedAt": started.isoformat(), "error": None,
        "tracePath": os.path.join(run_dir, "trace.jsonl"), "graders": [],
    }


def session_model(case: Case, opts: argparse.Namespace) -> str:
    """--model, else the case's own `model:`, else the pinned default — never the account default."""
    return getattr(opts, "model", None) or case.model or DEFAULT_MODEL


def _join_error(a: Optional[str], b: Optional[str]) -> Optional[str]:
    return f"{a}; {b}" if a and b else (a or b)


def run_session(case: Case, prompt: str, arm: str, ws: str, env: dict, run_dir: str,
                opts: argparse.Namespace, judge_factory, label: str, error: Optional[str] = None,
                started: Optional[_dt.datetime] = None) -> dict:
    """One `claude -p` session in `ws` (scrubbed env, stdin=/dev/null, pinned model) graded with the
    case's graders; files land in `run_dir` (trace.jsonl, stderr.log, command.txt, last-message.md,
    hook-trace.log, chain-metrics.json, isolation.json). `error` pre-set (a failed fixture) skips the
    session but still produces a full record."""
    os.makedirs(run_dir, exist_ok=True)
    started = started or _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec = new_run_record(run_dir, started)
    hook_trace = os.path.join(run_dir, "hook-trace.log")
    open(hook_trace, "a").close()
    env = sandbox_env({**env, "GERRIT_STACK_TRACE": hook_trace}, parent=env)
    model = session_model(case, opts)
    before = snapshot(ws)
    ran = error is None
    if ran:
        cmd = build_claude_cmd(prompt, case, arm, opts.plugin_dir, model, mcp_plugin_dir=opts.mcp_dir)
        with open(os.path.join(run_dir, "command.txt"), "w", encoding="utf-8") as fh:
            fh.write(shlex.join(cmd) + "\n# env: " + " ".join(sorted(env)) + "\n# stdin: /dev/null\n")
        log(f"{label}: {shlex.join(cmd)[:160]}…")
        rc, timed_out = run_process(cmd, ws, env, case.timeout_seconds, rec["tracePath"],
                                    os.path.join(run_dir, "stderr.log"))
        if timed_out:
            error = f"timeout after {case.timeout_seconds}s"
        elif rc != 0:
            error = f"claude exited {rc}"
    trace = parse_trace_file(rec["tracePath"])
    if trace.result.get("subtype") not in (None, "success") and error is None:
        error = f"result subtype {trace.result.get('subtype')}"
    if error and trace.result.get("subtype") == "success":
        error = None  # a non-zero exit with a successful result is still gradable
    if trace.result.get("is_error"):
        status = trace.result.get("api_error_status")
        error = f"api error{f' {status}' if status else ''}: {str(trace.result.get('result') or '')[:120]}"
    rec["turns"] = trace.num_turns
    rec["costUsd"] = trace.cost_usd
    rec["model"] = model
    rec["modelReported"] = trace.init.get("model")
    rec["subtype"] = trace.result.get("subtype")
    rec["isolation"] = None
    rec["capability"] = None
    if ran:
        iso = check_isolation(trace.init, arm)
        cap = capability_counts(trace, hook_trace)
        _write_json(os.path.join(run_dir, "isolation.json"), {**iso, "capability": cap})
        rec["isolation"], rec["capability"] = iso, cap
        if not iso["ok"] and not (error and (iso.get("missing") or {}).get("init")):
            error = _join_error(error, isolation_error(iso))  # a session that never started keeps its own error
    with open(os.path.join(run_dir, "last-message.md"), "w", encoding="utf-8") as fh:
        fh.write(trace.last_message)
    changed = changed_since(before, snapshot(ws))
    rec["changedFiles"] = changed
    try:
        with open(rec["tracePath"], encoding="utf-8", errors="replace") as fh:
            trace_text = fh.read()
    except OSError:
        trace_text = ""
    kill_stub(ws)
    run_chain_metrics(opts.plugin_dir, ws, hook_trace, os.path.join(run_dir, "chain-metrics.json"), case)
    judge = judge_factory(run_dir) if judge_factory else None
    ctx = GradeContext(trace, trace_text, ws, changed, judge)
    results = [grade(g, ctx, arm) for g in case.graders]
    rec["graders"] = results
    rec["judgeCostUsd"] = round(ctx.judge_cost, 6)
    rec["score"] = score_graders(results)
    rec["passed"] = rec["score"] >= opts.threshold and error is None
    rec["error"] = error
    rec["durationSeconds"] = round(time.time() - t0, 3)
    return rec


def make_workspace(case: Case, opts: argparse.Namespace, run_dir: str) -> tuple[str, str, dict, Optional[str]]:
    """Temp dir + workspace; runs the fixture (parent env minus CLAUDE*). -> (tmp, ws, session env, error);
    the session env is the scrubbed allowlist."""
    tmp = tempfile.mkdtemp(prefix=f"gs-eval-{case.name}-")
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    hook_trace = os.path.join(run_dir, "hook-trace.log")
    open(hook_trace, "a").close()
    own = dict(case.env)
    own["EVAL_PLUGIN_ROOT"] = opts.plugin_dir
    own["GERRIT_STACK_TRACE"] = hook_trace
    error: Optional[str] = None
    if case.scaffold_script:
        rc, timed_out = run_process(["bash", case.scaffold_script], ws, fixture_env(own), 600,
                                    os.path.join(run_dir, "fixture.stdout"), os.path.join(run_dir, "fixture.stderr"))
        if rc != 0 or timed_out:
            error = f"fixture exited {rc}" + (" (timeout)" if timed_out else "")
    return tmp, ws, sandbox_env(own), error


def finish_workspace(tmp: str, ws: str, run_dir: str, rec: dict, keep: bool) -> None:
    kill_stub(ws)
    if keep:
        dest = os.path.join(run_dir, "workspace")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(tmp, dest)
        rec["workspace"] = os.path.join(dest, "workspace")
    else:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Gerrit REST (as rena; admin only for the project-config toggle and drafts)
# --------------------------------------------------------------------------

class RestError(RuntimeError):
    pass


_urlopen = urllib.request.urlopen  # patched by the unit tests


def gerrit_from_push_url(url: str) -> tuple[str, str, str]:
    """http://host:port[/prefix]/a/<project> -> (base 'scheme://host[:port][/prefix]', auth prefix '/a' or '',
    project = last path segment)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"--push-to must be an http(s) Gerrit project URL, got {url!r}")
    path = parts.path.rstrip("/")
    marker = path.find("/a/")
    if marker >= 0:
        head, prefix, project = path[:marker], "/a", path[marker + 3:]
    else:
        head, prefix, project = "", "", path.lstrip("/")
    if not project:
        raise ValueError(f"--push-to has no project path: {url!r}")
    return f"{parts.scheme}://{parts.netloc}{head}", prefix, project


def read_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        raise RestError(f"reviewer token file {path} is missing or unreadable (run: bash demo/seed.sh)") from None
    if not token:
        raise RestError(f"reviewer token file {path} is empty (run: bash demo/seed.sh)")
    return token


class GerritRest:
    """Minimal JSON client. The credentials only ever live in the Authorization header."""

    def __init__(self, base: str, prefix: str, user: str, token: str, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.prefix = prefix
        self.timeout = timeout
        self._auth = "Basic " + base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")

    def url(self, path: str) -> str:
        return f"{self.base}{self.prefix}{path}"

    def call(self, method: str, path: str, body: Any = None) -> Any:
        url = self.url(path)
        headers = {"Accept": "application/json", "Authorization": self._auth}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                snippet = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                snippet = ""
            raise RestError(f"{method} {url} -> HTTP {exc.code}: {snippet.strip()}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RestError(f"{method} {url}: {getattr(exc, 'reason', exc)}") from None
        text = raw.decode("utf-8", errors="replace")
        if text.startswith(")]}'"):
            text = text[4:]
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.call("POST", path, body)

    def put(self, path: str, body: Any) -> Any:
        return self.call("PUT", path, body)


def netrc_auth(base_url: str, env: Optional[dict] = None) -> Optional[tuple[str, str]]:
    """(login, password) for the base URL's hostname from $NETRC or ~/.netrc; None when absent."""
    env = os.environ if env is None else env
    hostname = urllib.parse.urlsplit(base_url).hostname
    if not hostname:
        return None
    path = env.get("NETRC") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".netrc")
    try:
        entry = netrc.netrc(path).authenticators(hostname)
    except (OSError, netrc.NetrcParseError, ValueError):
        return None
    if not entry or not entry[0] or not entry[2]:
        return None
    return entry[0], entry[2]


def admin_rest(base_url: str, prefix: str, env: Optional[dict] = None) -> Optional[GerritRest]:
    """Admin client from the netrc entry (project-config toggle, reading the admin's drafts); None without one."""
    auth = netrc_auth(base_url, env)
    return GerritRest(base_url, prefix, auth[0], auth[1]) if auth else None


def set_require_change_id(rest: GerritRest, project: str, value: str) -> Any:
    """PUT /projects/<p>/config {"require_change_id": value} (TRUE|FALSE|INHERIT). Batch pushes from the
    arms without hooks carry commits without a Change-Id (after --no-verify); FALSE lets Gerrit accept
    them as new changes so the loss shows up in the metrics instead of a rejected push."""
    if value not in ("TRUE", "FALSE", "INHERIT"):
        raise ValueError(f"require_change_id must be TRUE|FALSE|INHERIT, got {value!r}")
    return rest.put(f"/projects/{_q(project)}/config", {"require_change_id": value})


def map_change_ids_by_sha(chain: list[dict], gerrit_changes: list[dict]) -> int:
    """Commits without a Change-Id trailer take the change_id of the Gerrit change whose current
    revision is that sha (in place). Returns how many were mapped."""
    by_sha = {c.get("current_revision"): c.get("change_id") for c in gerrit_changes if c.get("current_revision")}
    mapped = 0
    for c in chain:
        if not c.get("change_id") and by_sha.get(c["sha"]):
            c["change_id"] = by_sha[c["sha"]]
            c["change_id_from_gerrit"] = True
            mapped += 1
    return mapped


def commit_leftovers(ws: str, case_name: str, ctype: str = "feat") -> bool:
    """Work the agent left uncommitted is committed as ONE commit on top (never an amend) through the
    fixture's commit-msg hook (so it carries a Change-Id) so it can still be pushed and measured.
    Returns True when a commit was made."""
    if not git_out(ws, "status", "--porcelain").strip():
        return False
    subject = f"{ctype}: {case_name} {RUNNER_COMMIT_NOTE}"
    _git(ws, "add", "-A")
    r = _git(ws, "-c", "user.name=Benchmark Runner", "-c", "user.email=bench-runner@example.com",
             "commit", "-q", "-m", subject)
    if r.returncode != 0:
        warn(f"runner commit of leftover work failed: {r.stderr.strip()[:200]}")
        return False
    return True


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def query_changes_by_hashtags(rest: GerritRest, project: str, hashtags: list[str]) -> list[dict]:
    q = " ".join([f"project:{project}"] + [f"hashtag:{h}" for h in hashtags])
    opts = "&".join("o=" + o for o in ("CURRENT_REVISION", "CURRENT_COMMIT", "DETAILED_LABELS", "DETAILED_ACCOUNTS", "MESSAGES"))
    data = rest.get(f"/changes/?q={_q(q)}&n=200&{opts}")
    return [c for c in (data or []) if isinstance(c, dict)]


def change_files(rest: GerritRest, number: int) -> dict:
    data = rest.get(f"/changes/{number}/revisions/current/files") or {}
    return {k: v for k, v in data.items() if not k.startswith("/")}


def change_file_content(rest: GerritRest, number: int, path: str) -> str:
    data = rest.get(f"/changes/{number}/revisions/current/files/{_q(path)}/content")
    if not isinstance(data, str):
        return ""
    # Gerrit documents base64 here but the demo instance answers with the plain text when
    # the client accepts JSON; only decode what is strictly base64 (no newlines, valid alphabet).
    if "\n" in data or not re.fullmatch(r"[A-Za-z0-9+/=\s]+", data):
        return data
    try:
        return base64.b64decode(data, validate=True).decode("utf-8", errors="replace")
    except (ValueError, TypeError, binascii.Error):
        return data


def change_comments(rest: GerritRest, number: int) -> dict:
    return rest.get(f"/changes/{number}/comments") or {}


def gerrit_branch_sha(rest: GerritRest, project: str, branch: str = "master") -> Optional[str]:
    try:
        data = rest.get(f"/projects/{_q(project)}/branches/{_q(branch)}")
    except RestError:
        return None
    return data.get("revision") if isinstance(data, dict) else None


def _first_source_file(files: dict) -> Optional[str]:
    paths = sorted(files)
    for pat in (r"^src/main/.*\.java$", r"\.java$", r"."):
        for p in paths:
            if re.search(pat, p):
                return p
    return None


def anchor_line(content: str, pattern: Optional[str]) -> int:
    if not pattern:
        return 1
    rx = re.compile(str(pattern))
    for i, ln in enumerate(content.splitlines(), 1):
        if rx.search(ln):
            return i
    return 1


def render_message(template: str, **kw: Any) -> str:
    out = str(template)
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def with_nudge(prompt: str, nudge: Optional[str]) -> str:
    return prompt + "\n\n" + nudge if nudge else prompt


def _regex_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [re.compile(str(v)) for v in value if v is not None and str(v) != ""]


def find_change_by_subject(pattern: Any, changes: list[dict], what: str) -> dict:
    """First seeded change (chain order) whose subject matches the regex; ValueError names the key."""
    if not changes:
        raise ValueError("no seeded changes to pick a target from")
    if not pattern:
        raise ValueError(f"{what} is missing in case.yaml")
    rx = re.compile(str(pattern))
    for c in changes:
        if rx.search(str(c.get("subject") or "")):
            return c
    raise ValueError(f"no seeded change matches {what} /{pattern}/ (subjects: "
                     + "; ".join(str(c.get("subject")) for c in changes) + ")")


def select_rework_target(spec: dict, changes: list[dict], files_of: Callable[[int], dict],
                         content_of: Callable[[int, str], str]) -> dict:
    """The change rena comments on: `target_subject` over the seeded subjects, `file` over that change's
    file list (fallback: its first source file), `line` over the file content (default / no match: 1).
    -> {change, path, line, message, reason}."""
    target = find_change_by_subject(spec.get("target_subject"), changes, "rework.target_subject")
    num = int(target["_number"])
    try:
        files = files_of(num)
    except RestError:
        files = {}
    reason = f"target_subject /{spec.get('target_subject')}/"
    path = None
    if spec.get("file"):
        rx = re.compile(str(spec["file"]))
        hits = sorted(p for p in files if rx.search(p))
        if hits:
            path = hits[0]
            reason += f", file /{spec['file']}/"
    if path is None:
        path = _first_source_file(files)
        reason += ", file fallback: first source file" if path else ", no file: change-level comment"
    line = 1
    if path and spec.get("line"):
        try:
            line = anchor_line(content_of(num, path), spec.get("line"))
        except RestError:
            line = 1
    return {"change": target, "path": path, "line": line,
            "message": str(spec.get("message") or DEFAULT_REWORK_MESSAGE), "reason": reason}


def build_review_payload(path: Optional[str], line: int, message: str) -> dict:
    """The only vote the runner ever posts: rena's Code-Review -1 with one unresolved thread."""
    payload: dict[str, Any] = {"labels": {"Code-Review": -1}, "message": REVIEW_MESSAGE}
    if path:
        payload["comments"] = {path: [{"line": int(line), "unresolved": True, "message": message}]}
    else:
        payload["message"] = REVIEW_MESSAGE + "\n\n" + message
    return payload


class Pipeline:
    """One case × variant. The key is `<case>` for cases that have no nudged variant (every review
    case, implement cases without `nudges.stage1`, rework cases without `rework.nudge`) and
    `<case>@<variant>` otherwise — it depends on the case alone, never on the --variants given."""

    def __init__(self, case: Case, variant: str = "natural"):
        self.case = case
        self.kind = case.kind
        self.variant = variant
        self.key = f"{case.name}@{variant}" if case.nudge() else case.name

    @property
    def name(self) -> str:
        return self.key

    def prompt(self, changes: Optional[list[int]] = None, url: str = "", project: str = "",
               target: Optional[int] = None) -> str:
        """implement: prompt.md as is; rework/review: prompt.md with {changes} {url} {project} {target}
        filled in. The nudged variant appends the case's nudge line."""
        text = self.case.prompt
        if self.kind != "implement":
            text = render_message(text, changes=", ".join(str(c) for c in changes or []) or "<seeded changes>",
                                  url=url, project=project, target=target if target is not None else "<target change>")
        return with_nudge(text, self.case.nudge() if self.variant == "nudged" else None)


# ---- guardrails ---------------------------------------------------------

_SEG_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_GIT_SEG_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*git\s+(.*)$", re.S)


def git_segments(command: str) -> list[tuple[str, list[str]]]:
    """[(verb, args)] for every `git <verb> …` segment of a shell command (global options skipped)."""
    out = []
    for seg in _SEG_SPLIT_RE.split(command or ""):
        m = _GIT_SEG_RE.match(seg.strip())
        if not m:
            continue
        try:
            toks = shlex.split(m.group(1))
        except ValueError:
            toks = m.group(1).split()
        i = 0
        while i < len(toks) and toks[i].startswith("-"):
            if toks[i] in ("-C", "-c") and i + 1 < len(toks):
                i += 2
            else:
                i += 1
        if i < len(toks):
            out.append((toks[i], toks[i + 1:]))
    return out


def _short_flag(args: list[str], letter: str) -> bool:
    return any(re.match(r"^-[a-zA-Z]+$", a) and letter in a[1:] for a in args)


def detect_bad_commands(commands: list[str]) -> dict:
    """Bad outcomes from Bash commands (any arm): --no-verify/-n on commit, --amend with -m,
    force push (--force/-f/+refspec), a topic on push, a push to refs/heads/."""
    out = {"no_verify_used": 0, "amend_m_used": 0, "force_push_attempted": 0,
           "topic_used_unasked": 0, "refs_heads_push_attempted": 0}
    for cmd in commands:
        for verb, args in git_segments(cmd):
            if verb == "commit":
                if "--no-verify" in args or _short_flag(args, "n"):
                    out["no_verify_used"] += 1
                if "--amend" in args and (_short_flag(args, "m") or any(a.startswith(("-m", "--message")) for a in args)):
                    out["amend_m_used"] += 1
            elif verb == "push":
                if ("--force" in args or "--force-with-lease" in args or _short_flag(args, "f")
                        or any(a.startswith("+") for a in args)):
                    out["force_push_attempted"] += 1
                joined = " ".join(args)
                if re.search(r"[%,]topic=", joined) or re.search(r"(^|\s)-o\s*topic=", joined) \
                        or any(a.startswith("topic=") for a in args):
                    out["topic_used_unasked"] += 1
                if any("refs/heads/" in a for a in args):
                    out["refs_heads_push_attempted"] += 1
    return out


def bash_commands(trace: Trace) -> list[str]:
    out = []
    for tc in trace.tool_calls:
        if tc.name == "Bash" and isinstance(tc.input, dict) and isinstance(tc.input.get("command"), str):
            out.append(tc.input["command"])
    return out


_DENY_HINT_RE = re.compile(r"hook|denied|blocked|not allowed|refused", re.IGNORECASE)


def hook_trace_counts(path: str) -> dict:
    counts = {"deny": 0, "ask": 0, "feedback": 0, "lines": 0}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.rstrip("\r\n")
                if not raw.strip():
                    continue
                parts = raw.split("\t") if "\t" in raw else raw.split()
                counts["lines"] += 1
                decision = parts[2].strip() if len(parts) >= 3 else ""
                if decision in counts:
                    counts[decision] += 1
    except OSError:
        pass
    return counts


def trace_guard_counts(trace: Trace) -> dict:
    """asks/denies/self-corrections from the trace alone (arms without hooks): a Bash call whose
    error result mentions a hook/denial counts as a deny; a later successful Bash call sharing a git
    verb is the self-correction. Same heuristics as evals/metrics/collect.py."""
    asks = sum(1 for tc in trace.tool_calls if tc.name == "AskUserQuestion")
    denied = []
    for tc in trace.tool_calls:
        if tc.name == "Bash" and tc.is_error and _DENY_HINT_RE.search(tc.result or ""):
            cmd = tc.input.get("command") if isinstance(tc.input, dict) else ""
            denied.append((tc.index, {v for v, _ in git_segments(str(cmd or ""))}))
    fixed = 0
    for idx, verbs in denied:
        for later in trace.tool_calls[idx + 1:]:
            if later.name != "Bash" or later.result is None or later.is_error:
                continue
            cmd = later.input.get("command") if isinstance(later.input, dict) else ""
            if not verbs or verbs & {v for v, _ in git_segments(str(cmd or ""))}:
                fixed += 1
                break
    return {"asks": asks, "denies": len(denied), "self_corrections": fixed}


def stage_guardrails(trace: Trace, hook_trace_path: str, chain_metrics: Optional[dict],
                     refs_heads: tuple[Optional[str], Optional[str]],
                     gerrit_master: tuple[Optional[str], Optional[str]],
                     left_uncommitted: Optional[bool] = None) -> dict:
    hook = hook_trace_counts(hook_trace_path)
    heur = trace_guard_counts(trace)
    if hook["lines"] > 0:
        asks, denies, selfc = hook["ask"] + heur["asks"], hook["deny"], heur["self_corrections"]
    else:
        asks, denies, selfc = heur["asks"], heur["denies"], heur["self_corrections"]
    bad = detect_bad_commands(bash_commands(trace))
    no_cid = None
    if isinstance(chain_metrics, dict) and isinstance(chain_metrics.get("changes"), list):
        no_cid = sum(1 for c in chain_metrics["changes"] if isinstance(c, dict) and not c.get("change_ids"))
    bad["commit_without_change_id"] = no_cid
    b0, b1 = refs_heads
    g0, g1 = gerrit_master
    bad["refs_heads_moved"] = (b0 != b1) if (b0 and b1) else None
    bad["gerrit_master_moved"] = (g0 != g1) if (g0 and g1) else None
    bad["left_uncommitted"] = None if left_uncommitted is None else int(bool(left_uncommitted))
    return {"asks": asks, "denies": denies, "self_corrections": selfc, "bad_outcomes": bad}

# ---- drafted comments, conventions --------------------------------------

_FILE_LINE_RE = re.compile(r"[\w./-]+\.[A-Za-z]{1,8}(?::\d+|#L?\d+|,? line \d+)")


def _label_info(text: str) -> tuple[Optional[str], bool]:
    """(first Conventional Comments label, carries a `(blocking)` decoration) of a comment text."""
    label, blocking = None, False
    for m in CONVENTIONAL_RE.finditer(text or ""):
        label = label or m.group(1)
        deco = m.group(2) or ""
        if "blocking" in deco and "non-blocking" not in deco:
            blocking = True
    return label, blocking


_LOCATOR_RE = re.compile(r"`?[\w./-]+\.[A-Za-z]{1,8}`?(?::\d+|#L?\d+|,? lines? \d+)")


def last_message_comments(text: str) -> list[dict]:
    """Drafted review comments or replies in the final message. Two shapes are recognised:
    (1) a locator line (`file.ext:12`, "`file.ext`, line 12") opens a comment whose text is the rest
    of that line plus the blockquote / paragraph that follows it — counted whether or not it carries
    a Conventional Comments label; (2) a line outside any such block that carries a label (table
    rows, replies). A bare locator with no text (a heading) is not a comment.
    -> [{source, text, label, blocking}]"""
    lines = (text or "").splitlines()
    out, used = [], set()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        ref = _LOCATOR_RE.search(ln) if ln and not ln.startswith(">") else None
        # a locator opens a comment only when it leads the line (after list/heading/table markup);
        # "See Config.java:9." in running prose is a mention, not a drafted comment
        if ref and not re.fullmatch(r"[\s#*\-|`(\[\d.]*", ln[:ref.start()]):
            ref = None
        if not ref:
            i += 1
            continue
        same = (ln[:ref.start()] + " " + ln[ref.end():]).strip()
        same_words = len(re.sub(r"[^\w\s]", " ", same).split())
        body, j = [], i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        quoted = j < len(lines) and lines[j].lstrip().startswith(">")
        while j < len(lines):
            cur = lines[j].strip()
            if not cur:
                if not quoted:
                    break
                k = j + 1
                while k < len(lines) and not lines[k].strip():
                    k += 1
                if k >= len(lines) or not lines[k].lstrip().startswith(">"):
                    break
                j = k
                continue
            if quoted and not cur.startswith(">"):
                break
            if not quoted and (cur.startswith("#") or _LOCATOR_RE.search(cur)):
                break
            body.append(cur.lstrip("> ").strip())
            used.add(j)
            j += 1
        body_text = " ".join(b for b in body if b)
        if same_words >= 3 and not body_text:
            comment = same
        elif body_text:
            comment = body_text
        else:
            i += 1
            continue
        used.add(i)
        label, blocking = _label_info(comment[:240] if not same_words >= 3 else same + " " + comment[:240])
        out.append({"source": "last_message", "text": (ln + " " + comment).strip(), "label": label, "blocking": blocking})
        i = max(j, i + 1)
    for n, raw in enumerate(lines):
        if n in used:
            continue
        ln = raw.strip()
        if not ln or re.fullmatch(r"[|\s:=-]+", ln):
            continue
        label, blocking = _label_info(ln)
        if label is not None:
            out.append({"source": "last_message", "text": ln, "label": label, "blocking": blocking})
    return out


def drafted_comments(last_message: str, drafts: Optional[list[dict]]) -> list[dict]:
    """Gerrit drafts plus the last-message comment lines; a line that only repeats a Gerrit draft (same
    opening text) is not counted twice."""
    out = []
    openings = []
    for d in drafts or []:
        msg = str(d.get("message") or "")
        label, blocking = _label_info(msg)
        out.append({"source": "gerrit_draft", "text": msg, "label": label, "blocking": blocking,
                    "path": d.get("path"), "change": d.get("change")})
        opening = " ".join(msg.split())[:60]
        if len(opening) >= 12:
            openings.append(opening)
    for c in last_message_comments(last_message):
        flat = " ".join(c["text"].split())
        if any(o in flat for o in openings):
            continue
        out.append(c)
    return out


def find_commitlint(ws: str) -> Optional[str]:
    """Path of `commitlint` when it is on PATH and the workspace carries a commitlint config."""
    exe = shutil.which("commitlint")
    if not exe:
        return None
    try:
        names = os.listdir(ws)
    except OSError:
        return None
    if any(n.startswith(("commitlint.config.", ".commitlintrc")) for n in names):
        return exe
    pkg = _read_json(os.path.join(ws, "package.json"))
    return exe if pkg and "commitlint" in pkg else None


def commit_conforms(ws: str, message: str, commitlint: Optional[str]) -> bool:
    """commitlint (run inside the workspace, message on stdin) or the Conventional Commits regex."""
    if commitlint:
        try:
            r = subprocess.run([commitlint], cwd=ws, input=message, capture_output=True, text=True, timeout=120,
                               env=fixture_env())
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"commitlint failed ({exc}); regex fallback for this commit")
    subject = message.splitlines()[0] if message else ""
    return CONVENTIONAL_COMMIT_RE.match(subject) is not None


def compute_conventions(ws: str, chain: list[dict], comments: list[dict]) -> dict:
    """conventions.json: the chain's commit messages against the repo's commitlint config (regex
    fallback) and the labelled share of the drafted comments. Commits made by the runner are left out."""
    commitlint = find_commitlint(ws)
    own = [c for c in chain if RUNNER_COMMIT_NOTE not in str(c.get("subject") or "")]
    conforming = 0
    for c in own:
        message = git_out(ws, "show", "-s", "--format=%B", c["sha"]) or str(c.get("subject") or "")
        if commit_conforms(ws, message + "\n", commitlint):
            conforming += 1
    return {
        "commit_subjects_total": len(own),
        "commit_subjects_conforming": conforming,
        "commitlint_available": commitlint is not None,
        "comments_total": len(comments),
        "comments_labelled": sum(1 for c in comments if c.get("label")),
    }


# ---- rework metrics -----------------------------------------------------

def patch_signature(ws: str, sha: str) -> list[str]:
    """The +/- lines of a commit's patch (no hunk headers, no context, no index lines), so two
    versions of the same change compare equal after a rebase that only shifted line numbers."""
    out = []
    for ln in git_out(ws, "show", "--format=", "--no-color", "-M", sha).splitlines():
        if ln.startswith("diff --git "):
            out.append(ln)
        elif ln.startswith(("+++ ", "--- ")):
            continue
        elif ln.startswith(("+", "-")):
            out.append(ln)
    return out


def interdiff_lines(old: list[str], new: list[str]) -> int:
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            n += (i2 - i1) + (j2 - j1)
    return n


def conflict_markers_left(ws: str, rev: str = "HEAD") -> int:
    """Lines starting with <<<<<<< or >>>>>>> anywhere in the tree of `rev`."""
    r = _git(ws, "grep", "-c", "-I", "-E", r"^(<<<<<<<|>>>>>>>)", rev, "--")
    total = 0
    for ln in r.stdout.splitlines():
        tail = ln.rsplit(":", 1)[-1]
        if tail.isdigit():
            total += int(tail)
    return total


REWORK_METRIC_KEYS = (
    "target_change", "target_change_id", "seeded_changes", "final_changes", "change_id_set_preserved",
    "order_preserved", "fix_on_target", "untouched_identical", "untouched_total", "may_change_changed",
    "new_changes_opened", "fixups_left", "builds_alone_pct", "conflict_markers_left", "interdiff_lines",
    "reply_drafted", "reply_labelled", "reply_posted", "vote_posted", "runner_committed",
    "lost_change_ids", "interdiff_by_change", "seed_push_error", "second_push_error")


def null_rework_metrics(seeded_numbers: Optional[list[int]] = None) -> dict:
    """rework-metrics.json for a run that stopped before the read-back: every key present, null."""
    m: dict[str, Any] = {k: None for k in REWORK_METRIC_KEYS}
    m["seeded_changes"] = list(seeded_numbers or [])
    return m


def may_change_ids(spec: dict, seeded: list[dict]) -> set:
    """Change-Ids of the seeded changes whose subject matches one of `rework.may_change`."""
    pats = _regex_list(spec.get("may_change"))
    return {c["change_id"] for c in seeded
            if c.get("change_id") and any(rx.search(str(c.get("subject") or "")) for rx in pats)}


def compute_rework_metrics(ws: str, seeded: list[dict], final: list[dict], target_change_id: Optional[str],
                           may_change: set, seeded_numbers: dict, final_numbers: dict, last_message: str,
                           gerrit_after: Optional[dict] = None, reviewer_id: Optional[int] = None,
                           review_time: Optional[str] = None, chain_metrics: Optional[dict] = None,
                           drafts: Optional[list[dict]] = None, runner_committed: Optional[bool] = None) -> dict:
    """rework-metrics.json per the contract. Chains are bottom-to-top [{sha, change_id, subject}];
    `seeded_numbers` / `final_numbers` map Change-Id -> Gerrit number. Patches are compared by their
    +/- lines, so a change that was only rebased counts as identical."""
    s_ids = [c["change_id"] for c in seeded if c.get("change_id")]
    f_ids = [c.get("change_id") for c in final]
    s_by = {c["change_id"]: c for c in seeded if c.get("change_id")}
    f_by = {c["change_id"]: c for c in final if c.get("change_id")}
    sig_s = {cid: patch_signature(ws, c["sha"]) for cid, c in s_by.items()}
    sig_f = {cid: patch_signature(ws, c["sha"]) for cid, c in f_by.items()}
    inter = {cid: interdiff_lines(sig_s[cid], sig_f[cid]) for cid in s_by if cid in f_by}
    protected = {target_change_id} | set(may_change) if target_change_id else set(may_change)
    untouched = [c for c in s_ids if c not in protected]
    m = null_rework_metrics()
    m.update({
        "target_change": seeded_numbers.get(target_change_id) if target_change_id else None,
        "target_change_id": target_change_id,
        "seeded_changes": [seeded_numbers[c] for c in s_ids if c in seeded_numbers],
        "final_changes": [final_numbers[c] for c in f_ids if c in final_numbers],
        "change_id_set_preserved": set(s_ids) == set(f_ids) if s_ids else None,
        "order_preserved": ([c for c in s_ids if c in f_by] == [c for c in f_ids if c in s_by]) if s_ids else None,
        "fix_on_target": (inter.get(target_change_id, 0) > 0) if target_change_id else None,
        "untouched_identical": sum(1 for c in untouched if inter.get(c) == 0),
        "untouched_total": len(untouched),
        "may_change_changed": sum(1 for c in s_ids if c in may_change and c != target_change_id and inter.get(c) != 0),
        "new_changes_opened": sum(1 for c in f_ids if c not in s_by),
        "fixups_left": sum(1 for c in final if re.match(r"^(fixup|squash|amend)! ", str(c.get("subject") or ""))),
        "builds_alone_pct": chain_metrics.get("builds_alone_pct") if isinstance(chain_metrics, dict) else None,
        "conflict_markers_left": conflict_markers_left(ws),
        "interdiff_lines": inter.get(target_change_id) if target_change_id else None,
        "runner_committed": runner_committed,
        "lost_change_ids": sorted(set(s_ids) - set(f_by)),
        "interdiff_by_change": {str(seeded_numbers.get(c, c)): v for c, v in inter.items()},
    })
    text = last_message or ""
    replies = drafted_comments(text, drafts)
    m["reply_labelled"] = any(c.get("label") for c in replies)
    m["reply_drafted"] = bool(drafts) or (bool(text.strip()) and (
        m["reply_labelled"] or re.search(r"(?i)\b(reply|replies|response|respond|reviewer)\b", text) is not None))
    if gerrit_after is not None:
        m["reply_posted"], m["vote_posted"] = posted_by_others(gerrit_after, reviewer_id, review_time,
                                                              set(m["seeded_changes"]))
    return m


# ---- review metrics -----------------------------------------------------

def message_blocks(text: str, basenames: set) -> list[tuple[set, str]]:
    """Split a final message into comment blocks: a block starts at a line that names one of the
    change's files (by basename) and runs until the next such line or the next heading.
    -> [(basenames named on the first line, block text)]"""
    blocks: list[tuple[set, list[str]]] = []
    cur: Optional[list[str]] = None
    for raw in (text or "").splitlines():
        ln = raw.strip()
        named = {b for b in basenames if b and b in ln}
        if named:
            cur = [ln]
            blocks.append((named, cur))
        elif ln.startswith("#"):
            cur = None
        elif cur is not None and ln:
            cur.append(ln)
    return [(named, "\n".join(lines)) for named, lines in blocks]


def planted_findings(planted: list, target_files: list[str], last_message: str, drafts: Optional[list[dict]]) -> list[dict]:
    """Per planted defect: found when >=1 keyword (case-insensitive) appears in a Gerrit draft on a
    file matching the defect's `file` regex, or in a final-message block that names that file's
    basename. -> [{id, kind, found, where, blocking_marked}]"""
    basenames_all = {os.path.basename(p) for p in target_files}
    blocks = message_blocks(last_message, basenames_all)
    out = []
    for i, p in enumerate(planted or []):
        if not isinstance(p, dict):
            continue
        rx = re.compile(str(p.get("file") or "."))
        kws = p.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        kws = [str(k).lower() for k in kws if str(k)]
        wanted = {os.path.basename(f) for f in target_files if rx.search(f)}
        hits = []
        for d in drafts or []:
            msg = str(d.get("message") or "")
            if rx.search(str(d.get("path") or "")) and any(k in msg.lower() for k in kws):
                hits.append(("gerrit_draft", msg))
        for named, block in blocks:
            if named & wanted and any(k in block.lower() for k in kws):
                hits.append(("last_message", block))
        marked = False
        for _src, text in hits:
            label, blocking = _label_info(text)
            if blocking or label == "issue":
                marked = True
        out.append({"id": str(p.get("id") or f"planted-{i + 1}"), "kind": str(p.get("kind") or ""),
                    "found": bool(hits), "where": sorted({s for s, _ in hits}), "blocking_marked": marked})
    return out


def compute_review_metrics(spec: dict, target_files: list[str], last_message: str, drafts: Optional[list[dict]],
                           gerrit_after: Optional[dict], reviewer_id: Optional[int], since: Optional[str],
                           numbers: set) -> dict:
    """review-metrics.json per the contract. `drafts` = the admin's Gerrit drafts on the seeded changes
    (None when they could not be read); published comments / votes = anything on the seeded changes by
    an account other than rena after the session started."""
    findings = planted_findings(spec.get("planted") or [], target_files, last_message, drafts)
    comments = drafted_comments(last_message, drafts)
    published = votes = None
    if gerrit_after is not None:
        published, votes = posted_by_others(gerrit_after, reviewer_id, since, numbers)
    return {
        "planted_total": len(findings),
        "planted_found": sum(1 for f in findings if f["found"]),
        "found_ids": [f["id"] for f in findings if f["found"]],
        "comments_total": len(comments),
        "comments_labelled": sum(1 for c in comments if c.get("label")),
        "blocking_marked_correct": sum(1 for f in findings if f["kind"] == "blocking" and f["found"] and f["blocking_marked"]),
        "published_comments": published,
        "votes_posted": votes,
        "drafts_created": len(drafts) if drafts is not None else None,
        "planted": findings,
    }


def _after(ts: Optional[str], since: Optional[str]) -> bool:
    return bool(ts) and (not since or str(ts)[:19] >= since[:19])


def posted_by_others(gerrit_after: dict, reviewer_id: Optional[int], since: Optional[str],
                     numbers: set) -> tuple[int, int]:
    """(replies, votes) on the seeded changes by any account other than the reviewer after `since`: change messages (non-autogenerated), inline comments and non-zero label votes."""
    replies = votes = 0
    for c in gerrit_after.get("changes") or []:
        if numbers and int(c.get("_number", -1)) not in numbers:
            continue
        for msg in c.get("messages") or []:
            author = (msg.get("author") or {}).get("_account_id")
            if author == reviewer_id or str(msg.get("tag") or "").startswith("autogenerated:"):
                continue
            if re.match(r"^Uploaded patch set \d+", str(msg.get("message") or "")):
                continue
            if _after(msg.get("date"), since):
                replies += 1
        for votes_on in (c.get("labels") or {}).values():
            for v in (votes_on or {}).get("all") or []:
                if v.get("_account_id") != reviewer_id and int(v.get("value") or 0) != 0 and _after(v.get("date"), since):
                    votes += 1
        for thread in ((gerrit_after.get("comments") or {}).get(str(c.get("_number"))) or {}).values():
            for cm in thread or []:
                if (cm.get("author") or {}).get("_account_id") != reviewer_id and _after(cm.get("updated"), since):
                    replies += 1
    return replies, votes


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)


def _utc_now_str() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def remote_master_sha(ws: str) -> Optional[str]:
    """refs/heads/master of the fixture's local bare remote (../remote.git)."""
    bare = os.path.join(os.path.dirname(ws), "remote.git")
    if not os.path.isdir(bare):
        return None
    return git_out(bare, "rev-parse", "refs/heads/master") or None


def fetch_drafts(admin: Optional[GerritRest], numbers: list[int]) -> Optional[list[dict]]:
    """The admin account's draft comments on the given changes (the agent acts as the netrc admin):
    [{change, path, line, message, in_reply_to}]; None without admin credentials."""
    if admin is None:
        return None
    out = []
    for n in numbers:
        try:
            data = admin.get(f"/changes/{n}/drafts") or {}
        except RestError as exc:
            log(f"drafts of change {n} not readable: {exc}")
            continue
        for path, items in (data.items() if isinstance(data, dict) else []):
            for d in items or []:
                if isinstance(d, dict):
                    out.append({"change": n, "path": path, "line": d.get("line"), "message": d.get("message"),
                                "in_reply_to": d.get("in_reply_to")})
    return out


def read_back(rest: GerritRest, project: str, tags: list[str], run_dir: str) -> dict:
    """Changes carrying the run's hashtags plus their published comments -> gerrit-after.json."""
    changes = query_changes_by_hashtags(rest, project, tags)
    comments = {}
    for c in changes:
        try:
            comments[str(c["_number"])] = change_comments(rest, int(c["_number"]))
        except RestError as exc:
            comments[str(c["_number"])] = {"error": str(exc)}
    after = {"query": tags, "fetchedAt": _utc_now_str(), "changes": changes, "comments": comments}
    _write_json(os.path.join(run_dir, "gerrit-after.json"), after)
    return after


class PipelineStop(Exception):
    """Ends a run early after its metrics were written (message = error)."""


def seed_push(ws: str, opts: argparse.Namespace, tags: list[str], run_dir: str, rest: GerritRest, project: str) -> dict:
    """Push the chain the fixture built to refs/for/master, tag the seeded commits refs/bench/seed/<i>
    and resolve them to Gerrit changes. -> {push, chain, changes (chain order), numbers {Change-Id: n}}"""
    push = push_workspace_for_review_ex(ws, opts.push_to, tags, os.path.join(run_dir, "push.log"))
    if not push["ok"]:
        raise PipelineStop(f"seed push to {opts.push_to} failed ({push['error']})")
    if push["updated"]:
        raise PipelineStop(f"seed push updated existing changes {push['updated']} (the fixture's Change-Ids are "
                           "not unique per run, so runs would share changes)")
    chain = chain_commits(ws, push["base"])
    tag_chain(ws, chain)
    changes = query_changes_by_hashtags(rest, project, tags)
    map_change_ids_by_sha(chain, changes)
    by_cid = {c.get("change_id"): c for c in changes}
    numbers = {c["change_id"]: int(by_cid[c["change_id"]]["_number"]) for c in chain if c.get("change_id") in by_cid}
    ordered = []
    for c in chain:
        info = by_cid.get(c.get("change_id"))
        if info is not None:
            ordered.append({**info, "subject": info.get("subject") or c["subject"]})
    if not ordered:
        raise PipelineStop(f"seed push created no change on {opts.push_to} (no changes found by hashtag)")
    return {"push": push, "chain": chain, "changes": ordered, "numbers": numbers}


def prepare_session_workspace(ws: str, base_url: str) -> None:
    """After the seed push the agent must see a plain Gerrit clone: origin shares its base with HEAD,
    the runner's `review` remote is gone, and the Gerrit host is configured for the plugin's tools."""
    _git(ws, "config", "gerrit-stack.host", base_url)
    sync_origin_with_review(ws)
    _git(ws, "remote", "remove", "review")


def uncommitted(ws: str) -> bool:
    return bool(git_out(ws, "status", "--porcelain").strip())


def _base_record(pipe: "Pipeline", run_dir: str, started: _dt.datetime, opts: argparse.Namespace) -> dict:
    rec = new_run_record(run_dir, started)
    rec.update({"kind": pipe.kind, "variant": pipe.variant, "model": session_model(pipe.case, opts),
                "isolation": None, "capability": None, "conventions": None, "guardrails": None})
    return rec


def _reviewer_id(rest: GerritRest) -> Optional[int]:
    try:
        return int((rest.get("/accounts/self") or {}).get("_account_id"))
    except (RestError, TypeError, ValueError):
        return None


def _chain_json(chain: list[dict], numbers: dict) -> list[dict]:
    return [{"sha": c["sha"], "changeId": c.get("change_id"), "number": numbers.get(c.get("change_id")),
             "subject": c["subject"], "lines": c["lines"]} for c in chain]


def run_implement(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """fixture → one session → graders, chain metrics, guardrails, conventions → optional push."""
    case = pipe.case
    label = f"{pipe.key}/{arm}/{n}"
    run_dir = os.path.join(out_dir, "runs", pipe.key, arm, str(n))
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec = _base_record(pipe, run_dir, started, opts)
    tmp, ws, env, error = make_workspace(case, opts, run_dir)
    try:
        rh0 = remote_master_sha(ws)
        rec.update(run_session(case, pipe.prompt(), arm, ws, env, run_dir, opts, judge_factory, label,
                               error=error, started=started))
        trace = parse_trace_file(rec["tracePath"])
        left = uncommitted(ws)
        rec["guardrails"] = stage_guardrails(trace, os.path.join(run_dir, "hook-trace.log"),
                                             _read_json(os.path.join(run_dir, "chain-metrics.json")),
                                             (rh0, remote_master_sha(ws)), (None, None), left_uncommitted=left)
        rec["conventions"] = compute_conventions(ws, chain_commits(ws), [])
        _write_json(os.path.join(run_dir, "conventions.json"), rec["conventions"])
        if getattr(opts, "push_to", None):
            tags = push_hashtags(case.name, arm, os.path.basename(os.path.normpath(out_dir)), pipe.variant, n)
            rec["runnerCommitted"] = commit_leftovers(ws, case.name, "feat") if left else False
            push = push_workspace_for_review_ex(ws, opts.push_to, tags, os.path.join(run_dir, "push.log"))
            rec["pushed"], rec["pushError"], rec["hashtags"] = push["numbers"], push["error"], tags
            log(f"{label}: pushed changes {push['numbers'] or 'none'} ({', '.join(tags)})")
    except Exception as exc:  # never abort the suite
        rec["error"] = _join_error(rec.get("error"), f"runner error: {exc!r}")
        rec["passed"] = False
    finally:
        rec["durationSeconds"] = round(time.time() - t0, 3)
        finish_workspace(tmp, ws, run_dir, rec, opts.keep)
    return rec


def run_rework(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """fixture (seeded chain) → seed push → rena's -1 + unresolved thread → ONE session → leftover
    commit on top → second push → read-back → review.json, rework-metrics.json, conventions.json."""
    case, variant = pipe.case, pipe.variant
    label = f"{pipe.key}/{arm}/{n}"
    run_dir = os.path.join(out_dir, "runs", pipe.key, arm, str(n))
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec = _base_record(pipe, run_dir, started, opts)
    tags = push_hashtags(case.name, arm, os.path.basename(os.path.normpath(out_dir)), variant, n)
    rec["hashtags"] = tags
    base_url, prefix, project = gerrit_from_push_url(opts.push_to)
    tmp, ws, env, error = make_workspace(case, opts, run_dir)
    review: Optional[dict] = None
    rework: Optional[dict] = None
    seeded_numbers: list[int] = []
    try:
        if error:
            raise PipelineStop(error)
        spec = case.rework_spec()
        rest = GerritRest(base_url, prefix, REVIEWER, read_token(opts.rena_token))
        try:
            seed = seed_push(ws, opts, tags, run_dir, rest, project)
        except PipelineStop as exc:
            rework = null_rework_metrics()
            rework["seed_push_error"] = str(exc)
            raise
        seeded, numbers = seed["chain"], seed["numbers"]
        seeded_numbers = [int(c["_number"]) for c in seed["changes"]]
        rec["pushed"] = seeded_numbers
        rec["seededChain"] = _chain_json(seeded, numbers)
        # ---- reviewer step (rena)
        target = select_rework_target(spec, seed["changes"], lambda num: change_files(rest, num),
                                      lambda num, p: change_file_content(rest, num, p))
        t_change = target["change"]
        t_num = int(t_change["_number"])
        reviewer_id = _reviewer_id(rest)
        review = {
            "kind": "rework", "variant": variant, "reviewer": REVIEWER, "reviewerAccountId": reviewer_id,
            "targetChange": t_num, "targetChangeId": t_change.get("change_id"), "targetSubject": t_change.get("subject"),
            "reason": target["reason"], "file": target["path"], "line": target["line"], "message": target["message"],
            "seededChanges": seeded_numbers,
            "seededShas": {c["change_id"]: c["sha"] for c in seeded if c.get("change_id")},
            "mayChange": [numbers[c] for c in may_change_ids(spec, seeded) if c in numbers],
            "url": f"{base_url}/c/{project}/+/{t_num}",
        }
        payload = build_review_payload(target["path"], target["line"], target["message"])
        review_time = _utc_now_str()
        response = rest.post(f"/changes/{t_num}/revisions/current/review", payload)
        review.update({"payload": payload, "response": response, "postedAt": review_time})
        _write_json(os.path.join(run_dir, "review.json"), review)
        log(f"{label}: rena -1 on change {t_num} {target['path']}:{target['line']} ({target['reason']})")
        # ---- the one session
        prepare_session_workspace(ws, base_url)
        env2 = dict(env)
        env2["GERRIT_HOST"] = base_url
        prompt = pipe.prompt(seeded_numbers, base_url, project, t_num)
        rh0, gm0 = remote_master_sha(ws), gerrit_branch_sha(rest, project)
        rec.update(run_session(case, prompt, arm, ws, env2, run_dir, opts, judge_factory, label, started=started))
        committed = commit_leftovers(ws, case.name, "fix")
        rec["runnerCommitted"] = committed
        cm_path = os.path.join(run_dir, "chain-metrics.json")
        if committed:
            log(f"{label}: uncommitted work left; committed on top by the runner")
            run_chain_metrics(opts.plugin_dir, ws, os.path.join(run_dir, "hook-trace.log"), cm_path, case)
        trace = parse_trace_file(rec["tracePath"])
        rec["guardrails"] = stage_guardrails(trace, os.path.join(run_dir, "hook-trace.log"), _read_json(cm_path),
                                             (rh0, remote_master_sha(ws)), (gm0, gerrit_branch_sha(rest, project)),
                                             left_uncommitted=committed)
        # ---- second push + read-back
        push2 = push_workspace_for_review_ex(ws, opts.push_to, tags, os.path.join(run_dir, "push.log"))
        rec["secondPush"] = {"numbers": push2["numbers"], "new": push2["new"], "updated": push2["updated"],
                             "error": push2["error"]}
        log(f"{label}: second push {push2['numbers'] or 'nothing'} ({push2['error'] or 'ok'})")
        final = chain_commits(ws, push2["base"] or seed["push"]["base"])
        after = read_back(rest, project, tags, run_dir)
        map_change_ids_by_sha(final, after["changes"])
        after_by_cid = {c.get("change_id"): int(c["_number"]) for c in after["changes"]}
        final_numbers = {c["change_id"]: after_by_cid[c["change_id"]] for c in final if c.get("change_id") in after_by_cid}
        rec["finalChain"] = _chain_json(final, final_numbers)
        drafts = fetch_drafts(admin_rest(base_url, prefix), sorted(set(seeded_numbers) | set(final_numbers.values())))
        rework = compute_rework_metrics(ws, seeded, final, t_change.get("change_id"), may_change_ids(spec, seeded),
                                        numbers, final_numbers, trace.last_message, after, reviewer_id, review_time,
                                        _read_json(cm_path), drafts, committed)
        rework["second_push_error"] = push2["error"]
        rec["conventions"] = compute_conventions(ws, final, drafted_comments(trace.last_message, drafts))
        _write_json(os.path.join(run_dir, "conventions.json"), rec["conventions"])
    except PipelineStop as exc:
        rec["error"] = _join_error(rec.get("error"), f"pipeline error: {exc}")
        rec["passed"] = False
    except Exception as exc:  # never abort the suite
        rec["error"] = _join_error(rec.get("error"), f"pipeline error: {exc!r}")
        rec["passed"] = False
    finally:
        if rework is None:
            rework = null_rework_metrics(seeded_numbers)
        _write_json(os.path.join(run_dir, "rework-metrics.json"), rework)
        rec["review"] = review
        rec["rework"] = rework
        rec["durationSeconds"] = round(time.time() - t0, 3)
        finish_workspace(tmp, ws, run_dir, rec, opts.keep)
    return rec


def run_review(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """fixture (seeded chain) → seed push → ONE reviewing session → read-back (published comments and
    votes by anyone but rena are violations, Gerrit drafts are fine) → review-metrics.json."""
    case, variant = pipe.case, pipe.variant
    label = f"{pipe.key}/{arm}/{n}"
    run_dir = os.path.join(out_dir, "runs", pipe.key, arm, str(n))
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec = _base_record(pipe, run_dir, started, opts)
    tags = push_hashtags(case.name, arm, os.path.basename(os.path.normpath(out_dir)), variant, n)
    rec["hashtags"] = tags
    base_url, prefix, project = gerrit_from_push_url(opts.push_to)
    tmp, ws, env, error = make_workspace(case, opts, run_dir)
    review: Optional[dict] = None
    metrics: Optional[dict] = None
    try:
        if error:
            raise PipelineStop(error)
        spec = case.review_spec()
        rest = GerritRest(base_url, prefix, REVIEWER, read_token(opts.rena_token))
        seed = seed_push(ws, opts, tags, run_dir, rest, project)
        seeded, numbers = seed["chain"], seed["numbers"]
        seeded_numbers = [int(c["_number"]) for c in seed["changes"]]
        rec["pushed"] = seeded_numbers
        rec["seededChain"] = _chain_json(seeded, numbers)
        t_change = find_change_by_subject(spec.get("target_subject"), seed["changes"], "review.target_subject")
        t_num = int(t_change["_number"])
        try:
            target_files = sorted(change_files(rest, t_num))
        except RestError:
            target_files = []
        reviewer_id = _reviewer_id(rest)
        review = {
            "kind": "review", "variant": variant, "targetChange": t_num, "targetChangeId": t_change.get("change_id"),
            "targetSubject": t_change.get("subject"), "targetFiles": target_files, "seededChanges": seeded_numbers,
            "planted": [str(p.get("id")) for p in spec.get("planted") or [] if isinstance(p, dict)],
            "url": f"{base_url}/c/{project}/+/{t_num}",
        }
        prepare_session_workspace(ws, base_url)
        env2 = dict(env)
        env2["GERRIT_HOST"] = base_url
        prompt = pipe.prompt(seeded_numbers, base_url, project, t_num)
        rh0, gm0 = remote_master_sha(ws), gerrit_branch_sha(rest, project)
        session_start = _utc_now_str()
        review["sessionStartedAt"] = session_start
        _write_json(os.path.join(run_dir, "review.json"), review)
        rec.update(run_session(case, prompt, arm, ws, env2, run_dir, opts, judge_factory, label, started=started))
        trace = parse_trace_file(rec["tracePath"])
        rec["guardrails"] = stage_guardrails(trace, os.path.join(run_dir, "hook-trace.log"),
                                             _read_json(os.path.join(run_dir, "chain-metrics.json")),
                                             (rh0, remote_master_sha(ws)), (gm0, gerrit_branch_sha(rest, project)),
                                             left_uncommitted=uncommitted(ws))
        after = read_back(rest, project, tags, run_dir)
        drafts = fetch_drafts(admin_rest(base_url, prefix), seeded_numbers)
        metrics = compute_review_metrics(spec, target_files, trace.last_message, drafts, after, reviewer_id,
                                         session_start, set(seeded_numbers))
        _write_json(os.path.join(run_dir, "review-metrics.json"), metrics)
        rec["conventions"] = compute_conventions(ws, chain_commits(ws, seed["push"]["base"]),
                                                 drafted_comments(trace.last_message, drafts))
        _write_json(os.path.join(run_dir, "conventions.json"), rec["conventions"])
    except PipelineStop as exc:
        rec["error"] = _join_error(rec.get("error"), f"pipeline error: {exc}")
        rec["passed"] = False
    except Exception as exc:  # never abort the suite
        rec["error"] = _join_error(rec.get("error"), f"pipeline error: {exc!r}")
        rec["passed"] = False
    finally:
        rec["review"] = review
        rec["reviewMetrics"] = metrics
        rec["durationSeconds"] = round(time.time() - t0, 3)
        finish_workspace(tmp, ws, run_dir, rec, opts.keep)
    return rec


def run_pipeline(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """Dispatch on the case kind."""
    fn = {"implement": run_implement, "rework": run_rework, "review": run_review}[pipe.kind]
    return fn(pipe, arm, n, opts, out_dir, judge_factory)


# --------------------------------------------------------------------------
# Aggregation, report
# --------------------------------------------------------------------------

def _mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def run_cost(r: dict) -> float:
    """What a run adds to the cost ceiling: its session plus its judge calls."""
    return float(r.get("costUsd", 0.0)) + float(r.get("judgeCostUsd", 0.0))


def isolation_summary(runs: list[dict]) -> str:
    """`<ok>/<checked>` over the runs whose session started ('' when none did)."""
    checked = [r["isolation"] for r in runs if isinstance(r.get("isolation"), dict)]
    return f"{sum(1 for i in checked if i.get('ok'))}/{len(checked)}" if checked else ""


def _yes(runs: list[dict], block: str, key: str) -> str:
    vals = [r[block].get(key) for r in runs if isinstance(r.get(block), dict) and r[block].get(key) is not None]
    return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "-"


def _total(runs: list[dict], block: str, key: str) -> Any:
    vals = [r[block].get(key) for r in runs if isinstance(r.get(block), dict) and r[block].get(key) is not None]
    return sum(vals) if vals else "-"


def key_numbers(runs: list[dict]) -> str:
    """The few numbers that matter per kind, summed over an arm's runs (for the summary table)."""
    if any(isinstance(r.get("rework"), dict) for r in runs):
        return (f"ids {_yes(runs, 'rework', 'change_id_set_preserved')} order {_yes(runs, 'rework', 'order_preserved')} "
                f"fix {_yes(runs, 'rework', 'fix_on_target')} untouched {_total(runs, 'rework', 'untouched_identical')}"
                f"/{_total(runs, 'rework', 'untouched_total')} new {_total(runs, 'rework', 'new_changes_opened')} "
                f"markers {_total(runs, 'rework', 'conflict_markers_left')}")
    if any(isinstance(r.get("reviewMetrics"), dict) for r in runs):
        return (f"found {_total(runs, 'reviewMetrics', 'planted_found')}/{_total(runs, 'reviewMetrics', 'planted_total')} "
                f"labelled {_total(runs, 'reviewMetrics', 'comments_labelled')}/{_total(runs, 'reviewMetrics', 'comments_total')} "
                f"published {_total(runs, 'reviewMetrics', 'published_comments')} votes {_total(runs, 'reviewMetrics', 'votes_posted')}")
    return ""


def aggregate_case(case: Any, arms: dict[str, list[dict]], threshold: float, pipeline: Optional[Pipeline] = None) -> dict:
    by_arm = {}
    for arm, runs in arms.items():
        scores = [r["score"] for r in runs]
        by_arm[arm] = {
            "score": _mean(scores) if runs else None,
            "passRate": round(sum(1 for r in runs if r["passed"]) / len(runs), 4) if runs else None,
            "meanTurns": _mean([r["turns"] for r in runs if r["turns"] is not None]),
            "costUsd": round(sum(run_cost(r) for r in runs), 6),
            "isolationOk": isolation_summary(runs),
            "errors": sum(1 for r in runs if r.get("error")),
        }
    primary = "with" if "with" in arms else (next(iter(arms)) if arms else None)
    score = by_arm[primary]["score"] if primary else None
    pass_rate = by_arm[primary]["passRate"] if primary else None
    deltas = {}
    if "with" in by_arm and by_arm["with"]["score"] is not None:
        for other in ("without", "mcp-only"):
            if other in by_arm and by_arm[other]["score"] is not None:
                deltas[f"with-{other}"] = round(by_arm["with"]["score"] - by_arm[other]["score"], 4)
    delta = next(iter(deltas.values()), None)
    return {
        "name": pipeline.key if pipeline is not None else case.name,
        "dir": case.dir,
        "baseCase": case.name,
        "kind": getattr(case, "kind", "implement"),
        "variant": pipeline.variant if pipeline is not None else "natural",
        "runsPerCase": max((len(r) for r in arms.values()), default=0),
        "maxTurns": case.max_turns,
        "timeoutSeconds": case.timeout_seconds,
        "aggregates": {"score": score, "passRate": pass_rate, "delta": delta, "byArm": by_arm, "deltas": deltas,
                       "passed": (score is not None and score >= threshold)},
        "arms": arms,
    }


def aggregate(cases: list[dict], threshold: float, meta: dict) -> dict:
    scores = [c["aggregates"]["score"] for c in cases if c["aggregates"]["score"] is not None]
    deltas = [c["aggregates"]["delta"] for c in cases if c["aggregates"]["delta"] is not None]
    passed = sum(1 for c in cases if c["aggregates"]["passed"])
    total = len(cases)
    out = {
        "schemaVersion": 1,
        "startedAt": meta.get("startedAt"),
        "claudeVersion": meta.get("claudeVersion"),
        "suite": meta.get("suite"),
        "evalDir": meta.get("evalDir"),
        "model": meta.get("model"),
        "variants": meta.get("variants"),
        "costUsd": round(meta.get("costUsd", 0.0), 6),
        "durationSeconds": round(meta.get("durationSeconds", 0.0), 3),
        "partial": bool(meta.get("partial")),
        "partialReason": meta.get("partialReason"),
        "threshold": threshold,
        "arms": meta.get("arms", []),
        "aggregates": {
            "casesTotal": total,
            "casesPassed": passed,
            "overallScore": _mean(scores) if scores else 0.0,
            "overallPassRate": round(passed / total, 4) if total else 0.0,
            "meanDelta": _mean(deltas),
        },
        "cases": cases,
    }
    return out


def _bad_summary(guardrails: Optional[dict]) -> str:
    if not isinstance(guardrails, dict):
        return ""
    bad = guardrails.get("bad_outcomes") or {}
    hits = [k for k, v in bad.items() if v]
    return ", ".join(hits) if hits else "none"


def _cell(v: Any) -> str:
    return "" if v is None else str(v)


def _each_run(agg: dict):
    for c in agg["cases"]:
        for arm, runs in c["arms"].items():
            for i, r in enumerate(runs, 1):
                yield c, arm, i, r


def render_report(agg: dict) -> str:
    a = agg["aggregates"]
    lines = [
        "# gerrit-stack eval report",
        "",
        f"- started: {agg.get('startedAt')}",
        f"- claude: {agg.get('claudeVersion')}",
        f"- suite: {agg.get('suite')} · model: {agg.get('model')} · variants: {', '.join(agg.get('variants') or ['natural'])}",
        f"- arms: {', '.join(agg.get('arms') or [])}",
        f"- cost: ${agg.get('costUsd', 0):.4f} · duration: {agg.get('durationSeconds', 0):.0f}s"
        + (f" · **partial**: {agg.get('partialReason')}" if agg.get("partial") else ""),
        f"- overall score: **{a['overallScore']}** (threshold {agg.get('threshold')}), "
        f"cases passed: {a['casesPassed']}/{a['casesTotal']}, mean delta: {a['meanDelta']}",
        "",
        "| case | kind | variant | arm | runs | score | pass rate | mean turns | cost USD | delta (with − arm) | isolation ok | key numbers |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in agg["cases"]:
        for arm, st in c["aggregates"]["byArm"].items():
            d = c["aggregates"]["deltas"].get(f"with-{arm}")
            lines.append(f"| {c['name']} | {c.get('kind', 'implement')} | {c.get('variant', 'natural')} | {arm} | "
                         f"{len(c['arms'][arm])} | {st['score']} | {st['passRate']} | {st['meanTurns']} | "
                         f"{st['costUsd']:.4f} | {_cell(d)} | {st.get('isolationOk', '')} | {key_numbers(c['arms'][arm])} |")
    failures = []
    for c, arm, i, r in _each_run(agg):
        if r.get("error"):
            failures.append(f"- {c['name']}/{arm}/{i}: error: {r['error']}")
        for g in r.get("graders", []):
            if not g.get("skipped") and not g.get("passed"):
                failures.append(f"- {c['name']}/{arm}/{i}: grader `{g['name']}` ({g['type']}) FAIL — {g['detail']}")
    lines += ["", "## Failed graders and errors", ""]
    lines += failures or ["(none)"]
    lines.append("")
    rework = [(c, arm, i, r) for c, arm, i, r in _each_run(agg) if isinstance(r.get("rework"), dict)]
    if rework:
        lines += ["## Rework (seeded chain)", "",
                  "| case | arm | n | target | ids preserved | order preserved | fix on target | untouched identical | "
                  "may-change changed | new changes | fixups left | builds alone % | conflict markers | interdiff | "
                  "reply drafted / labelled | posted (reply/vote) | runner committed | bad outcomes |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c, arm, i, r in rework:
            rw = r["rework"]
            lines.append(
                f"| {c['name']} | {arm} | {i} | {_cell(rw.get('target_change'))} | {_cell(rw.get('change_id_set_preserved'))} | "
                f"{_cell(rw.get('order_preserved'))} | {_cell(rw.get('fix_on_target'))} | "
                f"{_cell(rw.get('untouched_identical'))}/{_cell(rw.get('untouched_total'))} | {_cell(rw.get('may_change_changed'))} | "
                f"{_cell(rw.get('new_changes_opened'))} | {_cell(rw.get('fixups_left'))} | {_cell(rw.get('builds_alone_pct'))} | "
                f"{_cell(rw.get('conflict_markers_left'))} | {_cell(rw.get('interdiff_lines'))} | "
                f"{_cell(rw.get('reply_drafted'))} / {_cell(rw.get('reply_labelled'))} | "
                f"{_cell(rw.get('reply_posted'))}/{_cell(rw.get('vote_posted'))} | {_cell(rw.get('runner_committed'))} | "
                f"{_bad_summary(r.get('guardrails'))} |")
        lines.append("")
    reviews = [(c, arm, i, r) for c, arm, i, r in _each_run(agg) if isinstance(r.get("reviewMetrics"), dict)]
    if reviews:
        lines += ["## Reviewer", "",
                  "| case | arm | n | planted found | found ids | comments labelled | blocking marked | drafts | "
                  "published comments | votes |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for c, arm, i, r in reviews:
            rv = r["reviewMetrics"]
            lines.append(
                f"| {c['name']} | {arm} | {i} | {_cell(rv.get('planted_found'))}/{_cell(rv.get('planted_total'))} | "
                f"{', '.join(rv.get('found_ids') or [])} | {_cell(rv.get('comments_labelled'))}/{_cell(rv.get('comments_total'))} | "
                f"{_cell(rv.get('blocking_marked_correct'))} | {_cell(rv.get('drafts_created'))} | "
                f"{_cell(rv.get('published_comments'))} | {_cell(rv.get('votes_posted'))} |")
        lines.append("")
    checked = [(c, arm, i, r) for c, arm, i, r in _each_run(agg) if isinstance(r.get("isolation"), dict)]
    if checked:
        lines += ["## Isolation and capability", "",
                  "| case | arm | n | isolation ok | unexpected | missing | plugins | MCP servers | model | "
                  "mcp calls (denied) | skill calls | hook lines | commits conforming | commitlint |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c, arm, i, r in checked:
            iso, cap, conv = r["isolation"], r.get("capability") or {}, r.get("conventions") or {}
            fp = iso.get("fingerprint") or {}
            unexpected = "; ".join(f"{k}: {', '.join(v)}" for k, v in (iso.get("unexpected") or {}).items() if v) or "none"
            missing = "; ".join(f"{k}: {', '.join(v)}" for k, v in (iso.get("missing") or {}).items()
                                if isinstance(v, list) and v) or "none"
            lines.append(
                f"| {c['name']} | {arm} | {i} | {iso.get('ok')} | {unexpected} | {missing} | "
                f"{', '.join(fp.get('plugins') or []) or 'none'} | {', '.join(fp.get('mcp_servers') or []) or 'none'} | "
                f"{_cell(fp.get('model'))} | {_cell(cap.get('mcp_calls'))} ({_cell(cap.get('mcp_denied'))}) | "
                f"{_cell(cap.get('skill_calls'))} | {_cell(cap.get('hook_lines'))} | "
                f"{_cell(conv.get('commit_subjects_conforming'))}/{_cell(conv.get('commit_subjects_total'))} | "
                f"{_cell(conv.get('commitlint_available'))} |")
        lines.append("")
    return "\n".join(lines)


def print_summary(agg: dict) -> None:
    a = agg["aggregates"]
    rows = [("case", "kind", "variant", "arm", "runs", "score", "pass", "turns", "cost", "iso ok", "key numbers")]
    for c in agg["cases"]:
        for arm, st in c["aggregates"]["byArm"].items():
            rows.append((c["name"], str(c.get("kind", "implement")), str(c.get("variant", "natural")), arm,
                         str(len(c["arms"][arm])), str(st["score"]), str(st["passRate"]), str(st["meanTurns"]),
                         f"{st['costUsd']:.3f}", str(st.get("isolationOk", "")), key_numbers(c["arms"][arm])))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())
    print(f"\noverall score {a['overallScore']} (threshold {agg.get('threshold')}), "
          f"cases passed {a['casesPassed']}/{a['casesTotal']}, mean delta {a['meanDelta']}, "
          f"cost ${agg.get('costUsd', 0):.4f}" + (f", PARTIAL: {agg.get('partialReason')}" if agg.get("partial") else ""))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def claude_version() -> Optional[str]:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30,
                              env=sandbox_env(), stdin=subprocess.DEVNULL).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_args(argv=None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Run gerrit-stack eval/benchmark cases with claude -p (stdlib only).")
    ap.add_argument("--eval-dir", default=here, help="directory holding <case>/prompt.md (default: evals/)")
    ap.add_argument("--bench", action="store_true", help="use <eval-dir>/bench instead")
    ap.add_argument("--case", action="append", default=[], metavar="GLOB", help="case name glob (repeatable)")
    ap.add_argument("--runs", type=int, default=None, help="runs per case × arm (default: prompt.md `runs` or 1)")
    ap.add_argument("--arms", default="with", help="comma list of with,without,mcp-only (default: with)")
    ap.add_argument("--ablation", action="store_true", help="shorthand for --arms with,without")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--max-cost-usd", type=float, default=None, help="stop (partial, exit 2) once spent")
    ap.add_argument("--model", default=None,
                    help=f"session model, always passed to claude and recorded (default: the case's `model:`, else {DEFAULT_MODEL})")
    ap.add_argument("--judge-model", default="haiku")
    ap.add_argument("--judge-votes", type=int, default=1)
    ap.add_argument("--plugin-dir", default=os.path.dirname(here), help="plugin root for the `with` arm")
    ap.add_argument("--mcp-plugin-dir", default=None, help="installed gerrit-mcp plugin dir for the with/mcp-only arms (default: newest ~/.claude/plugins/cache/gerrit-mcp/gerrit/*)")
    ap.add_argument("--keep", action="store_true", help="keep run workspaces under the run dir")
    ap.add_argument("--push-to", default=None, metavar="GERRIT_PROJECT_URL",
                    help="Gerrit project the runs push to (refs/for/master, hashtags bench-<case>-<arm>, run-<id>, "
                         "var-<variant>, rep-<n>), e.g. http://localhost:8080/a/demo-plugin; required for the case "
                         "kinds rework and review, optional for implement")
    ap.add_argument("--variants", default="natural", metavar="LIST",
                    help="comma list of natural,nudged (default: natural); nudged appends the case's nudge line "
                         "(implement: nudges.stage1, rework: rework.nudge) and is skipped for cases without one")
    ap.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                    help="run N cases of one arm in parallel (arms stay sequential; default 1)")
    ap.add_argument("--rena-token", default=None, metavar="FILE",
                    help="reviewer token file (default: <plugin-dir>/demo/work/.rena-token); never printed")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the commands, run nothing")
    ap.add_argument("--json", default=None, metavar="PATH", help="also write aggregate-result.json here")
    ap.add_argument("--out-dir", default=None, help="default evals/results/<timestamp>")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    args.mcp_dir = args.mcp_plugin_dir or find_mcp_plugin_dir()
    if args.mcp_dir is None:
        warn('gerrit-mcp plugin dir not found; the with/mcp-only arms run without the official MCP')
    args.plugin_dir = os.path.abspath(args.plugin_dir)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if args.ablation:
        arms = ["with", "without"]
    for a in arms:
        if a not in ARMS:
            ap.error(f"unknown arm {a!r} (choose from {', '.join(ARMS)})")
    args.arm_list = arms
    variants = [v.strip() for v in args.variants.split(",") if v.strip()] or ["natural"]
    for v in variants:
        if v not in VARIANTS:
            ap.error(f"unknown variant {v!r} (choose from {', '.join(VARIANTS)})")
    if args.push_to:
        try:
            gerrit_from_push_url(args.push_to)
        except ValueError as exc:
            ap.error(str(exc))
    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    args.variant_list = variants
    args.rena_token = args.rena_token or os.path.join(args.plugin_dir, "demo", "work", ".rena-token")
    if args.bench:
        args.eval_dir = os.path.join(args.eval_dir, "bench")
    return args


def build_pipelines(cases: list[Case], opts: argparse.Namespace) -> list[Pipeline]:
    """case × variant. A case without a nudge line has no nudged variant (logged once per case);
    rework/review cases need --push-to and their case.yaml block (ValueError names what is missing)."""
    out = []
    for c in cases:
        if c.kind in ("rework", "review"):
            if not getattr(opts, "push_to", None):
                raise ValueError(f"case {c.name} (kind: {c.kind}) needs --push-to <gerrit project url>")
            c.rework_spec() if c.kind == "rework" else c.review_spec()
        for v in opts.variant_list:
            if v == "nudged" and not c.nudge():
                say(f"# {c.name}: no nudge line in case.yaml (kind {c.kind}); nudged variant skipped")
                continue
            out.append(Pipeline(c, v))
    return out


def dry_run_pipeline(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, run_id: str) -> None:
    """Print the full plan of one run: fixture, seed push, reviewer post, session command with the
    scrubbed env, second push, read-back."""
    case, kind, variant = pipe.case, pipe.kind, pipe.variant
    print(f"# {pipe.key} / {arm} / run {n}  (kind: {kind}; variant: {variant}; model: {session_model(case, opts)}; "
          f"graders: {', '.join(g.name for g in case.graders) or 'none'})")
    own = {**case.env, "EVAL_PLUGIN_ROOT": opts.plugin_dir, "GERRIT_STACK_TRACE": "<run-dir>/hook-trace.log"}
    if case.scaffold_script:
        print(f"# fixture (cwd: temp workspace; env: parent minus CLAUDE*): bash {shlex.quote(case.scaffold_script)}")
    tags = push_hashtags(case.name, arm, run_id, variant, n)
    refspec = "HEAD:refs/for/master%" + ",".join("t=" + t for t in tags)
    base_url = project = ""
    if kind != "implement":
        base_url, _prefix, project = gerrit_from_push_url(opts.push_to)
        print(f"# seed push: rebase the fixture's chain onto {opts.push_to} master; git push review {refspec}; "
              f"seeded commits tagged refs/bench/seed/<i>")
    if kind == "rework":
        spec = case.rework_spec()
        print(f"# reviewer ({REVIEWER}): POST /changes/<target>/revisions/current/review Code-Review -1 + one unresolved "
              f"comment; target_subject /{spec.get('target_subject')}/ file /{spec.get('file') or ''}/ "
              f"line /{spec.get('line') or ''}/: {str(spec.get('message') or DEFAULT_REWORK_MESSAGE)!r}")
        print(f"# may_change: {spec.get('may_change') or []}")
    if kind == "review":
        spec = case.review_spec()
        planted = [f"{p.get('id')} ({p.get('kind')})" for p in spec.get("planted") or [] if isinstance(p, dict)]
        print(f"# review target: target_subject /{spec.get('target_subject')}/; planted: {', '.join(planted) or 'none'}")
    if kind != "implement":
        own["GERRIT_HOST"] = base_url
        print(f"# session prep: origin synced with review/master; review remote removed; "
              f"git config gerrit-stack.host {base_url}")
    env = sandbox_env(own)
    print(f"# session env (scrubbed; stdin=/dev/null): {env_summary(env)}")
    prompt = pipe.prompt(None, base_url, project, None)
    print(shlex.join(build_claude_cmd(prompt, case, arm, opts.plugin_dir, session_model(case, opts),
                                      mcp_plugin_dir=opts.mcp_dir)))
    print(f"# after the session: isolation.json (expected plugins: {', '.join(ARM_PLUGINS[arm]) or 'none'} + cc-plugin-*; "
          f"MCP servers: {', '.join(ARM_MCP_SERVERS[arm]) or 'none'}), capability counters, chain-metrics "
          f"(--verify-cmd{' --concerns' if case.has_concerns else ''}), conventions.json")
    if kind == "implement":
        if opts.push_to:
            print(f"# push: leftovers committed by the runner; git push review {refspec}")
    elif kind == "rework":
        print(f"# second push: leftovers committed on top by the runner; git push review {refspec}")
        print(f"# read back: /changes/?q={'+'.join('hashtag:' + t for t in tags)}, comments, admin drafts "
              f"-> gerrit-after.json, review.json, rework-metrics.json")
    else:
        print(f"# read back (no second push): /changes/?q={'+'.join('hashtag:' + t for t in tags)}, published "
              f"comments/votes by non-{REVIEWER} accounts, admin drafts (/changes/<n>/drafts) -> review-metrics.json")


LIMIT_RETRIES = 2  # re-runs of one run after an account session-limit (HTTP 429) error
_RESET_RE = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)", re.I)


def session_limit_wait(error: Optional[str], now: Optional[_dt.datetime] = None) -> Optional[int]:
    """Seconds to sleep before retrying a run that failed on the account's session limit
    ("api error 429: You've hit your session limit · resets 8:30pm (...)"): until the stated local
    reset time plus 5 minutes (next day if already past). None when the error is something else."""
    if not error or "429" not in error or "session limit" not in error.lower():
        return None
    now = now or _dt.datetime.now()
    m = _RESET_RE.search(error)
    if not m:
        return 30 * 60
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    reset = now.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if reset <= now:
        reset += _dt.timedelta(days=1)
    return int((reset - now).total_seconds()) + 5 * 60


def run_arm(arm: str, pipelines: list[Pipeline], opts: argparse.Namespace, out_dir: str, judge_factory,
            per_case: dict, budget: dict) -> bool:
    """Run every (pipeline, n) of one arm, serially or on a thread pool (-j). Returns True when the
    cost ceiling stopped the arm. `budget` = {"spent", "lock", "partialReason"} shared across arms."""
    jobs = [(pipe, n, opts.runs or pipe.case.runs) for pipe in pipelines for n in range(1, (opts.runs or pipe.case.runs) + 1)]

    def ceiling_hit(pipe: Pipeline, n: int) -> bool:
        with budget["lock"]:
            if opts.max_cost_usd is not None and budget["spent"] >= opts.max_cost_usd:
                if budget["partialReason"] is None:
                    budget["partialReason"] = f"cost ceiling ${opts.max_cost_usd} reached before {pipe.key}/{arm}/{n}"
                    warn(budget["partialReason"])
                return True
        return False

    def execute(pipe: Pipeline, n: int) -> dict:
        for attempt in range(1, LIMIT_RETRIES + 2):
            rec = run_pipeline(pipe, arm, n, opts, out_dir, judge_factory)
            with budget["lock"]:
                budget["spent"] += run_cost(rec)
            wait = session_limit_wait(rec.get("error"))
            if wait is None or attempt > LIMIT_RETRIES:
                return rec
            log(f"{pipe.key}/{arm}/{n}: account session limit; waiting {wait // 60} min for the reset, "
                f"then re-running from scratch (attempt {attempt + 1})")
            time.sleep(wait)
        return rec

    def status_line(rec: dict) -> str:
        line = (f"{'PASS' if rec['passed'] else 'FAIL'} score={rec['score']} turns={rec['turns']} cost=${rec['costUsd']:.4f}"
                + (f" judge=${rec['judgeCostUsd']:.4f}" if rec.get("judgeCostUsd") else ""))
        iso = rec.get("isolation")
        if isinstance(iso, dict):
            line += f" isolation={'ok' if iso.get('ok') else 'FAILED'}"
        keys = key_numbers([rec])
        if keys:
            line += " | " + keys
        return line + (f" error={rec['error']}" if rec.get("error") else "")

    if opts.jobs <= 1:
        for pipe, n, runs in jobs:
            if ceiling_hit(pipe, n):
                return True
            print(f"→ {pipe.key} [{arm}] run {n}/{runs} …", flush=True)
            rec = execute(pipe, n)
            per_case[pipe.key][arm].append(rec)
            print("   " + status_line(rec), flush=True)
        return False

    def worker(job: tuple) -> tuple:
        pipe, n, runs = job
        if ceiling_hit(pipe, n):
            return pipe.key, n, None
        say(f"→ {pipe.key}/{arm}/{n}: start (run {n}/{runs})")
        rec = execute(pipe, n)
        say(f"   {pipe.key}/{arm}/{n}: {status_line(rec)}")
        return pipe.key, n, rec

    results: dict[str, list[tuple[int, dict]]] = {pipe.key: [] for pipe in pipelines}
    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.jobs) as pool:
        for key, n, rec in pool.map(worker, jobs):
            if rec is not None:
                results[key].append((n, rec))
    for key, items in results.items():
        per_case[key][arm].extend(rec for _n, rec in sorted(items, key=lambda it: it[0]))
    return budget["partialReason"] is not None


def require_change_id_off(opts: argparse.Namespace, env: Optional[dict] = None) -> Callable[[], None]:
    """Any batch with --push-to. Set the project's receive.requireChangeId to FALSE for the batch (admin from ~/.netrc, this call
    only) and return the function that restores INHERIT. Without admin credentials or on a REST
    error: warn, change nothing, return a no-op."""
    base_url, prefix, project = gerrit_from_push_url(opts.push_to)
    admin = admin_rest(base_url, prefix, env)
    if admin is None:
        warn(f"no ~/.netrc entry for {base_url}: receive.requireChangeId left as is (commits without a Change-Id will be rejected)")
        return lambda: None
    try:
        set_require_change_id(admin, project, "FALSE")
    except RestError as exc:
        warn(f"could not set receive.requireChangeId=FALSE on {project}: {exc}")
        return lambda: None
    say(f"receive.requireChangeId: {project} -> FALSE for this batch (admin from ~/.netrc)")

    def restore() -> None:
        try:
            set_require_change_id(admin, project, "INHERIT")
            say(f"receive.requireChangeId: {project} -> INHERIT (restored)")
        except RestError as exc:
            warn(f"could not restore receive.requireChangeId on {project}: {exc} — run: "
                 f"curl -n -X PUT -H 'Content-Type: application/json' -d '{{\"require_change_id\":\"INHERIT\"}}' "
                 f"{base_url}{prefix}/projects/{_q(project)}/config")
    return restore


def _raise_on_sigterm(signum, frame):  # so `finally` blocks run (restore requireChangeId) when the batch is killed
    raise SystemExit(128 + signum)


def main(argv=None) -> int:
    global VERBOSE
    opts = parse_args(argv)
    VERBOSE = opts.verbose
    try:
        signal.signal(signal.SIGTERM, _raise_on_sigterm)
        signal.signal(signal.SIGHUP, _raise_on_sigterm)
    except (ValueError, OSError):  # not the main thread (tests)
        pass
    try:
        cases = discover_cases(opts.eval_dir, opts.case)
    except (ValueError, OSError) as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print(f"no cases found in {opts.eval_dir}" + (f" matching {opts.case}" if opts.case else ""), file=sys.stderr)
        return 1
    started = _dt.datetime.now(_dt.timezone.utc)
    out_dir = opts.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                                           started.strftime("%Y%m%d-%H%M%S"))
    out_dir = os.path.abspath(out_dir)  # hooks run inside the workspace: GERRIT_STACK_TRACE must be absolute
    try:
        pipelines = build_pipelines(cases, opts)
    except ValueError as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2
    if not pipelines:
        print(f"run.py: no case has the requested variant(s) {', '.join(opts.variant_list)}", file=sys.stderr)
        return 1
    if any(p.kind in ("rework", "review") for p in pipelines):
        try:
            read_token(opts.rena_token)
        except RestError as exc:
            if opts.dry_run:
                warn(str(exc))
            else:
                print(f"run.py: {exc}", file=sys.stderr)
                return 2
    run_id = os.path.basename(os.path.normpath(out_dir))
    if opts.dry_run:
        if opts.push_to:
            base_url, prefix, project = gerrit_from_push_url(opts.push_to)
            print(f"# receive.requireChangeId: PUT {base_url}{prefix}/projects/{_q(project)}/config "
                  f"{{\"require_change_id\": \"FALSE\"}} as the ~/.netrc admin before the first push; restored to INHERIT at the end")
        for arm in opts.arm_list:
            print(f"# arm {arm}: user settings excluded (--setting-sources project,local); plugins: "
                  f"{', '.join(ARM_PLUGINS[arm]) or 'none'}")
            for pipe in pipelines:
                runs = opts.runs or pipe.case.runs
                for n in range(1, runs + 1):
                    dry_run_pipeline(pipe, arm, n, opts, run_id)
        if opts.push_to:
            print("# receive.requireChangeId: restore INHERIT")
        print(f"# out-dir would be {out_dir}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    eval_dir = os.path.abspath(opts.eval_dir)
    meta: dict[str, Any] = {"startedAt": started.isoformat(), "claudeVersion": claude_version(),
                            "arms": opts.arm_list, "partial": False, "partialReason": None, "costUsd": 0.0,
                            "suite": os.path.basename(eval_dir.rstrip("/")), "evalDir": eval_dir,
                            "model": opts.model or DEFAULT_MODEL, "variants": opts.variant_list}
    per_case: dict[str, dict[str, list[dict]]] = {p.key: {a: [] for a in opts.arm_list} for p in pipelines}
    budget: dict[str, Any] = {"spent": 0.0, "lock": threading.Lock(), "partialReason": None}
    t0 = time.time()

    def judge_factory(run_dir: str):
        return make_judge(opts.judge_model, opts.judge_votes, run_dir, run_dir)

    restore_change_id = require_change_id_off(opts) if opts.push_to else (lambda: None)
    try:
        for arm in opts.arm_list:
            if run_arm(arm, pipelines, opts, out_dir, judge_factory, per_case, budget):
                break
    finally:
        restore_change_id()
    if budget["partialReason"]:
        meta["partial"] = True
        meta["partialReason"] = budget["partialReason"]

    meta["costUsd"] = budget["spent"]
    meta["durationSeconds"] = time.time() - t0
    case_aggs = [aggregate_case(p.case, per_case[p.key], opts.threshold, p) for p in pipelines]
    agg = aggregate(case_aggs, opts.threshold, meta)
    with open(os.path.join(out_dir, "aggregate-result.json"), "w", encoding="utf-8") as fh:
        json.dump(agg, fh, indent=2)
    if opts.json:
        os.makedirs(os.path.dirname(os.path.abspath(opts.json)) or ".", exist_ok=True)
        with open(opts.json, "w", encoding="utf-8") as fh:
            json.dump(agg, fh, indent=2)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_report(agg))
    print()
    print_summary(agg)
    print(f"results: {out_dir}")
    if agg["partial"]:
        return 2
    return 0 if (agg["aggregates"]["overallScore"] or 0.0) >= opts.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
