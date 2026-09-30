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

stdlib only; `python3 evals/run.py --help` for options.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Optional

DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill", "AskUserQuestion"]
DEFAULT_MAX_TURNS = 30
DEFAULT_TIMEOUT = 600
ARMS = ("with", "without", "mcp-only")
GRADER_TYPES = ("regex", "tool_used", "tool_order", "file_exists", "llm", "baseline")

VERBOSE = False


def log(msg: str) -> None:
    if VERBOSE:
        sys.stderr.write(f"[run.py] {msg}\n")
        sys.stderr.flush()


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


class Case:
    def __init__(self, path: str):
        self.dir = os.path.abspath(path)
        self.name = os.path.basename(self.dir.rstrip("/"))
        with open(os.path.join(self.dir, "prompt.md"), encoding="utf-8") as fh:
            self.front, self.prompt = split_frontmatter(fh.read())
        self.name = str(self.front.get("name") or self.name)
        self.scaffold_script: Optional[str] = None
        cy = os.path.join(self.dir, "case.yaml")
        if os.path.exists(cy):
            with open(cy, encoding="utf-8") as fh:
                cfg = parse_yaml(fh.read()) or {}
            ctx = cfg.get("context") or {}
            if isinstance(ctx, dict) and ctx.get("scaffold_script"):
                self.scaffold_script = os.path.join(self.dir, str(ctx["scaffold_script"]))
        self.graders: list[Grader] = []
        gdir = os.path.join(self.dir, "graders")
        if os.path.isdir(gdir):
            for fn in sorted(os.listdir(gdir)):
                if not fn.endswith(".md"):
                    continue
                with open(os.path.join(gdir, fn), encoding="utf-8") as fh:
                    front, body = split_frontmatter(fh.read())
                self.graders.append(Grader(fn[:-3], front, body, os.path.join(gdir, fn)))

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
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project,local",
           "--allowedTools", ",".join(case.allowed_tools),
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


def run_one(case: Case, arm: str, n: int, opts: argparse.Namespace, out_dir: str, judge_factory) -> dict:
    run_dir = os.path.join(out_dir, "runs", case.name, arm, str(n))
    os.makedirs(run_dir, exist_ok=True)
    started = _dt.datetime.now(_dt.timezone.utc)
    t0 = time.time()
    rec: dict[str, Any] = {
        "score": 0.0, "passed": False, "turns": None, "costUsd": 0.0, "judgeCostUsd": 0.0,
        "durationSeconds": 0.0, "startedAt": started.isoformat(), "error": None,
        "tracePath": os.path.join(run_dir, "trace.jsonl"), "graders": [],
    }
    tmp = tempfile.mkdtemp(prefix=f"gs-eval-{case.name}-")
    ws = os.path.join(tmp, "workspace")
    os.makedirs(ws)
    hook_trace = os.path.join(run_dir, "hook-trace.log")
    open(hook_trace, "a").close()
    env = dict(os.environ)
    env.update(case.env)
    env["EVAL_PLUGIN_ROOT"] = opts.plugin_dir
    env["GERRIT_STACK_TRACE"] = hook_trace
    fixture_env = dict(env)
    before: dict = {}
    error: Optional[str] = None
    try:
        if case.scaffold_script:
            rc, timed_out = run_process(["bash", case.scaffold_script], ws, fixture_env, 300,
                                        os.path.join(run_dir, "fixture.stdout"), os.path.join(run_dir, "fixture.stderr"))
            if rc != 0 or timed_out:
                error = f"fixture exited {rc}" + (" (timeout)" if timed_out else "")
        before = snapshot(ws)
        if error is None:
            cmd = build_claude_cmd(case.prompt, case, arm, opts.plugin_dir, opts.model, mcp_plugin_dir=opts.mcp_dir)
            with open(os.path.join(run_dir, "command.txt"), "w", encoding="utf-8") as fh:
                fh.write(shlex.join(cmd) + "\n")
            log(f"{case.name}/{arm}/{n}: {shlex.join(cmd)[:160]}…")
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
        rec["turns"] = trace.num_turns
        rec["costUsd"] = trace.cost_usd
        rec["model"] = trace.init.get("model")
        rec["subtype"] = trace.result.get("subtype")
        with open(os.path.join(run_dir, "last-message.md"), "w", encoding="utf-8") as fh:
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
        run_chain_metrics(opts.plugin_dir, ws, hook_trace, os.path.join(run_dir, "chain-metrics.json"))
        judge = judge_factory(run_dir) if judge_factory else None
        ctx = GradeContext(trace, trace_text, ws, changed, judge)
        results = [grade(g, ctx, arm) for g in case.graders]
        rec["graders"] = results
        rec["judgeCostUsd"] = round(ctx.judge_cost, 6)
        rec["score"] = score_graders(results)
        rec["passed"] = rec["score"] >= opts.threshold and error is None
        rec["error"] = error
    except Exception as exc:  # never abort the suite
        rec["error"] = f"runner error: {exc!r}"
    finally:
        kill_stub(ws)
        rec["durationSeconds"] = round(time.time() - t0, 3)
        if opts.keep:
            dest = os.path.join(run_dir, "workspace")
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(tmp, dest)
            rec["workspace"] = os.path.join(dest, "workspace")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return rec


# --------------------------------------------------------------------------
# Aggregation, report
# --------------------------------------------------------------------------

def _mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def aggregate_case(case: Case, arms: dict[str, list[dict]], threshold: float) -> dict:
    by_arm = {}
    for arm, runs in arms.items():
        scores = [r["score"] for r in runs]
        by_arm[arm] = {
            "score": _mean(scores) if runs else None,
            "passRate": round(sum(1 for r in runs if r["passed"]) / len(runs), 4) if runs else None,
            "meanTurns": _mean([r["turns"] for r in runs if r["turns"] is not None]),
            "costUsd": round(sum(r["costUsd"] + r.get("judgeCostUsd", 0.0) for r in runs), 6),
        }
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
    return {
        "name": case.name,
        "dir": case.dir,
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
        "| case | arm | runs | score | pass rate | mean turns | cost USD | delta (with − arm) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in agg["cases"]:
        by_arm = c["aggregates"]["byArm"]
        for arm, st in by_arm.items():
            d = c["aggregates"]["deltas"].get(f"with-{arm}")
            lines.append(f"| {c['name']} | {arm} | {len(c['arms'][arm])} | {st['score']} | {st['passRate']} | "
                         f"{st['meanTurns']} | {st['costUsd']:.4f} | {'' if d is None else d} |")
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
    lines += ["", "## Failed graders and errors", ""]
    lines += failures or ["(none)"]
    lines.append("")
    return "\n".join(lines)


def print_summary(agg: dict) -> None:
    a = agg["aggregates"]
    rows = [("case", "arm", "runs", "score", "pass", "turns", "cost")]
    for c in agg["cases"]:
        for arm, st in c["aggregates"]["byArm"].items():
            rows.append((c["name"], arm, str(len(c["arms"][arm])), str(st["score"]), str(st["passRate"]),
                         str(st["meanTurns"]), f"{st['costUsd']:.3f}"))
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
    if args.bench:
        args.eval_dir = os.path.join(args.eval_dir, "bench")
    return args


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
    if opts.dry_run:
        for arm in opts.arm_list:
            arm_plugin_state(arm, opts.mcp_plugin, dry_run=True)
            for case in cases:
                runs = opts.runs or case.runs
                for n in range(1, runs + 1):
                    print(f"# {case.name} / {arm} / run {n}  (cwd: temp workspace; graders: "
                          f"{', '.join(g.name for g in case.graders) or 'none'})")
                    if case.scaffold_script:
                        print(f"bash {shlex.quote(case.scaffold_script)}")
                    print(shlex.join(build_claude_cmd(case.prompt, case, arm, opts.plugin_dir, opts.model, mcp_plugin_dir=opts.mcp_dir)))
        print(f"# out-dir would be {out_dir}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    meta: dict[str, Any] = {"startedAt": started.isoformat(), "claudeVersion": claude_version(),
                            "arms": opts.arm_list, "partial": False, "partialReason": None, "costUsd": 0.0}
    per_case: dict[str, dict[str, list[dict]]] = {c.name: {a: [] for a in opts.arm_list} for c in cases}
    spent = 0.0
    t0 = time.time()

    def judge_factory(run_dir: str):
        return make_judge(opts.judge_model, opts.judge_votes, run_dir, run_dir)

    stop = False
    for arm in opts.arm_list:
        if stop:
            break
        restore = arm_plugin_state(arm, opts.mcp_plugin, dry_run=False)
        try:
            for case in cases:
                if stop:
                    break
                runs = opts.runs or case.runs
                for n in range(1, runs + 1):
                    if opts.max_cost_usd is not None and spent >= opts.max_cost_usd:
                        meta["partial"] = True
                        meta["partialReason"] = f"cost ceiling ${opts.max_cost_usd} reached before {case.name}/{arm}/{n}"
                        warn(meta["partialReason"])
                        stop = True
                        break
                    print(f"→ {case.name} [{arm}] run {n}/{runs} …", flush=True)
                    rec = run_one(case, arm, n, opts, out_dir, judge_factory)
                    per_case[case.name][arm].append(rec)
                    spent += rec["costUsd"] + rec.get("judgeCostUsd", 0.0)
                    status = "PASS" if rec["passed"] else "FAIL"
                    print(f"   {status} score={rec['score']} turns={rec['turns']} cost=${rec['costUsd']:.4f}"
                          + (f" judge=${rec['judgeCostUsd']:.4f}" if rec.get("judgeCostUsd") else "")
                          + (f" error={rec['error']}" if rec.get("error") else ""), flush=True)
        finally:
            restore()

    meta["costUsd"] = spent
    meta["durationSeconds"] = time.time() - t0
    case_aggs = [aggregate_case(c, per_case[c.name], opts.threshold) for c in cases]
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
