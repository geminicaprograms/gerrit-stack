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

Outputs <out-dir>/aggregate-result.json (official schemaVersion 1),
<out-dir>/runs/<case>/<arm>/<n>/{trace.jsonl,hook-trace.log,chain-metrics.json,
last-message.md}, <out-dir>/report.md and a summary table on stdout.
Exit 0 = overall score >= threshold, 1 = below, 2 = partial (cost ceiling).

Rework + guardrail benchmark (--scenarios fix,split, needs --push-to): every
case × arm × scenario × variant becomes a pipeline `<case>@<scenario>-<variant>`:
stage 1 (today's run) → push to the demo Gerrit → reviewer step as `rena`
(Code-Review -1 + one unresolved comment, via REST) → stage 2 session in the
same workspace → second push → Gerrit read-back → rework-metrics.json and
per-stage guardrails. The runner never submits and never votes except that -1.

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
SCENARIOS = ("fix", "split")
VARIANTS = ("natural", "nudged")
REVIEWER = "rena"
REVIEW_MESSAGE = "Reviewed as rena"
CONVENTIONAL_RE = re.compile(r"(?:^|[\s|>*_`(\"'])(praise|nitpick|suggestion|issue|todo|question|thought|chore|note)( \([^)]*\))?:", re.M)
MCP_TOOL_RULE = "mcp__plugin_gerrit_gerrit"  # permission rule: all tools of the gerrit-mcp plugin's server
DEFAULT_STAGE2_PROMPT = (
    "Reviewer feedback arrived on the change(s) you pushed for review to {url} (project {project}, "
    "change(s) {changes}). Read the review comments, address the feedback and prepare the updated "
    "change(s) locally. Draft the reply to the reviewer in your final message, but do not post any "
    "comment, vote or push before I approve. This session is unattended: treat your own plan as approved."
)
DEFAULT_SPLIT_MESSAGE = ("issue (blocking): this change touches {n} production files ({files}) and mixes several "
                         "concerns ({concerns}); split it so each concern can be reviewed and reverted alone.")
SPLIT_NOT_APPLICABLE = "already split (max 1 production file per change)"
RUNNER_COMMIT_NOTE = "(left uncommitted by the agent; committed by the benchmark runner)"
DEFAULT_FIX_MESSAGE = "issue (blocking): please address this before it can be merged."

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
        self.scenario = front.get("scenario")  # graders-rework/: fix|split, absent = both
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
        # rework benchmark (ignored by the official runner): `rework:` + `nudges:` blocks and graders-rework/
        rework = self.cfg.get("rework")
        self.rework: dict = rework if isinstance(rework, dict) else {}
        nudges = self.cfg.get("nudges")
        self.nudges: dict = nudges if isinstance(nudges, dict) else {}
        self.graders: list[Grader] = load_graders(os.path.join(self.dir, "graders"))
        self.rework_graders: list[Grader] = load_graders(os.path.join(self.dir, "graders-rework"))

    def rework_spec(self, scenario: str) -> dict:
        """The `rework.<scenario>` block; ValueError with a precise message when it is missing."""
        spec = self.rework.get(scenario) if self.rework else None
        if not isinstance(spec, dict):
            raise ValueError(f"case {self.name}: {os.path.join(self.dir, 'case.yaml')} has no "
                             f"'rework: {scenario}:' block (required for --scenarios {scenario})")
        return spec

    def stage2_prompt_template(self) -> str:
        p = self.rework.get("prompt") if self.rework else None
        return str(p).strip() if p else DEFAULT_STAGE2_PROMPT

    def nudge(self, stage: int, scenario: Optional[str] = None) -> Optional[str]:
        """nudges.stage1 or nudges.stage2.<scenario> (None when absent)."""
        if stage == 1:
            v = self.nudges.get("stage1")
            return str(v).strip() if v else None
        s2 = self.nudges.get("stage2")
        if isinstance(s2, dict) and scenario:
            v = s2.get(scenario)
            return str(v).strip() if v else None
        return None

    def graders_for_scenario(self, scenario: str) -> list[Grader]:
        return [g for g in self.rework_graders if not g.scenario or str(g.scenario) == scenario]

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
    m = model or case.model
    if m:
        cmd += ["--model", m]
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
            env = dict(os.environ)
            env.pop("GERRIT_STACK_TRACE", None)
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


# --------------------------------------------------------------------------
# gerrit-mcp plugin toggling (arms)
# --------------------------------------------------------------------------

def plugin_enabled(name: str) -> Optional[bool]:
    try:
        out = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().lstrip("❯ ").strip() == name or ln.strip().endswith(" " + name) or ln.strip() == f"❯ {name}":
            for follow in lines[i + 1:i + 6]:
                if "Status:" in follow:
                    return "enabled" in follow and "disabled" not in follow
    return None


def set_plugin(name: str, enabled: bool) -> bool:
    verb = "enable" if enabled else "disable"
    try:
        rc = subprocess.run(["claude", "plugin", verb, name], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"claude plugin {verb} {name} failed: {exc}")
        return False
    if rc.returncode != 0:
        warn(f"claude plugin {verb} {name} exited {rc.returncode}: {rc.stderr.strip()[:200]}")
        return False
    return True


def arm_plugin_state(arm: str, mcp_plugin: str, dry_run: bool) -> Callable[[], None]:
    """Arms no longer toggle the user-scope plugin: runs use --setting-sources project,local and
    load gerrit-mcp explicitly with --plugin-dir, so there is no global state to flip or restore."""
    if dry_run:
        print(f"# arm {arm}: user settings excluded; gerrit-mcp {'loaded via --plugin-dir' if arm != 'without' else 'not loaded'}")
    return lambda: None

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


def push_workspace_for_review(ws: str, url: str, hashtags: list[str], log_path: str) -> list[int]:
    """Legacy wrapper: the created/updated change numbers ([] when nothing was pushed)."""
    return push_workspace_for_review_ex(ws, url, hashtags, log_path)["numbers"]


def push_hashtags(case_name: str, arm: str, run_id: str, scenario: Optional[str] = None,
                  variant: Optional[str] = None, rep: Optional[int] = None) -> list[str]:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)
    tags = [f"bench-{case_name}-{arm}", f"run-{safe}"]
    if scenario:
        tags.append(f"scn-{scenario}-{variant or 'natural'}")
    if rep is not None:
        tags.append(f"rep-{rep}")
    return tags


def chain_commits(ws: str, base: Optional[str] = None) -> list[dict]:
    """The workspace chain bottom-to-top: [{sha, change_id, subject, lines}]. Base = the given sha,
    else `merge-base review/master HEAD`, else the fixture root (excluded)."""
    if not base:
        base = git_out(ws, "merge-base", "review/master", "HEAD")
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
    """The stage-1 push rebased the workspace onto review/master (the demo Gerrit's history),
    so the fixture's local bare remote would no longer share a base with HEAD and every chain
    tool would count Gerrit's root commits as part of the chain. Point origin's master at
    review/master (a bare repo we own) and refresh origin/master."""
    if _git(ws, "rev-parse", "--verify", "-q", "refs/remotes/review/master").returncode != 0:
        return
    _git(ws, "push", "-q", "--force", "origin", "refs/remotes/review/master:refs/heads/master")
    _git(ws, "fetch", "-q", "origin")


def tag_chain(ws: str, chain: list[dict], prefix: str = "refs/bench/stage1") -> None:
    for i, c in enumerate(chain, 1):
        _git(ws, "update-ref", f"{prefix}/{i}", c["sha"])

def run_chain_metrics(plugin_root: str, ws: str, hook_trace: str, out_path: str) -> None:
    script = os.path.join(plugin_root, "scripts", "chain-metrics.sh")
    if not os.path.exists(script):
        return
    try:
        rc = subprocess.run(["bash", script, "--json", "--hook-trace", hook_trace, ws], cwd=plugin_root,
                            capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"chain-metrics failed: {exc}")
        return
    if rc.returncode != 0:
        log(f"chain-metrics exited {rc.returncode}: {rc.stderr.strip()[:200]}")
        return
    try:
        data = json.loads(rc.stdout)
    except ValueError:
        log("chain-metrics printed non-JSON output; ignored")
        return
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def new_stage_record(stage_dir: str, started: _dt.datetime) -> dict:
    return {
        "score": 0.0, "passed": False, "turns": None, "costUsd": 0.0, "judgeCostUsd": 0.0,
        "durationSeconds": 0.0, "startedAt": started.isoformat(), "error": None,
        "tracePath": os.path.join(stage_dir, "trace.jsonl"), "graders": [],
    }


def run_stage(case: Case, prompt: str, graders: list[Grader], arm: str, ws: str, env: dict, stage_dir: str,
              opts: argparse.Namespace, judge_factory, label: str, error: Optional[str] = None,
              started: Optional[_dt.datetime] = None) -> dict:
    """One `claude -p` session in `ws` graded with `graders`; files land in `stage_dir`
    (trace.jsonl, stderr.log, command.txt, last-message.md, hook-trace.log, chain-metrics.json).
    `error` pre-set (e.g. a failed fixture) skips the session but still produces a full record.
    The record keys and their order are the legacy run_one keys."""
    os.makedirs(stage_dir, exist_ok=True)
    started = started or _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec = new_stage_record(stage_dir, started)
    hook_trace = os.path.join(stage_dir, "hook-trace.log")
    open(hook_trace, "a").close()
    env = dict(env)
    env["GERRIT_STACK_TRACE"] = hook_trace
    before = snapshot(ws)
    if error is None:
        cmd = build_claude_cmd(prompt, case, arm, opts.plugin_dir, opts.model, mcp_plugin_dir=opts.mcp_dir)
        with open(os.path.join(stage_dir, "command.txt"), "w", encoding="utf-8") as fh:
            fh.write(shlex.join(cmd) + "\n")
        log(f"{label}: {shlex.join(cmd)[:160]}…")
        rc, timed_out = run_process(cmd, ws, env, case.timeout_seconds, rec["tracePath"],
                                    os.path.join(stage_dir, "stderr.log"))
        if timed_out:
            error = f"timeout after {case.timeout_seconds}s"
        elif rc != 0:
            error = f"claude exited {rc}"
    trace = parse_trace_file(rec["tracePath"])
    if trace.result.get("subtype") not in (None, "success") and error is None:
        error = f"result subtype {trace.result.get('subtype')}"
    if error and trace.result.get("subtype") == "success":
        error = None  # a non-zero exit with a successful result is still gradable
    rec["turns"] = trace.num_turns
    rec["costUsd"] = trace.cost_usd
    rec["model"] = trace.init.get("model")
    rec["subtype"] = trace.result.get("subtype")
    with open(os.path.join(stage_dir, "last-message.md"), "w", encoding="utf-8") as fh:
        fh.write(trace.last_message)
    after = snapshot(ws)
    changed = changed_since(before, after)
    rec["changedFiles"] = changed
    try:
        with open(rec["tracePath"], encoding="utf-8", errors="replace") as fh:
            trace_text = fh.read()
    except OSError:
        trace_text = ""
    kill_stub(ws)
    run_chain_metrics(opts.plugin_dir, ws, hook_trace, os.path.join(stage_dir, "chain-metrics.json"))
    judge = judge_factory(stage_dir) if judge_factory else None
    ctx = GradeContext(trace, trace_text, ws, changed, judge)
    results = [grade(g, ctx, arm) for g in graders]
    rec["graders"] = results
    rec["judgeCostUsd"] = round(ctx.judge_cost, 6)
    rec["score"] = score_graders(results)
    rec["passed"] = rec["score"] >= opts.threshold and error is None
    rec["error"] = error
    rec["durationSeconds"] = round(time.time() - t0, 3)
    return rec


def make_workspace(case: Case, opts: argparse.Namespace, run_dir: str) -> tuple[str, str, dict, Optional[str]]:
    """Temp dir + workspace + env; runs the fixture. -> (tmp, ws, env, error)."""
    tmp = tempfile.mkdtemp(prefix=f"gs-eval-{case.name}-")
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    hook_trace = os.path.join(run_dir, "hook-trace.log")
    open(hook_trace, "a").close()
    env = dict(os.environ)
    env.update(case.env)
    env["EVAL_PLUGIN_ROOT"] = opts.plugin_dir
    env["GERRIT_STACK_TRACE"] = hook_trace
    error: Optional[str] = None
    if case.scaffold_script:
        rc, timed_out = run_process(["bash", case.scaffold_script], ws, dict(env), 300,
                                    os.path.join(run_dir, "fixture.stdout"), os.path.join(run_dir, "fixture.stderr"))
        if rc != 0 or timed_out:
            error = f"fixture exited {rc}" + (" (timeout)" if timed_out else "")
    return tmp, ws, env, error


def finish_workspace(tmp: str, ws: str, run_dir: str, rec: dict, keep: bool) -> None:
    kill_stub(ws)
    if keep:
        dest = os.path.join(run_dir, "workspace")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(tmp, dest)
        rec["workspace"] = os.path.join(dest, "workspace")
    else:
        shutil.rmtree(tmp, ignore_errors=True)


def run_one(case: Case, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """Legacy single-stage run (no --scenarios): fixture, session, graders, chain-metrics, optional push."""
    run_dir = os.path.join(out_dir, "runs", case.name, arm, str(n))
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec: dict[str, Any] = new_stage_record(run_dir, started)
    tmp, ws, env, error = make_workspace(case, opts, run_dir)
    try:
        rec.update(run_stage(case, case.prompt, case.graders, arm, ws, env, run_dir, opts, judge_factory,
                             f"{case.name}/{arm}/{n}", error=error, started=started))
        if getattr(opts, "push_to", None):
            tags = push_hashtags(case.name, arm, os.path.basename(os.path.normpath(out_dir)))
            rec["pushed"] = push_workspace_for_review(ws, opts.push_to, tags, os.path.join(run_dir, "push.log"))
            rec["hashtags"] = tags
            log(f"{case.name}/{arm}/{n}: pushed changes {rec['pushed'] or 'none'} ({', '.join(tags)})")
    except Exception as exc:  # never abort the suite
        rec["error"] = f"runner error: {exc!r}"
    finally:
        rec["durationSeconds"] = round(time.time() - t0, 3)
        finish_workspace(tmp, ws, run_dir, rec, opts.keep)
    return rec


# --------------------------------------------------------------------------
# Rework benchmark: Gerrit REST (as rena), reviewer step, stage 2, metrics
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
    """Admin client from the netrc entry (used only for the project-config toggle); None without one."""
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


def commit_leftovers(ws: str, case_name: str, stage: int) -> bool:
    """Work the agent left uncommitted is committed as ONE commit through the fixture's commit-msg
    hook (so it carries a Change-Id) so it can still be pushed and graded; stage 2 gets a commit on
    top, never an amend. Returns True when a commit was made."""
    if not git_out(ws, "status", "--porcelain").strip():
        return False
    subject = f"{'feat' if stage == 1 else 'fix'}: {case_name} {RUNNER_COMMIT_NOTE}"
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


def _size(change: dict) -> int:
    return int(change.get("insertions") or 0) + int(change.get("deletions") or 0)


def _first_source_file(files: dict) -> Optional[str]:
    paths = sorted(files)
    for pat in (r"^src/main/.*\.java$", r"\.java$", r"."):
        for p in paths:
            if re.search(pat, p):
                return p
    return None


def production_files(files: dict) -> list[str]:
    """Paths under src/main/ that are not documentation (src/main/resources/Documentation/), sorted."""
    return sorted(p for p in files if p.startswith("src/main/") and not p.startswith("src/main/resources/Documentation/"))


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


def select_target(scenario: str, spec: dict, changes: list[dict], files_of: Callable[[int], dict],
                  content_of: Callable[[int, str], str]) -> dict:
    """Pick the change rena comments on. `changes` are the stage-1 ChangeInfos in chain order.
    fix: anchor_file regex over the current-revision file list (fallback: most src/main files, then largest);
    split: the largest change (insertions + deletions). -> {change, path, line, message, reason}."""
    if not changes:
        raise ValueError("no stage-1 changes to review")
    files_cache: dict[int, dict] = {}

    def files(c: dict) -> dict:
        n = int(c["_number"])
        if n not in files_cache:
            try:
                files_cache[n] = files_of(n)
            except RestError:
                files_cache[n] = {}
        return files_cache[n]

    if scenario == "split":
        # the change touching the most production files (ties → largest); with no change touching
        # two of them the stack is already split and the reviewer step is skipped
        prod = {int(c["_number"]): production_files(files(c)) for c in changes}
        best = max(len(v) for v in prod.values())
        pool = [c for c in changes if len(prod[int(c["_number"])]) == best]
        target = max(pool, key=_size)
        tfiles = prod[int(target["_number"])]
        concerns = spec.get("concerns") or []
        if isinstance(concerns, str):
            concerns = [c.strip() for c in concerns.split(",") if c.strip()]
        message = render_message(spec.get("message") or DEFAULT_SPLIT_MESSAGE, n=len(tfiles),
                                 files=", ".join(os.path.basename(p) for p in tfiles),
                                 concerns=", ".join(str(c) for c in concerns))
        path = tfiles[0] if tfiles else _first_source_file(files(target))
        out = {"change": target, "path": path, "line": 1, "message": message,
               "reason": f"most production files ({len(tfiles)}), then largest", "production_files": tfiles}
        if best < 2:
            out["skipped"] = SPLIT_NOT_APPLICABLE
        return out
    if scenario != "fix":
        raise ValueError(f"unknown scenario {scenario!r}")
    anchor_file = spec.get("anchor_file")
    target, path, reason = None, None, ""
    if anchor_file:
        rx = re.compile(str(anchor_file))
        for c in changes:
            hits = sorted(p for p in files(c) if rx.search(p))
            if hits:
                target, path, reason = c, hits[0], f"anchor_file /{anchor_file}/"
                break
    if target is None:
        def main_files(c: dict) -> int:
            return sum(1 for p in files(c) if p.startswith("src/main"))
        best = max(main_files(c) for c in changes)
        pool = [c for c in changes if main_files(c) == best] if best > 0 else list(changes)
        target = max(pool, key=_size)
        path = _first_source_file(files(target))
        reason = "fallback: most src/main files, then largest" if best > 0 else "fallback: largest change"
    line = 1
    if path and spec.get("anchor_line"):
        try:
            line = anchor_line(content_of(int(target["_number"]), path), spec.get("anchor_line"))
        except RestError:
            line = 1
    message = str(spec.get("message") or DEFAULT_FIX_MESSAGE)
    return {"change": target, "path": path, "line": line, "message": message, "reason": reason}


def build_review_payload(path: Optional[str], line: int, message: str) -> dict:
    """The only vote the runner ever posts: rena's Code-Review -1 with one unresolved thread."""
    payload: dict[str, Any] = {"labels": {"Code-Review": -1}, "message": REVIEW_MESSAGE}
    if path:
        payload["comments"] = {path: [{"line": int(line), "unresolved": True, "message": message}]}
    else:
        payload["message"] = REVIEW_MESSAGE + "\n\n" + message
    return payload


def stage2_prompt(case: Case, scenario: str, variant: str, changes: list[int], url: str, project: str,
                  target: Optional[int]) -> str:
    text = render_message(case.stage2_prompt_template(), changes=", ".join(str(c) for c in changes) or "?",
                          url=url, project=project, target=target if target is not None else "?")
    return with_nudge(text, case.nudge(2, scenario) if variant == "nudged" else None)


def with_nudge(prompt: str, nudge: Optional[str]) -> str:
    return prompt + "\n\n" + nudge if nudge else prompt


class Pipeline:
    """One case × scenario × variant (scenario None = legacy single stage)."""

    def __init__(self, case: Case, scenario: Optional[str] = None, variant: str = "natural"):
        self.case = case
        self.scenario = scenario
        self.variant = variant
        self.key = case.name if scenario is None else f"{case.name}@{scenario}-{variant}"

    @property
    def name(self) -> str:
        return self.key

    def stage1_prompt(self) -> str:
        if self.scenario is None:
            return self.case.prompt
        return with_nudge(self.case.prompt, self.case.nudge(1) if self.variant == "nudged" else None)


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


def range_signature(ws: str, base: str, tip: str) -> list[str]:
    out = []
    for ln in git_out(ws, "diff", "--no-color", "-M", base, tip).splitlines():
        if ln.startswith("diff --git ") or (ln.startswith(("+", "-")) and not ln.startswith(("+++ ", "--- "))):
            out.append(ln)
    return out


def interdiff_lines(old: list[str], new: list[str]) -> int:
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            n += (i2 - i1) + (j2 - j1)
    return n


def compute_rework_metrics(ws: str, scenario: str, stage1_chain: list[dict], stage2_chain: list[dict],
                           target_change_id: Optional[str], stage1_numbers: dict, stage2_numbers: dict,
                           last_message: str, gerrit_after: Optional[dict], reviewer_id: Optional[int],
                           review_time: Optional[str], stage1_base: Optional[str], stage2_base: Optional[str]) -> dict:
    """rework-metrics.json per the contract. Chains are bottom-to-top [{sha, change_id, lines}];
    `stage1_numbers`/`stage2_numbers` map Change-Id -> Gerrit number."""
    s1_ids = [c["change_id"] for c in stage1_chain if c.get("change_id")]
    s2_ids = [c["change_id"] for c in stage2_chain if c.get("change_id")]
    s1_by = {c["change_id"]: c for c in stage1_chain if c.get("change_id")}
    s2_by = {c["change_id"]: c for c in stage2_chain if c.get("change_id")}
    sig1 = {cid: patch_signature(ws, c["sha"]) for cid, c in s1_by.items()}
    sig2 = {cid: patch_signature(ws, c["sha"]) for cid, c in s2_by.items()}
    inter = {cid: interdiff_lines(sig1[cid], sig2[cid]) for cid in s1_by if cid in s2_by}
    t_idx = s1_ids.index(target_change_id) if target_change_id in s1_ids else None
    descendants = s1_ids[t_idx + 1:] if t_idx is not None else []
    ancestors = s1_ids[:t_idx] if t_idx is not None else []
    m: dict[str, Any] = {
        "target_change": stage1_numbers.get(target_change_id) if target_change_id else None,
        "target_change_id": target_change_id,
        "stage1_changes": [stage1_numbers[c] for c in s1_ids if c in stage1_numbers],
        "stage2_changes": [stage2_numbers[c] for c in s2_ids if c in stage2_numbers],
        "fixup_on_target": (target_change_id in inter and inter[target_change_id] > 0) if target_change_id else None,
        "change_id_set_preserved": set(s1_ids) == set(s2_ids) if s1_ids else None,
        "new_changes_opened": len(set(s2_ids) - set(s1_ids)),
        "descendants_total": len(descendants),
        "descendants_rebased": sum(1 for c in descendants if c in inter and inter[c] == 0),
        "changes_needing_reread": sum(1 for c in s1_ids if c in inter and inter[c] > 0),
        "interdiff_lines": inter.get(target_change_id) if target_change_id else None,
        "landable_below": sum(1 for c in ancestors if c in inter and inter[c] == 0),
        "landable_below_lines": sum(s2_by[c]["lines"] for c in ancestors if c in inter and inter[c] == 0),
        "split_count": (len(s2_ids) - len(s1_ids)) if scenario == "split" else None,
        "split_equivalent": None,
        "reply_drafted": None,
        "reply_conventional": None,
        "reply_posted": None,
        "vote_posted": None,
        "lost_change_ids": sorted(set(s1_ids) - set(s2_ids)),
        "interdiff_by_change": {stage1_numbers.get(c, c): v for c, v in inter.items()},
        "split_applicable": True if scenario == "split" else None,
        "stage1_push_error": None,
        "stage2_push_error": None,
        "stage1_runner_committed": None,
        "stage2_runner_committed": None,
    }
    if scenario == "split" and stage1_chain and stage2_chain:
        b1 = stage1_base or f"{stage1_chain[0]['sha']}^"
        b2 = stage2_base or f"{stage2_chain[0]['sha']}^"
        m["split_equivalent"] = range_signature(ws, b1, stage1_chain[-1]["sha"]) == range_signature(ws, b2, stage2_chain[-1]["sha"])
    text = last_message or ""
    m["reply_conventional"] = bool(CONVENTIONAL_RE.search(text))
    m["reply_drafted"] = bool(text.strip()) and (m["reply_conventional"]
                                                 or re.search(r"(?i)\b(reply|replies|response|respond|reviewer)\b", text) is not None)
    if gerrit_after is not None:
        m["reply_posted"], m["vote_posted"] = posted_by_others(gerrit_after, reviewer_id, review_time,
                                                              set(m["stage1_changes"]))
    return m


def null_rework_metrics(scenario: str, stage1_numbers: list[int]) -> dict:
    """rework-metrics.json for a pipeline that stopped after stage 1 (push failed, or split not
    applicable): every contract key present, null where nothing was measured."""
    keys = ("target_change", "target_change_id", "stage1_changes", "stage2_changes", "fixup_on_target",
            "change_id_set_preserved", "new_changes_opened", "descendants_total", "descendants_rebased",
            "changes_needing_reread", "interdiff_lines", "landable_below", "landable_below_lines", "split_count",
            "split_equivalent", "reply_drafted", "reply_conventional", "reply_posted", "vote_posted",
            "lost_change_ids", "interdiff_by_change", "split_applicable", "stage1_push_error", "stage2_push_error",
            "stage1_runner_committed", "stage2_runner_committed")
    m: dict[str, Any] = {k: None for k in keys}
    m["stage1_changes"] = list(stage1_numbers)
    return m


def _after(ts: Optional[str], since: Optional[str]) -> bool:
    return bool(ts) and (not since or str(ts)[:19] >= since[:19])


def posted_by_others(gerrit_after: dict, reviewer_id: Optional[int], since: Optional[str],
                     stage1_numbers: set) -> tuple[int, int]:
    """(replies, votes) on the stage-1 changes by any account other than the reviewer after the
    reviewer step: change messages (non-autogenerated), inline comments and non-zero label votes."""
    replies = votes = 0
    for c in gerrit_after.get("changes") or []:
        if stage1_numbers and int(c.get("_number", -1)) not in stage1_numbers:
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


class PipelineStop(Exception):
    """Ends a pipeline early after its metrics were written (message = error, empty = clean skip)."""


def run_pipeline(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    """Stage 1 → push → reviewer step (rena) → stage 2 → push → read-back → metrics."""
    case, scenario, variant = pipe.case, pipe.scenario, pipe.variant
    assert scenario is not None
    label = f"{pipe.key}/{arm}/{n}"
    run_dir = os.path.join(out_dir, "runs", pipe.key, arm, str(n))
    stage2_dir = os.path.join(run_dir, "stage2")
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec: dict[str, Any] = new_stage_record(run_dir, started)
    run_id = os.path.basename(os.path.normpath(out_dir))
    tags = push_hashtags(case.name, arm, run_id, scenario, variant, n)
    base_url, prefix, project = gerrit_from_push_url(opts.push_to)
    tmp, ws, env, error = make_workspace(case, opts, run_dir)
    review: Optional[dict] = None
    stage2: Optional[dict] = None
    rework: Optional[dict] = None
    stage2_skipped = False
    guardrails: dict[str, Any] = {"stage1": None, "stage2": None}
    try:
        spec = case.rework_spec(scenario)
        rest = GerritRest(base_url, prefix, REVIEWER, read_token(opts.rena_token))
        # ---- stage 1
        rh0, gm0 = remote_master_sha(ws), gerrit_branch_sha(rest, project)
        rec.update(run_stage(case, pipe.stage1_prompt(), case.graders, arm, ws, env, run_dir, opts, judge_factory,
                             label, error=error, started=started))
        rec["runnerCommitted"] = commit_leftovers(ws, case.name, 1)
        if rec["runnerCommitted"]:
            log(f"{label}: stage 1 left uncommitted work; committed by the runner")
        trace1 = parse_trace_file(rec["tracePath"])
        guardrails["stage1"] = stage_guardrails(trace1, os.path.join(run_dir, "hook-trace.log"),
                                                _read_json(os.path.join(run_dir, "chain-metrics.json")),
                                                (rh0, remote_master_sha(ws)), (gm0, gerrit_branch_sha(rest, project)),
                                                left_uncommitted=rec["runnerCommitted"])
        push1 = push_workspace_for_review_ex(ws, opts.push_to, tags, os.path.join(run_dir, "push.log"))
        rec["pushed"] = push1["numbers"]
        rec["hashtags"] = tags
        rec["pushError"] = push1["error"]
        log(f"{label}: stage-1 push {push1['numbers'] or 'nothing'} ({push1['error'] or 'ok'})")
        pushed1 = push1["ok"] or push1["error"] == "no new changes"  # identical patchset already there
        stage1_chain = chain_commits(ws, push1["base"]) if pushed1 else []
        tag_chain(ws, stage1_chain)
        gerrit_changes = query_changes_by_hashtags(rest, project, tags) if pushed1 else []
        if map_change_ids_by_sha(stage1_chain, gerrit_changes):
            log(f"{label}: mapped commits without a Change-Id trailer to Gerrit changes by sha")
        by_cid = {c.get("change_id"): c for c in gerrit_changes}
        stage1_numbers = {c["change_id"]: int(by_cid[c["change_id"]]["_number"]) for c in stage1_chain
                          if c.get("change_id") in by_cid}
        ordered = [by_cid[c["change_id"]] for c in stage1_chain if c.get("change_id") in by_cid]
        ordered += [c for c in gerrit_changes if c not in ordered]
        rec["stage1Chain"] = [{"sha": c["sha"], "changeId": c["change_id"], "number": stage1_numbers.get(c["change_id"]),
                               "subject": c["subject"], "lines": c["lines"]} for c in stage1_chain]
        if not ordered:
            rework = null_rework_metrics(scenario, [])
            rework["stage1_push_error"] = push1["error"] or "no changes found by hashtag"
            rework["stage1_runner_committed"] = rec["runnerCommitted"]
            _write_json(os.path.join(run_dir, "rework-metrics.json"), rework)
            raise PipelineStop(f"stage 1 pushed no change to {opts.push_to} ({rework['stage1_push_error']})")
        # ---- reviewer step (rena)
        target = select_target(scenario, spec, ordered, lambda num: change_files(rest, num),
                               lambda num, p: change_file_content(rest, num, p))
        t_change = target["change"]
        t_num = int(t_change["_number"])
        try:
            reviewer_id = int((rest.get("/accounts/self") or {}).get("_account_id"))
        except (RestError, TypeError, ValueError):
            reviewer_id = None
        review = {
            "scenario": scenario, "variant": variant, "reviewer": REVIEWER, "reviewerAccountId": reviewer_id,
            "targetChange": t_num, "targetChangeId": t_change.get("change_id"), "targetSubject": t_change.get("subject"),
            "reason": target["reason"], "file": target["path"], "line": target["line"], "message": target["message"],
            "productionFiles": target.get("production_files"),
            "stage1Changes": [int(c["_number"]) for c in ordered],
            "stage1Shas": {c["change_id"]: c["sha"] for c in stage1_chain if c.get("change_id")},
            "url": f"{base_url}/c/{project}/+/{t_num}",
        }
        if target.get("skipped"):
            # nothing to ask for: no vote, no comment, no stage 2
            review["skipped"] = target["skipped"]
            _write_json(os.path.join(run_dir, "review.json"), review)
            rework = null_rework_metrics(scenario, [int(c["_number"]) for c in ordered])
            rework["target_change"], rework["target_change_id"] = t_num, t_change.get("change_id")
            rework["split_applicable"] = False
            rework["stage1_push_error"] = push1["error"]
            rework["stage1_runner_committed"] = rec["runnerCommitted"]
            _write_json(os.path.join(run_dir, "rework-metrics.json"), rework)
            stage2_skipped = True
            say(f"   {label}: split not applicable — {target['skipped']}; reviewer step and stage 2 skipped")
            raise PipelineStop()
        payload = build_review_payload(target["path"], target["line"], target["message"])
        review_time = _utc_now_str()
        response = rest.post(f"/changes/{t_num}/revisions/current/review", payload)
        review.update({"payload": payload, "response": response, "postedAt": review_time})
        _write_json(os.path.join(run_dir, "review.json"), review)
        log(f"{label}: rena -1 on change {t_num} {target['path']}:{target['line']} ({target['reason']})")
        # ---- stage 2 prep
        _git(ws, "config", "gerrit-stack.host", base_url)
        sync_origin_with_review(ws)
        _git(ws, "remote", "remove", "review")
        env2 = dict(env)
        env2["GERRIT_HOST"] = base_url
        prompt2 = stage2_prompt(case, scenario, variant, [int(c["_number"]) for c in ordered], base_url, project, t_num)
        rh1, gm1 = remote_master_sha(ws), gerrit_branch_sha(rest, project)
        stage2 = run_stage(case, prompt2, case.graders_for_scenario(scenario), arm, ws, env2, stage2_dir, opts,
                           judge_factory, label + "/stage2")
        stage2["runnerCommitted"] = commit_leftovers(ws, case.name, 2)
        if stage2["runnerCommitted"]:
            log(f"{label}: stage 2 left uncommitted work; committed on top by the runner")
        trace2 = parse_trace_file(stage2["tracePath"])
        guardrails["stage2"] = stage_guardrails(trace2, os.path.join(stage2_dir, "hook-trace.log"),
                                                _read_json(os.path.join(stage2_dir, "chain-metrics.json")),
                                                (rh1, remote_master_sha(ws)), (gm1, gerrit_branch_sha(rest, project)),
                                                left_uncommitted=stage2["runnerCommitted"])
        push2 = push_workspace_for_review_ex(ws, opts.push_to, tags, os.path.join(stage2_dir, "push.log"))
        stage2["pushed"] = push2["numbers"]
        stage2["pushError"] = push2["error"]
        stage2["hashtags"] = tags
        log(f"{label}: stage-2 push {push2['numbers'] or 'nothing'} ({push2['error'] or 'ok'})")
        stage2_chain = chain_commits(ws, push2["base"])
        # ---- read back
        after_changes = query_changes_by_hashtags(rest, project, tags)
        map_change_ids_by_sha(stage2_chain, after_changes)
        comments = {}
        for c in after_changes:
            try:
                comments[str(c["_number"])] = change_comments(rest, int(c["_number"]))
            except RestError as exc:
                comments[str(c["_number"])] = {"error": str(exc)}
        gerrit_after = {"query": tags, "fetchedAt": _utc_now_str(), "changes": after_changes, "comments": comments}
        _write_json(os.path.join(run_dir, "gerrit-after.json"), gerrit_after)
        after_by_cid = {c.get("change_id"): int(c["_number"]) for c in after_changes}
        stage2_numbers = {cid: after_by_cid[cid] for cid in {c.get("change_id") for c in stage2_chain} if cid in after_by_cid}
        rework = compute_rework_metrics(ws, scenario, stage1_chain, stage2_chain, t_change.get("change_id"),
                                        stage1_numbers, stage2_numbers, parse_trace_file(stage2["tracePath"]).last_message,
                                        gerrit_after, reviewer_id, review_time, push1["base"], push2["base"])
        rework["stage1_push_error"] = push1["error"]
        rework["stage2_push_error"] = push2["error"]
        rework["stage1_runner_committed"] = rec["runnerCommitted"]
        rework["stage2_runner_committed"] = stage2["runnerCommitted"]
        _write_json(os.path.join(run_dir, "rework-metrics.json"), rework)
    except PipelineStop as exc:
        if str(exc):
            rec["error"] = (rec.get("error") + "; " if rec.get("error") else "") + f"pipeline error: {exc}"
            rec["passed"] = False
    except Exception as exc:  # never abort the suite
        rec["error"] = (rec.get("error") + "; " if rec.get("error") else "") + f"pipeline error: {exc!r}"
        rec["passed"] = False
    finally:
        if not stage2_skipped:
            rec["stage2"] = stage2
        rec["review"] = review
        rec["rework"] = rework
        rec["guardrails"] = guardrails
        rec["pipelineCostUsd"] = round(rec["costUsd"] + rec.get("judgeCostUsd", 0.0)
                                       + ((stage2 or {}).get("costUsd", 0.0) + (stage2 or {}).get("judgeCostUsd", 0.0)), 6)
        rec["pipelineDurationSeconds"] = round(time.time() - t0, 3)
        finish_workspace(tmp, ws, run_dir, rec, opts.keep)
    return rec


# --------------------------------------------------------------------------
# Aggregation, report
# --------------------------------------------------------------------------

def _mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _has_stage2(runs: list[dict]) -> bool:
    return any(isinstance(r, dict) and "stage2" in r for r in runs)


def run_cost(r: dict) -> float:
    """What a run adds to the cost ceiling: the whole pipeline when there is one."""
    if r.get("pipelineCostUsd") is not None:
        return float(r["pipelineCostUsd"])
    return float(r.get("costUsd", 0.0)) + float(r.get("judgeCostUsd", 0.0))


def aggregate_case(case: Any, arms: dict[str, list[dict]], threshold: float, pipeline: Optional[Pipeline] = None) -> dict:
    by_arm = {}
    for arm, runs in arms.items():
        scores = [r["score"] for r in runs]
        by_arm[arm] = {
            "score": _mean(scores) if runs else None,
            "passRate": round(sum(1 for r in runs if r["passed"]) / len(runs), 4) if runs else None,
            "meanTurns": _mean([r["turns"] for r in runs if r["turns"] is not None]),
            "costUsd": round(sum(r["costUsd"] + r.get("judgeCostUsd", 0.0) for r in runs), 6),
        }
        if _has_stage2(runs):
            s2 = [r["stage2"] for r in runs if isinstance(r.get("stage2"), dict)]
            by_arm[arm]["stage2Score"] = _mean([s["score"] for s in s2]) if s2 else None
            by_arm[arm]["stage2PassRate"] = round(sum(1 for s in s2 if s["passed"]) / len(s2), 4) if s2 else None
            by_arm[arm]["pipelineCostUsd"] = round(sum(run_cost(r) for r in runs), 6)
    primary = "with" if "with" in arms else (next(iter(arms)) if arms else None)
    score = by_arm[primary]["score"] if primary else None
    pass_rate = by_arm[primary]["passRate"] if primary else None
    delta = None
    if "with" in by_arm and by_arm["with"]["score"] is not None:
        for other in ("without", "mcp-only"):
            if other in by_arm and by_arm[other]["score"] is not None:
                delta = round(by_arm["with"]["score"] - by_arm[other]["score"], 4)
                break
    deltas = {}
    if "with" in by_arm and by_arm["with"]["score"] is not None:
        for other in ("without", "mcp-only"):
            if other in by_arm and by_arm[other]["score"] is not None:
                deltas[f"with-{other}"] = round(by_arm["with"]["score"] - by_arm[other]["score"], 4)
    out = {
        "name": pipeline.key if pipeline is not None else case.name,
        "dir": case.dir,
    }
    if pipeline is not None and pipeline.scenario is not None:
        out.update({"baseCase": case.name, "scenario": pipeline.scenario, "variant": pipeline.variant})
    out.update({
        "runsPerCase": max((len(r) for r in arms.values()), default=0),
        "maxTurns": case.max_turns,
        "timeoutSeconds": case.timeout_seconds,
        "aggregates": {"score": score, "passRate": pass_rate, "delta": delta, "byArm": by_arm, "deltas": deltas,
                       "passed": (score is not None and score >= threshold)},
        "arms": arms,
    })
    return out


def aggregate(cases: list[dict], threshold: float, meta: dict) -> dict:
    scores = [c["aggregates"]["score"] for c in cases if c["aggregates"]["score"] is not None]
    deltas = [c["aggregates"]["delta"] for c in cases if c["aggregates"]["delta"] is not None]
    passed = sum(1 for c in cases if c["aggregates"]["passed"])
    total = len(cases)
    out = {
        "schemaVersion": 1,
        "startedAt": meta.get("startedAt"),
        "claudeVersion": meta.get("claudeVersion"),
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


def render_report(agg: dict) -> str:
    a = agg["aggregates"]
    staged = any(_has_stage2(runs) for c in agg["cases"] for runs in c["arms"].values())
    lines = [
        "# gerrit-stack eval report",
        "",
        f"- started: {agg.get('startedAt')}",
        f"- claude: {agg.get('claudeVersion')}",
        f"- arms: {', '.join(agg.get('arms') or [])}",
        f"- cost: ${agg.get('costUsd', 0):.4f} · duration: {agg.get('durationSeconds', 0):.0f}s"
        + (f" · **partial**: {agg.get('partialReason')}" if agg.get("partial") else ""),
        f"- overall score: **{a['overallScore']}** (threshold {agg.get('threshold')}), "
        f"cases passed: {a['casesPassed']}/{a['casesTotal']}, mean delta: {a['meanDelta']}",
        "",
        "| case | arm | runs | score | pass rate | mean turns | cost USD | delta (with − arm) |"
        + (" stage-2 score | pipeline cost USD |" if staged else ""),
        "|---|---|---|---|---|---|---|---|" + ("---|---|" if staged else ""),
    ]
    for c in agg["cases"]:
        by_arm = c["aggregates"]["byArm"]
        for arm, st in by_arm.items():
            d = c["aggregates"]["deltas"].get(f"with-{arm}")
            row = (f"| {c['name']} | {arm} | {len(c['arms'][arm])} | {st['score']} | {st['passRate']} | "
                   f"{st['meanTurns']} | {st['costUsd']:.4f} | {'' if d is None else d} |")
            if staged:
                pc = st.get("pipelineCostUsd")
                row += f" {st.get('stage2Score', '')} | {'' if pc is None else f'{pc:.4f}'} |"
            lines.append(row)
    failures = []
    for c in agg["cases"]:
        for arm, runs in c["arms"].items():
            for i, r in enumerate(runs, 1):
                if r.get("error"):
                    failures.append(f"- {c['name']}/{arm}/{i}: error: {r['error']}")
                for g in r.get("graders", []):
                    if g.get("skipped"):
                        continue
                    if not g.get("passed"):
                        failures.append(f"- {c['name']}/{arm}/{i}: grader `{g['name']}` ({g['type']}) FAIL — {g['detail']}")
                s2 = r.get("stage2")
                if isinstance(s2, dict):
                    if s2.get("error"):
                        failures.append(f"- {c['name']}/{arm}/{i}/stage2: error: {s2['error']}")
                    for g in s2.get("graders", []):
                        if not g.get("skipped") and not g.get("passed"):
                            failures.append(f"- {c['name']}/{arm}/{i}/stage2: grader `{g['name']}` ({g['type']}) FAIL — {g['detail']}")
    lines += ["", "## Failed graders and errors", ""]
    lines += failures or ["(none)"]
    lines.append("")
    if staged:
        lines += ["## Rework and guardrails", "",
                  "| pipeline | arm | n | target | fixup on target | ids preserved | new changes | desc. rebased | "
                  "interdiff | landable below | reply drafted | posted (reply/vote) | bad outcomes s1 | bad outcomes s2 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in agg["cases"]:
            for arm, runs in c["arms"].items():
                for i, r in enumerate(runs, 1):
                    rw = r.get("rework") or {}
                    gr = r.get("guardrails") or {}
                    lines.append(
                        f"| {c['name']} | {arm} | {i} | {rw.get('target_change', '')} | {rw.get('fixup_on_target', '')} | "
                        f"{rw.get('change_id_set_preserved', '')} | {rw.get('new_changes_opened', '')} | "
                        f"{rw.get('descendants_rebased', '')}/{rw.get('descendants_total', '')} | {rw.get('interdiff_lines', '')} | "
                        f"{rw.get('landable_below', '')} | {rw.get('reply_drafted', '')} | "
                        f"{rw.get('reply_posted', '')}/{rw.get('vote_posted', '')} | "
                        f"{_bad_summary(gr.get('stage1'))} | {_bad_summary(gr.get('stage2'))} |")
        lines.append("")
    return "\n".join(lines)


def _bad_summary(stage: Optional[dict]) -> str:
    if not isinstance(stage, dict):
        return ""
    bad = stage.get("bad_outcomes") or {}
    hits = [k for k, v in bad.items() if v]
    return ", ".join(hits) if hits else "none"


def print_summary(agg: dict) -> None:
    a = agg["aggregates"]
    staged = any(_has_stage2(runs) for c in agg["cases"] for runs in c["arms"].values())
    rows = [("case", "arm", "runs", "score", "pass", "turns", "cost") + (("s2 score", "pipeline $") if staged else ())]
    for c in agg["cases"]:
        for arm, st in c["aggregates"]["byArm"].items():
            row = (c["name"], arm, str(len(c["arms"][arm])), str(st["score"]), str(st["passRate"]),
                   str(st["meanTurns"]), f"{st['costUsd']:.3f}")
            if staged:
                pc = st.get("pipelineCostUsd")
                row += (str(st.get("stage2Score", "")), "" if pc is None else f"{pc:.3f}")
            rows.append(row)
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)))
    print(f"\noverall score {a['overallScore']} (threshold {agg.get('threshold')}), "
          f"cases passed {a['casesPassed']}/{a['casesTotal']}, mean delta {a['meanDelta']}, "
          f"cost ${agg.get('costUsd', 0):.4f}" + (f", PARTIAL: {agg.get('partialReason')}" if agg.get("partial") else ""))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def claude_version() -> Optional[str]:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout.strip() or None
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
    ap.add_argument("--model", default=None, help="override the case model")
    ap.add_argument("--judge-model", default="haiku")
    ap.add_argument("--judge-votes", type=int, default=1)
    ap.add_argument("--plugin-dir", default=os.path.dirname(here), help="plugin root for the `with` arm")
    ap.add_argument("--mcp-plugin-dir", default=None, help="installed gerrit-mcp plugin dir for the with/mcp-only arms (default: newest ~/.claude/plugins/cache/gerrit-mcp/gerrit/*)")
    ap.add_argument("--mcp-plugin", default="gerrit@gerrit-mcp", help="plugin toggled per arm ('' = never touch)")
    ap.add_argument("--keep", action="store_true", help="keep run workspaces under the run dir")
    ap.add_argument("--push-to", default=None, metavar="GERRIT_PROJECT_URL",
                    help="after each run, rebase the workspace commits onto <url>/master and push them to refs/for/master with hashtags bench-<case>-<arm> and run-<results-id> (e.g. http://localhost:8080/a/demo-plugin)")
    ap.add_argument("--scenarios", default="", metavar="LIST",
                    help="rework benchmark: comma list of fix,split (default: none = legacy single stage); needs --push-to")
    ap.add_argument("--variants", default="natural", metavar="LIST",
                    help="comma list of natural,nudged (default: natural); nudged appends the case's nudges: lines")
    ap.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                    help="run N pipelines/cases of one arm in parallel (arms stay sequential; default 1)")
    ap.add_argument("--rena-token", default=None, metavar="FILE",
                    help="reviewer token file (default: <plugin-dir>/demo/work/.rena-token); never printed")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
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
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    for s in scenarios:
        if s not in SCENARIOS:
            ap.error(f"unknown scenario {s!r} (choose from {', '.join(SCENARIOS)})")
    variants = [v.strip() for v in args.variants.split(",") if v.strip()] or ["natural"]
    for v in variants:
        if v not in VARIANTS:
            ap.error(f"unknown variant {v!r} (choose from {', '.join(VARIANTS)})")
    if scenarios and not args.push_to:
        ap.error("--scenarios requires --push-to <gerrit project url> (the reviewer step needs a Gerrit)")
    if args.push_to:
        try:
            gerrit_from_push_url(args.push_to)
        except ValueError as exc:
            ap.error(str(exc))
    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    args.scenario_list = scenarios
    args.variant_list = variants
    args.rena_token = args.rena_token or os.path.join(args.plugin_dir, "demo", "work", ".rena-token")
    if args.bench:
        args.eval_dir = os.path.join(args.eval_dir, "bench")
    return args


def build_pipelines(cases: list[Case], opts: argparse.Namespace) -> list[Pipeline]:
    """Legacy: one Pipeline per case (scenario None). Scenarios: case × scenario × variant, and every
    case must carry the rework block for each requested scenario (ValueError names the missing one)."""
    if not opts.scenario_list:
        return [Pipeline(c) for c in cases]
    out = []
    for c in cases:
        for s in opts.scenario_list:
            c.rework_spec(s)  # raises with a precise message when the block is missing
            for v in opts.variant_list:
                out.append(Pipeline(c, s, v))
    return out


def dry_run_pipeline(pipe: Pipeline, arm: str, n: int, opts: argparse.Namespace, run_id: str) -> None:
    case = pipe.case
    print(f"# {pipe.key} / {arm} / run {n}  (cwd: temp workspace; graders: "
          f"{', '.join(g.name for g in case.graders) or 'none'})")
    if case.scaffold_script:
        print(f"bash {shlex.quote(case.scaffold_script)}")
    print(shlex.join(build_claude_cmd(pipe.stage1_prompt(), case, arm, opts.plugin_dir, opts.model, mcp_plugin_dir=opts.mcp_dir)))
    if pipe.scenario is None:
        return
    scenario, variant = pipe.scenario, pipe.variant
    base_url, _prefix, project = gerrit_from_push_url(opts.push_to)
    tags = push_hashtags(case.name, arm, run_id, scenario, variant, n)
    spec = case.rework_spec(scenario)
    print(f"# push 1: git push review HEAD:refs/for/master%{','.join('t=' + t for t in tags)}")
    if scenario == "fix":
        where = f"anchor_file /{spec.get('anchor_file')}/ line /{spec.get('anchor_line') or ''}/"
        msg = str(spec.get("message") or DEFAULT_FIX_MESSAGE)
    else:
        concerns = spec.get("concerns") or []
        where = "the change with most production files under src/main/ (ties: largest); skipped when none has >= 2"
        msg = render_message(spec.get("message") or DEFAULT_SPLIT_MESSAGE, n="<n>", files="<production files>",
                             concerns=", ".join(str(c) for c in concerns))
    print(f"# reviewer ({REVIEWER}): POST /changes/<target>/revisions/current/review Code-Review -1 + unresolved comment on "
          f"{where}: {msg!r}")
    print(f"# stage 2 (same workspace; git config gerrit-stack.host {base_url}; GERRIT_HOST={base_url}; review remote removed; "
          f"graders: {', '.join(g.name for g in case.graders_for_scenario(scenario)) or 'none'})")
    p2 = stage2_prompt(case, scenario, variant, [], base_url, project, None).replace("change(s) ?", "change(s) <stage-1 changes>")
    print(shlex.join(build_claude_cmd(p2, case, arm, opts.plugin_dir, opts.model, mcp_plugin_dir=opts.mcp_dir)))
    print(f"# push 2: same hashtags; read back /changes/?q={'+'.join('hashtag:' + t for t in tags)}")


def run_arm(arm: str, pipelines: list[Pipeline], opts: argparse.Namespace, out_dir: str, judge_factory,
            per_case: dict, budget: dict) -> bool:
    """Run every (pipeline, n) of one arm, serially or on a thread pool (-j). Returns True when the
    cost ceiling stopped the arm. `budget` = {"spent", "lock", "partialReason"} shared across arms."""
    jobs = [(pipe, n, opts.runs or pipe.case.runs) for pipe in pipelines for n in range(1, (opts.runs or pipe.case.runs) + 1)]
    staged = bool(opts.scenario_list)

    def ceiling_hit(pipe: Pipeline, n: int) -> bool:
        with budget["lock"]:
            if opts.max_cost_usd is not None and budget["spent"] >= opts.max_cost_usd:
                if budget["partialReason"] is None:
                    budget["partialReason"] = f"cost ceiling ${opts.max_cost_usd} reached before {pipe.key}/{arm}/{n}"
                    warn(budget["partialReason"])
                return True
        return False

    def execute(pipe: Pipeline, n: int) -> dict:
        rec = run_pipeline(pipe, arm, n, opts, out_dir, judge_factory) if staged \
            else run_one(pipe.case, arm, n, opts, out_dir, judge_factory)
        with budget["lock"]:
            budget["spent"] += run_cost(rec)
        return rec

    def status_line(rec: dict) -> str:
        line = (f"{'PASS' if rec['passed'] else 'FAIL'} score={rec['score']} turns={rec['turns']} cost=${rec['costUsd']:.4f}"
                + (f" judge=${rec['judgeCostUsd']:.4f}" if rec.get("judgeCostUsd") else ""))
        s2 = rec.get("stage2")
        if isinstance(s2, dict):
            line += f" | stage2 {'PASS' if s2.get('passed') else 'FAIL'} score={s2.get('score')} cost=${s2.get('costUsd', 0.0):.4f}"
            line += f" | pipeline=${rec.get('pipelineCostUsd', 0.0):.4f}"
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
    """Set the project's receive.requireChangeId to FALSE for the batch (admin from ~/.netrc, this call
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


def main(argv=None) -> int:
    global VERBOSE
    opts = parse_args(argv)
    VERBOSE = opts.verbose
    cases = discover_cases(opts.eval_dir, opts.case)
    if not cases:
        print(f"no cases found in {opts.eval_dir}" + (f" matching {opts.case}" if opts.case else ""), file=sys.stderr)
        return 1
    started = _dt.datetime.now(_dt.timezone.utc)
    out_dir = opts.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                                           started.strftime("%Y%m%d-%H%M%S"))
    try:
        pipelines = build_pipelines(cases, opts)
    except ValueError as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2
    if opts.scenario_list:
        try:
            read_token(opts.rena_token)
        except RestError as exc:
            if opts.dry_run:
                warn(str(exc))
            else:
                print(f"run.py: {exc}", file=sys.stderr)
                return 2
        for c in cases:
            if not c.rework_graders:
                warn(f"{c.name}: no graders-rework/*.md — stage-2 scores will be 0")
    run_id = os.path.basename(os.path.normpath(out_dir))
    if opts.dry_run:
        if opts.scenario_list:
            base_url, prefix, project = gerrit_from_push_url(opts.push_to)
            print(f"# receive.requireChangeId: PUT {base_url}{prefix}/projects/{_q(project)}/config "
                  f"{{\"require_change_id\": \"FALSE\"}} as the ~/.netrc admin before the first push; restored to INHERIT at the end")
        for arm in opts.arm_list:
            arm_plugin_state(arm, opts.mcp_plugin, dry_run=True)
            for pipe in pipelines:
                runs = opts.runs or pipe.case.runs
                for n in range(1, runs + 1):
                    dry_run_pipeline(pipe, arm, n, opts, run_id)
        if opts.scenario_list:
            print("# receive.requireChangeId: restore INHERIT")
        print(f"# out-dir would be {out_dir}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    meta: dict[str, Any] = {"startedAt": started.isoformat(), "claudeVersion": claude_version(),
                            "arms": opts.arm_list, "partial": False, "partialReason": None, "costUsd": 0.0}
    per_case: dict[str, dict[str, list[dict]]] = {p.key: {a: [] for a in opts.arm_list} for p in pipelines}
    budget: dict[str, Any] = {"spent": 0.0, "lock": threading.Lock(), "partialReason": None}
    t0 = time.time()

    def judge_factory(run_dir: str):
        return make_judge(opts.judge_model, opts.judge_votes, run_dir, run_dir)

    restore_change_id = require_change_id_off(opts) if opts.scenario_list else (lambda: None)
    try:
        for arm in opts.arm_list:
            restore = arm_plugin_state(arm, opts.mcp_plugin, dry_run=False)
            try:
                stop = run_arm(arm, pipelines, opts, out_dir, judge_factory, per_case, budget)
            finally:
                restore()
            if stop:
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
