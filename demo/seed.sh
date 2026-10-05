#!/usr/bin/env bash
# gerrit-stack demo: seed the local Gerrit started by demo/docker-compose.yml.
#
# Usage: bash demo/seed.sh [--reset-netrc] [-v]
#   --reset-netrc  drop the localhost entries from ~/.netrc first (forces a fresh admin token)
#   -v             verbose (every REST call with its HTTP status)
#
# Idempotent: every step checks before it changes anything. Creates the admin
# account (first account = Administrators), reviewer `rena`, project
# `demo-plugin`, clones it into demo/work/demo-plugin with the commit-msg hook,
# pushes the skeleton plus the team files (.gerrit-stack, commitlint.config.mjs),
# sets gerrit-stack.verify-cmd, wires the Gerrit source
# tree for in-tree builds and writes the gerrit-mcp config. Tokens go only into
# ~/.netrc (admin) and demo/work/.rena-token (rena), both mode 600.
# Needs: curl, jq, git, python3, rsync.
set -uo pipefail

GERRIT_URL=${GERRIT_URL:-http://localhost:8080}
GERRIT_URL=${GERRIT_URL%/}
demo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo_dir=$(cd "$demo_dir/.." && pwd -P)
work_dir=$demo_dir/work
clone_dir=$work_dir/demo-plugin
rena_token_file=$work_dir/.rena-token
netrc=$HOME/.netrc
netrc_backup=$HOME/.netrc.gerrit-stack.bak
project=demo-plugin
wait_seconds=180

verbose=0
reset_netrc=0
for arg in "$@"; do
  case $arg in
    -v|--verbose) verbose=1 ;;
    --reset-netrc) reset_netrc=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "seed.sh: unknown flag: $arg" >&2; exit 2 ;;
  esac
done

for tool in curl jq git python3 rsync; do
  command -v "$tool" >/dev/null 2>&1 || { echo "seed.sh: $tool not found on PATH" >&2; exit 2; }
done

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/gerrit-stack-seed.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT
jar=$tmpdir/cookies
failed=0
fallback=()
SECONDS=0

log() { printf '%s\n' "$*"; }
vlog() { [ "$verbose" -eq 1 ] && printf '  %s\n' "$*" >&2; return 0; }
step() { printf '\n== %s (%ss)\n' "$*" "$SECONDS"; }
warn() { printf 'seed.sh: %s\n' "$*" >&2; }
# fail "<what>" "<manual command>"...: record a failed step and its manual fallback
fail() {
  failed=1
  warn "FAILED: $1"
  fallback+=("# $1")
  shift
  for cmd in "$@"; do fallback+=("$cmd"); done
  fallback+=("")
}
random_hex() { python3 -c 'import secrets; print(secrets.token_hex(16))'; }

# rest <METHOD> <url> <json-body or ""> [curl args...] -> sets REST_CODE, REST_BODY (XSSI prefix stripped)
# Bodies (which may carry a token) are written to a mode-600 temp file and sent with
# --data @file, never on curl's argv, so a secret body never shows up in `ps`.
REST_CODE=000
REST_BODY=
rest() {
  local method=$1 url=$2 body=$3
  shift 3
  local out=$tmpdir/rest.body
  local body_file=
  if [ -n "$body" ]; then
    body_file=$(umask 077 && mktemp "$tmpdir/rest-body.XXXXXX")
    printf '%s' "$body" > "$body_file"
  fi
  local args=(-sS -o "$out" -w '%{http_code}' -X "$method")
  if [ -n "$body_file" ]; then
    args+=(-H 'Content-Type: application/json' --data @"$body_file")
  fi
  : > "$out"
  REST_CODE=$(curl "${args[@]}" "$@" "$url" 2>>"$tmpdir/curl.err") || REST_CODE=000
  [ -n "$body_file" ] && rm -f "$body_file"
  REST_BODY=$(sed "1s/^)]}'\$//" "$out")
  vlog "$method ${url#"$GERRIT_URL"} -> $REST_CODE"
}
# rest_a: same, authenticated as admin via ~/.netrc
rest_a() { rest "$1" "$2" "$3" -n; }
# rest_s: same, authenticated with the bootstrap cookie jar + XSRF header
rest_s() { rest "$1" "$2" "$3" -b "$jar" -c "$jar" -H "X-Gerrit-Auth: ${xsrf:-}"; }
admin_ok() { curl -n -fs -o /dev/null "$GERRIT_URL/a/accounts/self"; }
cookie_value() { awk -v n="$1" '$6 == n { v = $7 } END { print v }' "$jar" 2>/dev/null; }

# netrc_edit <write login password | drop>: replace/remove the localhost + 127.0.0.1 entries in ~/.netrc.
# Other entries, comments and macdefs are kept byte for byte; the file ends up mode 600.
# The password is passed via NETRC_TOKEN, an env var scoped to this one invocation, never as
# a command argument, so it never shows up in `ps`.
netrc_edit() {
  local mode=$1 login=${2:-} password=${3:-}
  NETRC_TOKEN="$password" python3 - "$netrc" "$netrc_backup" "$mode" "$login" <<'PY'
import os, re, shutil, sys
path, backup, mode, login = sys.argv[1:5]
password = os.environ.get("NETRC_TOKEN", "")
drop = {"localhost", "127.0.0.1"}
text = ""
if os.path.exists(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)
# Split into entries starting at a `machine`/`default` token; skip over macdef bodies.
tokens = list(re.finditer(r"\S+", text))
starts = []  # (offset, machine-name)
i = 0
while i < len(tokens):
    t = tokens[i].group(0)
    if t == "macdef":
        end = text.find("\n\n", tokens[i].end())
        end = len(text) if end < 0 else end + 2
        while i < len(tokens) and tokens[i].start() < end:
            i += 1
        continue
    if t == "machine" and i + 1 < len(tokens):
        starts.append((tokens[i].start(), tokens[i + 1].group(0)))
        i += 2
        continue
    if t == "default":
        starts.append((tokens[i].start(), "default"))
    i += 1
if starts:
    head = text[: starts[0][0]]
    entries = []
    for n, (off, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(text)
        entries.append((name, text[off:end]))
    kept = head + "".join(body for name, body in entries if name not in drop)
    removed = sum(1 for name, _ in entries if name in drop)
else:
    kept, removed = text, 0
if kept and not kept.endswith("\n"):
    kept += "\n"
if mode == "write":
    for host in ("localhost", "127.0.0.1"):
        kept += "machine %s login %s password %s\n" % (host, login, password)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
    f.write(kept)
os.chmod(path, 0o600)
print("%s: %s %d localhost entr%s%s" % (path, "replaced" if mode == "write" else "removed",
      removed, "y" if removed == 1 else "ies", "; wrote 2" if mode == "write" else ""))
PY
}

# ---------------------------------------------------------------- 1. wait
step "waiting for Gerrit at $GERRIT_URL (up to ${wait_seconds}s)"
waited=0
until curl -fs -o /dev/null "$GERRIT_URL/config/server/version"; do
  if [ "$waited" -ge "$wait_seconds" ]; then
    fail "Gerrit did not answer $GERRIT_URL/config/server/version within ${wait_seconds}s" \
      "docker compose -f $demo_dir/docker-compose.yml ps" \
      "docker compose -f $demo_dir/docker-compose.yml logs --tail 50 gerrit"
    break
  fi
  sleep 3
  waited=$((waited + 3))
done
if [ "$failed" -eq 0 ]; then
  version=$(curl -fs "$GERRIT_URL/config/server/version" | sed "1s/^)]}'\$//" | jq -r . 2>/dev/null)
  log "Gerrit ${version:-?} is up"
else
  printf '\nmanual fallback:\n'; printf '  %s\n' "${fallback[@]}"; exit 1
fi

# ---------------------------------------------------------------- 2. admin
if [ "$reset_netrc" -eq 1 ]; then
  step "dropping localhost entries from ~/.netrc (--reset-netrc)"
  netrc_edit drop
fi
step "admin account + ~/.netrc"
if admin_ok; then
  log "admin credentials in ~/.netrc already work; skipping bootstrap"
else
  : > "$jar"
  # DEVELOPMENT_BECOME_ANY_ACCOUNT: /login/?user_name=admin becomes the account 'admin', creating it when it
  # does not exist yet (the first account on the site lands in Administrators). Fallback: action=create_account.
  curl -sS -L -o /dev/null -c "$jar" -b "$jar" "$GERRIT_URL/login/?user_name=admin" 2>>"$tmpdir/curl.err"
  rest GET "$GERRIT_URL/accounts/self" "" -b "$jar" -c "$jar"
  if [ "$REST_CODE" = 200 ] && [ "$(printf '%s' "$REST_BODY" | jq -r '.username // ""')" = admin ]; then
    log "became account 'admin' (id $(printf '%s' "$REST_BODY" | jq -r ._account_id)) via /login/?user_name=admin"
  else
    : > "$jar"
    curl -sS -L -o /dev/null -c "$jar" -b "$jar" "$GERRIT_URL/login/?action=create_account" 2>>"$tmpdir/curl.err"
    rest GET "$GERRIT_URL/accounts/self" "" -b "$jar" -c "$jar"
    if [ "$REST_CODE" = 200 ]; then
      log "created account $(printf '%s' "$REST_BODY" | jq -r ._account_id) via /login/?action=create_account"
    else
      fail "could not create the first account via $GERRIT_URL/login/?action=create_account (HTTP $REST_CODE)" \
        "curl -c jar -b jar -L '$GERRIT_URL/login/?action=create_account'" \
        "# then continue with the steps below using -b jar and -H 'X-Gerrit-Auth: <XSRF_TOKEN from jar>'"
    fi
  fi
  if [ "$failed" -eq 0 ]; then
    xsrf=$(cookie_value XSRF_TOKEN)
    if [ -z "$xsrf" ]; then
      curl -sS -o /dev/null -c "$jar" -b "$jar" "$GERRIT_URL/" 2>>"$tmpdir/curl.err"
      curl -sS -o /dev/null -c "$jar" -b "$jar" "$GERRIT_URL/accounts/self/detail" 2>>"$tmpdir/curl.err"
      xsrf=$(cookie_value XSRF_TOKEN)
    fi
    [ -n "$xsrf" ] || warn "no XSRF_TOKEN cookie; PUTs on /accounts/self will probably be rejected"
    rest_s PUT "$GERRIT_URL/accounts/self/username" '{"username":"admin"}'
    case $REST_CODE in
      200|201) log "username: admin" ;;
      405) vlog "username already set" ;;
      *) warn "PUT /accounts/self/username -> HTTP $REST_CODE — continuing" ;;
    esac
    rest_s PUT "$GERRIT_URL/accounts/self/name" '{"name":"Admin"}'
    [ "$REST_CODE" = 200 ] || warn "PUT /accounts/self/name -> HTTP $REST_CODE — continuing"
    rest_s PUT "$GERRIT_URL/accounts/self/emails/admin@example.com" '{"no_confirmation":true}'
    case $REST_CODE in 200|201|409) ;; *) warn "PUT /accounts/self/emails/admin@example.com -> HTTP $REST_CODE — continuing" ;; esac

    token=$(random_hex)
    rest_s DELETE "$GERRIT_URL/accounts/self/tokens/demo" ""
    rest_s PUT "$GERRIT_URL/accounts/self/tokens/demo" "{\"token\":\"$token\"}"
    case $REST_CODE in
      200|201) log "token 'demo' set on admin" ;;
      404|405)
        vlog "tokens API unavailable (HTTP $REST_CODE); falling back to password.http"
        rest_s PUT "$GERRIT_URL/accounts/self/password.http" '{"generate":true}'
        if [ "$REST_CODE" = 200 ]; then
          token=$(printf '%s' "$REST_BODY" | jq -r .)
          log "generated HTTP password on admin (password.http fallback)"
        else
          fail "could not set an HTTP token for admin (tokens/demo HTTP 404/405, password.http HTTP $REST_CODE)" \
            "curl -b jar -H 'X-Gerrit-Auth: <XSRF_TOKEN>' -X PUT -H 'Content-Type: application/json' -d '{\"generate\":true}' $GERRIT_URL/accounts/self/password.http"
        fi ;;
      *)
        fail "PUT /accounts/self/tokens/demo -> HTTP $REST_CODE" \
          "curl -b jar -H 'X-Gerrit-Auth: <XSRF_TOKEN>' -X PUT -H 'Content-Type: application/json' -d '{\"token\":\"<32 hex>\"}' $GERRIT_URL/accounts/self/tokens/demo" ;;
    esac
    if [ -n "${token:-}" ]; then
      netrc_edit write admin "$token"
      if admin_ok; then
        log "admin credentials verified against $GERRIT_URL/a/accounts/self"
      else
        fail "\$HOME/.netrc written but $GERRIT_URL/a/accounts/self still rejects admin" \
          "curl -n $GERRIT_URL/a/accounts/self   # check 'machine localhost login admin password <token>' in ~/.netrc"
      fi
    fi
    unset token
  fi
fi

# ---------------------------------------------------------------- 3. rena
step "reviewer account 'rena'"
mkdir -p "$work_dir"
rena_ok() { [ -s "$rena_token_file" ] && curl -fs -o /dev/null -u "rena:$(cat "$rena_token_file")" "$GERRIT_URL/a/accounts/self"; }
if rena_ok; then
  log "rena exists and demo/work/.rena-token works"
else
  rena_token=$(random_hex)
  rest_a PUT "$GERRIT_URL/a/accounts/rena" \
    "{\"name\":\"Rena Reviewer\",\"email\":\"rena@example.com\",\"tokens\":[{\"id\":\"demo\",\"token\":\"$rena_token\"}]}"
  if [ "$REST_CODE" = 400 ]; then
    vlog "AccountInput.tokens rejected; retrying with http_password"
    rest_a PUT "$GERRIT_URL/a/accounts/rena" \
      "{\"name\":\"Rena Reviewer\",\"email\":\"rena@example.com\",\"http_password\":\"$rena_token\"}"
  fi
  case $REST_CODE in
    200|201)
      log "created account rena (id $(printf '%s' "$REST_BODY" | jq -r ._account_id))" ;;
    409)
      log "rena exists; issuing a fresh token"
      rest_a DELETE "$GERRIT_URL/a/accounts/rena/tokens/demo" ""
      rest_a PUT "$GERRIT_URL/a/accounts/rena/tokens/demo" "{\"token\":\"$rena_token\"}"
      if [ "$REST_CODE" != 200 ] && [ "$REST_CODE" != 201 ]; then
        rest_a PUT "$GERRIT_URL/a/accounts/rena/password.http" "{\"http_password\":\"$rena_token\"}"
        [ "$REST_CODE" = 200 ] || fail "could not set a token for rena (HTTP $REST_CODE)" \
          "curl -n -X PUT -H 'Content-Type: application/json' -d '{\"token\":\"<32 hex>\"}' $GERRIT_URL/a/accounts/rena/tokens/demo" \
          "printf '%s' '<32 hex>' > $rena_token_file && chmod 600 $rena_token_file"
      fi ;;
    *)
      fail "PUT /a/accounts/rena -> HTTP $REST_CODE" \
        "curl -n -X PUT -H 'Content-Type: application/json' -d '{\"name\":\"Rena Reviewer\",\"email\":\"rena@example.com\",\"tokens\":[{\"id\":\"demo\",\"token\":\"<32 hex>\"}]}' $GERRIT_URL/a/accounts/rena" \
        "printf '%s' '<32 hex>' > $rena_token_file && chmod 600 $rena_token_file" ;;
  esac
  if [ "$failed" -eq 0 ]; then
    (umask 077 && printf '%s\n' "$rena_token" > "$rena_token_file") && chmod 600 "$rena_token_file"
    if rena_ok; then
      log "rena token stored in demo/work/.rena-token (mode 600) and verified"
    else
      fail "rena token stored but $GERRIT_URL/a/accounts/self rejects it" \
        "curl -u rena:\$(cat $rena_token_file) $GERRIT_URL/a/accounts/self"
    fi
  fi
  unset rena_token
fi

# ---------------------------------------------------------------- 4. project
step "project $project"
rest_a GET "$GERRIT_URL/a/projects/$project" ""
if [ "$REST_CODE" = 200 ]; then
  log "$project exists"
else
  rest_a PUT "$GERRIT_URL/a/projects/$project" "{\"create_empty_commit\":true,\"description\":\"gerrit-stack demo plugin\"}"
  case $REST_CODE in
    200|201) log "created $project" ;;
    409) log "$project exists" ;;
    *) fail "PUT /a/projects/$project -> HTTP $REST_CODE" \
         "curl -n -X PUT -H 'Content-Type: application/json' -d '{\"create_empty_commit\":true,\"description\":\"gerrit-stack demo plugin\"}' $GERRIT_URL/a/projects/$project" ;;
  esac
fi
# Gerrit 3.14's default ACL gives Administrators no 'push' on refs/heads/* (only create/submit), and the seed
# pushes the skeleton straight to refs/heads/master. Grant it on this project only; the demo itself always
# pushes to refs/for/master.
rest_a GET "$GERRIT_URL/a/groups/Administrators" ""
admins_uuid=$(printf '%s' "$REST_BODY" | jq -r '.id // ""' 2>/dev/null)
rest_a GET "$GERRIT_URL/a/access/?project=$project" ""
if [ -n "$admins_uuid" ] && [ "$(printf '%s' "$REST_BODY" | jq -r --arg p "$project" --arg g "$admins_uuid" \
    '.[$p].local["refs/heads/*"].permissions.push.rules[$g].action // ""')" = ALLOW ]; then
  log "Administrators may push to refs/heads/* in $project"
else
  acl="{\"add\":{\"refs/heads/*\":{\"permissions\":{\"push\":{\"rules\":{\"$admins_uuid\":{\"action\":\"ALLOW\"}}}}}}}"
  rest_a POST "$GERRIT_URL/a/projects/$project/access" "$acl"
  if [ -n "$admins_uuid" ] && [ "$REST_CODE" = 200 ]; then
    log "granted push on refs/heads/* to Administrators in $project (seed-only direct push)"
  else
    fail "could not grant push on refs/heads/* to Administrators in $project (HTTP $REST_CODE)" \
      "curl -n $GERRIT_URL/a/groups/Administrators   # take .id" \
      "curl -n -X POST -H 'Content-Type: application/json' -d '{\"add\":{\"refs/heads/*\":{\"permissions\":{\"push\":{\"rules\":{\"<Administrators id>\":{\"action\":\"ALLOW\"}}}}}}}' $GERRIT_URL/a/projects/$project/access"
  fi
fi

# ---------------------------------------------------------------- 5. clone + hook + skeleton
step "clone demo/work/$project + commit-msg hook + skeleton"
clone_ok=0
if [ -d "$clone_dir/.git" ]; then
  log "clone exists: $clone_dir"
  clone_ok=1
elif git clone -q "$GERRIT_URL/a/$project" "$clone_dir" 2>"$tmpdir/git.err"; then
  log "cloned $GERRIT_URL/a/$project"
  clone_ok=1
else
  sed 's/^/  git: /' "$tmpdir/git.err" >&2
  fail "git clone $GERRIT_URL/a/$project failed" \
    "git clone $GERRIT_URL/a/$project $clone_dir"
fi

if [ "$clone_ok" -eq 1 ]; then
  hooks_dir=$(git -C "$clone_dir" rev-parse --path-format=absolute --git-path hooks 2>/dev/null || echo "$clone_dir/.git/hooks")
  mkdir -p "$hooks_dir"
  if [ -x "$hooks_dir/commit-msg" ] && grep -q 'Change-Id' "$hooks_dir/commit-msg"; then
    log "commit-msg hook present"
  elif curl -fsSL "$GERRIT_URL/tools/hooks/commit-msg" -o "$hooks_dir/commit-msg" && chmod +x "$hooks_dir/commit-msg"; then
    log "commit-msg hook installed from $GERRIT_URL/tools/hooks/commit-msg"
  elif cp -f "$repo_dir/tests/fixtures/commit-msg" "$hooks_dir/commit-msg" && chmod +x "$hooks_dir/commit-msg"; then
    log "commit-msg hook installed from tests/fixtures/commit-msg (download failed)"
  else
    fail "could not install the commit-msg hook" \
      "curl -fsSL $GERRIT_URL/tools/hooks/commit-msg -o $hooks_dir/commit-msg && chmod +x $hooks_dir/commit-msg"
  fi

  # Only touch the working tree when it is clean and carries no local work beyond an unpushed seed commit
  # (a rehearsal in progress is left alone).
  seed_subject='chore: seed demo-plugin skeleton'
  git -C "$clone_dir" fetch -q origin 2>/dev/null
  dirty=$(git -C "$clone_dir" status --porcelain 2>/dev/null | head -1)
  foreign=$(git -C "$clone_dir" log --format=%s origin/master..HEAD 2>/dev/null | grep -v -F -x "$seed_subject" | head -1)
  if [ -n "$dirty" ] || [ -n "$foreign" ]; then
    detail=${dirty:+uncommitted changes}
    [ -n "$foreign" ] && detail="${detail:+$detail, }unpushed commit \"$foreign\""
    warn "clone has local work ($detail); leaving it alone. bash demo/reset.sh for a clean slate."
  else
    if [ "$(git -C "$clone_dir" rev-list --count origin/master..HEAD 2>/dev/null || echo 0)" -eq 0 ]; then
      git -C "$clone_dir" checkout -q -B master origin/master 2>/dev/null
    fi
    rsync -a --delete --exclude .git "$demo_dir/skeleton/" "$clone_dir/"
    # Team conventions travel with the repository: commit messages are linted by the repo's own commitlint
    # config, review comments use Conventional Comments. Nothing is installed here; without a `commitlint`
    # command the plugin only mentions that the config is not being checked.
    printf '[gerrit-stack]\n\tcommit-lint = auto\n\tcomment-style = conventional\n' > "$clone_dir/.gerrit-stack"
    cat > "$clone_dir/commitlint.config.mjs" <<'CL'
// Conventional Commits, with git's usual 72-column subject and body wrap
// (config-conventional alone allows 100). Footers keep 100: a trailer cannot wrap.
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'header-max-length': [2, 'always', 72],
    'body-max-line-length': [2, 'always', 72],
  },
};
CL
    if [ -n "$(git -C "$clone_dir" status --porcelain)" ]; then
      if git -C "$clone_dir" add -A \
        && git -C "$clone_dir" -c user.name=Admin -c user.email=admin@example.com commit -q -m "$seed_subject"; then
        log "skeleton committed ($(git -C "$clone_dir" rev-parse --short HEAD))"
      else
        fail "commit of the skeleton failed" \
          "git -C $clone_dir add -A && git -C $clone_dir commit -m '$seed_subject'"
      fi
    fi
    if [ "$(git -C "$clone_dir" rev-list --count origin/master..HEAD 2>/dev/null || echo 0)" -gt 0 ]; then
      if git -C "$clone_dir" push -q origin HEAD:refs/heads/master 2>"$tmpdir/git.err"; then
        log "pushed $(git -C "$clone_dir" rev-parse --short HEAD) to refs/heads/master (seed only; the demo pushes to refs/for/master)"
      else
        sed 's/^/  git: /' "$tmpdir/git.err" >&2
        fail "push of the skeleton to refs/heads/master failed" \
          "git -C $clone_dir push origin HEAD:refs/heads/master"
      fi
    else
      log "skeleton already in sync with origin/master"
    fi
  fi

  git -C "$clone_dir" config gerrit-stack.verify-cmd 'bash tools/quick-check.sh'
  git -C "$clone_dir" config gerrit-stack.remote origin
  git -C "$clone_dir" config remote.origin.push HEAD:refs/for/master
  git -C "$clone_dir" config --unset gerrit-stack.grouping 2>/dev/null || true
  log "git config gerrit-stack.verify-cmd='bash tools/quick-check.sh', gerrit-stack.remote=origin, remote.origin.push=HEAD:refs/for/master (no grouping key)"

  # -------------------------------------------------------------- 6. gerrit tree (in-tree builds)
  tree=${GERRIT_TREE:-$HOME/workspace/open/gerrit-3.14}
  if [ -d "$tree" ]; then
    step "wiring Gerrit tree $tree for in-tree builds"
    if bash "$demo_dir/gerrit-tree.sh" "$tree" "$clone_dir"; then
      :
    else
      fail "demo/gerrit-tree.sh $tree $clone_dir failed (tools/verify.sh will not work; quick-check.sh is unaffected)" \
        "bash $demo_dir/gerrit-tree.sh $tree $clone_dir"
    fi
  else
    log "no Gerrit tree at $tree (set GERRIT_TREE to enable tools/verify.sh); quick-check.sh still works"
  fi
fi

# ---------------------------------------------------------------- 7. gerrit-mcp config
step "gerrit-mcp config"
mcp_dir=$(find "$HOME"/.claude/plugins/cache/gerrit-mcp/gerrit -mindepth 2 -maxdepth 2 -type d -name gerrit_mcp_server 2>/dev/null | while IFS= read -r d; do printf '%s %s\n' "$(stat -f %m "$d" 2>/dev/null || stat -c %Y "$d")" "$d"; done | sort -rn | head -1 | cut -d' ' -f2-)
mcp_host='{"name":"gerrit-stack demo","external_url":"http://localhost:8080/","authentication":{"type":"http_basic"}}'
if [ -z "$mcp_dir" ]; then
  fail "gerrit-mcp not installed (no ~/.claude/plugins/cache/gerrit-mcp/gerrit/*/gerrit_mcp_server)" \
    "claude plugin marketplace add https://github.com/GerritCodeReview/gerrit-mcp-server && claude plugin install gerrit@gerrit-mcp" \
    "printf '%s\\n' '{\"default_gerrit_base_url\":\"http://localhost:8080/\",\"gerrit_hosts\":[$mcp_host]}' > ~/.claude/plugins/cache/gerrit-mcp/gerrit/<version>/gerrit_mcp_server/gerrit_config.json"
else
  mcp_cfg=$mcp_dir/gerrit_config.json
  existing='{}'
  if [ -s "$mcp_cfg" ]; then
    if jq -e 'type == "object"' "$mcp_cfg" >/dev/null 2>&1; then
      existing=$(cat "$mcp_cfg")
    else
      warn "$mcp_cfg is not a JSON object; replacing it (backup: $mcp_cfg.bak)"
      cp -f "$mcp_cfg" "$mcp_cfg.bak"
    fi
  fi
  if jq -n --argjson old "$existing" --argjson host "$mcp_host" '
      $old
      | .default_gerrit_base_url = "http://localhost:8080/"
      | .gerrit_hosts = (
          ((.gerrit_hosts // []) | map(select(((.external_url // "") | test("^https?://(localhost|127\\.0\\.0\\.1):8080/?$")) | not)))
          + [$host])' > "$tmpdir/gerrit_config.json" \
    && cp -f "$tmpdir/gerrit_config.json" "$mcp_cfg"; then
    log "wrote $mcp_cfg ($(jq '.gerrit_hosts | length' "$mcp_cfg") host(s); auth http_basic via ~/.netrc)"
  else
    fail "could not write $mcp_cfg" \
      "printf '%s\\n' '{\"default_gerrit_base_url\":\"http://localhost:8080/\",\"gerrit_hosts\":[$mcp_host]}' > $mcp_cfg"
  fi
fi

# ---------------------------------------------------------------- summary
printf '\n== done in %ss\n' "$SECONDS"
cat <<EOF
Gerrit       : $GERRIT_URL/
Open changes : $GERRIT_URL/q/status:open+project:$project
Project      : $GERRIT_URL/admin/repos/$project
Accounts     : admin (token in ~/.netrc, also used by git and gerrit-mcp) · rena (token in demo/work/.rena-token)
Clone        : $clone_dir  (gerrit-stack.verify-cmd = bash tools/quick-check.sh)
Reviewer bot : bash demo/reviewer-comment.sh <change-number>
Next         : cd $clone_dir && claude --plugin-dir $repo_dir
EOF
if [ "$failed" -eq 1 ]; then
  printf '\nmanual fallback (one or more steps failed; run the equivalent commands by hand):\n'
  printf '  %s\n' "${fallback[@]}"
  exit 1
fi
