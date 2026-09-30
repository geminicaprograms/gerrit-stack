#!/usr/bin/env bash
# scripts/lib/gerrit-detect.sh — Gerrit repository detection for gerrit-stack.
#
# `source`d by hook and tool scripts (bash 3.2 compatible, shellcheck-clean).
# Never turns on `set -e`, never changes shell options or the caller's cwd, and
# never touches the network. Every function returns 0 unless its contract says
# otherwise (callers use `if gs_detect; then …`), so the libraries are safe
# under `set -uo pipefail` + `trap 'exit 0' ERR`.
#
# Public API:
#   gs_detect [dir]        exports GS_TOPLEVEL GS_REMOTE GS_HOST GS_BRANCH
#                          GS_PROJECT GS_BASE GS_HOOK_OK GS_HOOKS_DIR GS_STATE_DIR;
#                          returns 1 silently (and unsets them) when `dir` is not
#                          a git work tree, gerrit-stack.enabled=false, or no
#                          Gerrit signal is found.
#   gs_config <key> [default]   value of `git config gerrit-stack.<key>` in the
#                          detected repo (cwd when nothing was detected), else default.
#   gs_state_dir           prints the per-worktree state dir (created on demand);
#                          prints nothing outside a repo.
#   gs_trace <script> <verb> <decision>   appends a TAB-separated line to
#                          $GERRIT_STACK_TRACE when that variable is set.

_GS_GERRIT_URL_RE='(review\.|gerrit|googlesource\.com|gerrithub\.io|:29418/|^https?://[^/]+/a/)'
_GS_VARS='GS_TOPLEVEL GS_REMOTE GS_HOST GS_BRANCH GS_PROJECT GS_BASE GS_HOOK_OK GS_HOOKS_DIR GS_STATE_DIR'

# ---------------------------------------------------------------- internals

# _gs_canon <path> — absolute, symlink-resolved path in _gs_canon_out (best
# effort: the deepest existing directory is resolved with `pwd -P`).
_gs_canon() {
  local p="$1" parent base
  case "$p" in /*) ;; *) p="$PWD/$p" ;; esac
  if [ -d "$p" ]; then
    _gs_canon_out=$(cd "$p" 2>/dev/null && pwd -P) || _gs_canon_out=$p
    return 0
  fi
  parent=${p%/*}
  base=${p##*/}
  [ -n "$parent" ] || parent=/
  if [ -d "$parent" ]; then
    _gs_canon_out=$(cd "$parent" 2>/dev/null && pwd -P) || _gs_canon_out=$parent
    _gs_canon_out="${_gs_canon_out%/}/$base"
  else
    _gs_canon_out=$p
  fi
  return 0
}

# _gs_url_parts <url> — sets _gs_url_scheme (http|https|ssh|git|file|…),
# _gs_url_authority (host[:port], userinfo stripped), _gs_url_host and
# _gs_url_path ("/…" for URLs, the raw path for scp-like and local URLs).
_gs_url_parts() {
  local url="$1" rest head
  _gs_url_scheme='' _gs_url_authority='' _gs_url_host='' _gs_url_path=''
  case "$url" in
    file://*)
      _gs_url_scheme='file'
      _gs_url_path=${url#file://}
      ;;
    *://*)
      _gs_url_scheme=${url%%://*}
      rest=${url#*://}
      _gs_url_authority=${rest%%/*}
      _gs_url_path=${rest#"$_gs_url_authority"}
      _gs_url_authority=${_gs_url_authority##*@}
      _gs_url_host=${_gs_url_authority%%:*}
      ;;
    /*|./*|../*)
      _gs_url_scheme='file'
      _gs_url_path=$url
      ;;
    *:*)
      head=${url%%:*}
      case "$head" in
        */*) _gs_url_scheme='file'; _gs_url_path=$url ;;
        *)
          _gs_url_scheme=ssh
          _gs_url_authority=${head##*@}
          _gs_url_host=$_gs_url_authority
          _gs_url_path=${url#*:}
          ;;
      esac
      ;;
    *)
      _gs_url_scheme='file'
      _gs_url_path=$url
      ;;
  esac
  return 0
}

# _gs_project_from_url <url> — Gerrit project name guessed from a remote URL
# (in _gs_project_out): path without leading "/", "a/" auth prefix, trailing
# "/" or ".git"; basename only for local paths.
_gs_project_from_url() {
  local p
  _gs_url_parts "$1"
  p=$_gs_url_path
  if [ "$_gs_url_scheme" = file ]; then
    p=${p%/}
    p=${p##*/}
  else
    p=${p#/}
    case "$p" in
      a/*) p=${p#a/} ;;
      */a/*) p=${p#*/a/} ;;
    esac
  fi
  p=${p%/}
  p=${p%.git}
  _gs_project_out=$p
  return 0
}

# _gs_host_from_url <url> <project> — http(s) base URL of the Gerrit server in
# _gs_host_out ("" when the URL is not http(s)). Strips userinfo, the "/a/"
# authenticated prefix and everything after it, or a trailing "/<project>".
_gs_host_from_url() {
  local project="$2" p
  _gs_host_out=''
  _gs_url_parts "$1"
  case "$_gs_url_scheme" in http|https) ;; *) return 0 ;; esac
  _gs_host_out="$_gs_url_scheme://$_gs_url_authority"
  p=${_gs_url_path%/}
  case "$p" in
    /a/*|/a) ;;
    */a/*) _gs_host_out="$_gs_host_out${p%%/a/*}" ;;
    */a) _gs_host_out="$_gs_host_out${p%/a}" ;;
    *)
      p=${p%.git}
      if [ -n "$project" ]; then
        case "$p" in
          */"$project") _gs_host_out="$_gs_host_out${p%/"$project"}" ;;
        esac
      fi
      ;;
  esac
  return 0
}

# _gs_remote_url <top> <remote> — URL in _gs_remote_url_out ("" when unknown).
_gs_remote_url() {
  _gs_remote_url_out=$(git -C "$1" remote get-url "$2" 2>/dev/null) || _gs_remote_url_out=''
  return 0
}

# _gs_gitreview_get <file> <key> — value of gerrit.<key> in a .gitreview file.
_gs_gitreview_get() {
  git config -f "$1" --get "gerrit.$2" 2>/dev/null || true
}

# _gs_push_branch <top> <remote> — branch named by a HEAD:refs/for/<branch>
# push refspec of <remote> (in _gs_push_branch_out, "%options" stripped).
_gs_push_branch() {
  local spec specs
  _gs_push_branch_out=''
  specs=$(git -C "$1" config --get-all "remote.$2.push" 2>/dev/null) || specs=''
  for spec in $specs; do
    case "$spec" in
      *:refs/for/*)
        spec=${spec#*:refs/for/}
        spec=${spec%%%*}
        _gs_push_branch_out=$spec
        return 0
        ;;
    esac
  done
  return 0
}

# ---------------------------------------------------------------- public API

gs_detect() {
  local dir="${1:-.}" top enabled remotes r url gitreview
  local gr_host='' gr_port='' gr_project='' gr_branch='' gr_remote='' gr_scheme=''
  local remote='' branch='' project='' host='' base='' hooks state hook_ok

  # shellcheck disable=SC2086  # intentional word splitting of the var list
  unset $_GS_VARS

  [ -d "$dir" ] || return 1
  top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || return 1
  [ -n "$top" ] || return 1

  enabled=$(git -C "$top" config --type=bool gerrit-stack.enabled 2>/dev/null) || enabled=true
  [ "$enabled" != false ] || return 1

  remotes=$(git -C "$top" remote 2>/dev/null) || remotes=''

  gitreview="$top/.gitreview"
  if [ -f "$gitreview" ]; then
    gr_host=$(_gs_gitreview_get "$gitreview" host)
    gr_port=$(_gs_gitreview_get "$gitreview" port)
    gr_project=$(_gs_gitreview_get "$gitreview" project)
    gr_branch=$(_gs_gitreview_get "$gitreview" defaultbranch)
    gr_remote=$(_gs_gitreview_get "$gitreview" defaultremote)
    gr_scheme=$(_gs_gitreview_get "$gitreview" scheme)
  else
    gitreview=''
  fi

  # 1. explicit config
  r=$(git -C "$top" config --get gerrit-stack.remote 2>/dev/null) || r=''
  if [ -n "$r" ]; then
    _gs_remote_url "$top" "$r"
    [ -n "$_gs_remote_url_out" ] && remote=$r
  fi

  # 2. .gitreview: defaultremote → remote whose host matches → origin → first
  if [ -z "$remote" ] && [ -n "$gitreview" ] && [ -n "$remotes" ]; then
    if [ -n "$gr_remote" ]; then
      _gs_remote_url "$top" "$gr_remote"
      [ -n "$_gs_remote_url_out" ] && remote=$gr_remote
    fi
    if [ -z "$remote" ] && [ -n "$gr_host" ]; then
      for r in $remotes; do
        _gs_remote_url "$top" "$r"
        _gs_url_parts "$_gs_remote_url_out"
        if [ "$_gs_url_host" = "$gr_host" ]; then remote=$r; break; fi
      done
    fi
    if [ -z "$remote" ]; then
      for r in $remotes; do
        if [ "$r" = origin ]; then remote=origin; break; fi
      done
    fi
    if [ -z "$remote" ]; then
      for r in $remotes; do remote=$r; break; done
    fi
  fi

  # 3. a HEAD:refs/for/ push refspec on any remote
  if [ -z "$remote" ]; then
    for r in $remotes; do
      _gs_push_branch "$top" "$r"
      if [ -n "$_gs_push_branch_out" ]; then remote=$r; break; fi
    done
  fi

  # 4. Gerrit-looking remote URL (origin preferred)
  if [ -z "$remote" ]; then
    for r in origin $remotes; do
      _gs_remote_url "$top" "$r"
      [ -n "$_gs_remote_url_out" ] || continue
      if [[ $_gs_remote_url_out =~ $_GS_GERRIT_URL_RE ]]; then remote=$r; break; fi
    done
  fi

  [ -n "$remote" ] || return 1
  _gs_remote_url "$top" "$remote"
  url=$_gs_remote_url_out

  # branch: config → upstream of HEAD (when it tracks <remote>) →
  #         .gitreview defaultbranch → push refspec → <remote>/HEAD → master
  branch=$(git -C "$top" config --get gerrit-stack.branch 2>/dev/null) || branch=''
  if [ -z "$branch" ]; then
    r=$(git -C "$top" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || r=''
    case "$r" in
      "$remote"/*) branch=${r#"$remote"/} ;;
    esac
  fi
  [ -n "$branch" ] || branch=$gr_branch
  if [ -z "$branch" ]; then
    _gs_push_branch "$top" "$remote"
    branch=$_gs_push_branch_out
  fi
  if [ -z "$branch" ]; then
    r=$(git -C "$top" symbolic-ref -q "refs/remotes/$remote/HEAD" 2>/dev/null) || r=''
    branch=${r#refs/remotes/"$remote"/}
  fi
  [ -n "$branch" ] || branch=master

  # project: .gitreview → remote URL path
  project=${gr_project%.git}
  project=${project#/}
  if [ -z "$project" ]; then
    _gs_project_from_url "$url"
    project=$_gs_project_out
  fi

  # host (http(s) only): config → remote URL when it is the .gitreview host →
  # .gitreview scheme+host+port when http(s) → remote URL when no .gitreview host
  host=$(git -C "$top" config --get gerrit-stack.host 2>/dev/null) || host=''
  if [ -z "$host" ]; then
    _gs_url_parts "$url"
    if [ -n "$gr_host" ]; then
      if [ "$_gs_url_host" = "$gr_host" ]; then
        _gs_host_from_url "$url" "$project"
        host=$_gs_host_out
      fi
      if [ -z "$host" ]; then
        case "$gr_scheme" in
          http|https) host="$gr_scheme://$gr_host${gr_port:+:$gr_port}" ;;
        esac
      fi
    else
      _gs_host_from_url "$url" "$project"
      host=$_gs_host_out
    fi
  fi
  while [ "${host%/}" != "$host" ]; do host=${host%/}; done

  # base: refs/remotes/<remote>/<branch> → @{upstream} → ""
  if git -C "$top" rev-parse -q --verify "refs/remotes/$remote/$branch" >/dev/null 2>&1; then
    base="refs/remotes/$remote/$branch"
  else
    base=$(git -C "$top" rev-parse --symbolic-full-name '@{upstream}' 2>/dev/null) || base=''
  fi

  # hooks dir (core.hooksPath- and worktree-safe) and per-worktree state dir
  hooks=$(git -C "$top" rev-parse --git-path hooks 2>/dev/null) || hooks=.git/hooks
  case "$hooks" in /*) ;; *) hooks="$top/$hooks" ;; esac
  _gs_canon "$hooks"; hooks=$_gs_canon_out
  state=$(git -C "$top" rev-parse --git-path gerrit-stack 2>/dev/null) || state=.git/gerrit-stack
  case "$state" in /*) ;; *) state="$top/$state" ;; esac
  _gs_canon "$state"; state=$_gs_canon_out

  hook_ok=0
  if [ -f "$hooks/commit-msg" ] && [ -x "$hooks/commit-msg" ] \
    && grep -q 'Change-Id' "$hooks/commit-msg" 2>/dev/null; then
    hook_ok=1
  fi

  export GS_TOPLEVEL="$top" GS_REMOTE="$remote" GS_HOST="$host" GS_BRANCH="$branch" \
    GS_PROJECT="$project" GS_BASE="$base" GS_HOOK_OK="$hook_ok" \
    GS_HOOKS_DIR="$hooks" GS_STATE_DIR="$state"
  return 0
}

gs_config() {
  local key="${1:-}" v
  [ -n "$key" ] || { [ $# -ge 2 ] && printf '%s\n' "$2"; return 0; }
  v=$(git -C "${GS_TOPLEVEL:-.}" config --get "gerrit-stack.$key" 2>/dev/null) || v=''
  if [ -n "$v" ]; then
    printf '%s\n' "$v"
  elif [ $# -ge 2 ]; then
    printf '%s\n' "$2"
  fi
  return 0
}

# _gs_state_path — state dir path in _gs_state_path_out without creating it
# ("" outside a repo).
_gs_state_path() {
  local d
  d=${GS_STATE_DIR:-}
  if [ -z "$d" ]; then
    d=$(git rev-parse --git-path gerrit-stack 2>/dev/null) || d=''
    if [ -n "$d" ]; then
      _gs_canon "$d"
      d=$_gs_canon_out
    fi
  fi
  _gs_state_path_out=$d
  return 0
}

gs_state_dir() {
  _gs_state_path
  [ -n "$_gs_state_path_out" ] || return 0
  mkdir -p "$_gs_state_path_out" 2>/dev/null || true
  printf '%s\n' "$_gs_state_path_out"
  return 0
}

gs_trace() {
  [ -n "${GERRIT_STACK_TRACE:-}" ] || return 0
  printf '%s\t%s\t%s\n' "${1:-}" "${2:-}" "${3:-}" >> "$GERRIT_STACK_TRACE" 2>/dev/null || true
  return 0
}
