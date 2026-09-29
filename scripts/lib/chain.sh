#!/usr/bin/env bash
# scripts/lib/chain.sh — relation-chain queries, state snapshots and shell
# command parsing for gerrit-stack. Sources gerrit-detect.sh.
#
# bash 3.2 compatible, shellcheck-clean, safe under `set -uo pipefail` +
# `trap 'exit 0' ERR`: no function returns non-zero unless documented below.
#
# Public API:
#   gs_chain_commits [base]      shas oldest→newest of base..HEAD (base defaults
#                                to $GS_BASE; nothing when empty)
#   gs_change_ids_of <sha>       every Change-Id footer value, one per line
#                                (JGit footer semantics: last paragraph only)
#   gs_subject_of <sha>          first line of the commit message
#   gs_is_fixup <sha>            0 iff the subject starts fixup!/squash!/amend!
#   gs_diffstat_of <sha>         "<lines> <files>" (insertions+deletions)
#   gs_snapshot_write            Change-Id set of the chain → $GS_STATE_DIR/chain-ids
#   gs_snapshot_diff             prints "lost: I…"/"new: I…" lines; 1 on drift,
#                                0 when identical or when there is no snapshot
#   gs_session_mark <id>         touches $GS_STATE_DIR/session-<id>
#   gs_session_marked <id>       0 iff that marker exists
#   gs_parse_git_cmd "<cmd>"     one "dir<TAB>verb<TAB>args" line per git call
#   gs_git_args_have <args> <flag>   0 iff <flag> is present (bundled short
#                                options too, "--opt=" prefix form, stops at --)

# shellcheck source-path=SCRIPTDIR
# shellcheck source=gerrit-detect.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/gerrit-detect.sh"

# ---------------------------------------------------------------- git helpers

# _gs_git <args…> — git in the detected repo (cwd otherwise); never fails,
# never writes to stderr.
_gs_git() {
  git -C "${GS_TOPLEVEL:-.}" "$@" 2>/dev/null || true
}

# shellcheck disable=SC2120  # [base] is optional by contract
gs_chain_commits() {
  local base
  if [ $# -ge 1 ]; then base=$1; else base=${GS_BASE:-}; fi
  [ -n "$base" ] || return 0
  _gs_git rev-list --reverse "$base..HEAD" --
  return 0
}

gs_change_ids_of() {
  local sha="${1:-}"
  [ -n "$sha" ] || return 0
  # Last paragraph of the message only (trailing blank lines ignored); every
  # "Change-Id: <value>" line in it counts, in order.
  _gs_git log -1 --format=%B "$sha" -- | awk '
    /^[[:space:]]*$/ { blank = 1; next }
    { if (blank) { para = ""; blank = 0 } para = para $0 "\n" }
    END {
      n = split(para, lines, "\n")
      for (i = 1; i <= n; i++) {
        if (lines[i] ~ /^Change-Id:[ \t]*/) {
          sub(/^Change-Id:[ \t]*/, "", lines[i])
          sub(/[ \t]+$/, "", lines[i])
          print lines[i]
        }
      }
    }'
  return 0
}

gs_subject_of() {
  local sha="${1:-}"
  [ -n "$sha" ] || return 0
  _gs_git log -1 --format=%s "$sha" --
  return 0
}

gs_is_fixup() {
  local subject
  subject=$(gs_subject_of "${1:-}")
  case "$subject" in
    'fixup! '*|'squash! '*|'amend! '*) return 0 ;;
  esac
  return 1
}

gs_diffstat_of() {
  local sha="${1:-}"
  _gs_git show --numstat --format= "$sha" -- | awk '
    BEGIN { l = 0; f = 0 }
    NF >= 3 { f++; if ($1 != "-") l += $1; if ($2 != "-") l += $2 }
    END { print l, f }'
  return 0
}

# ---------------------------------------------------------------- snapshot

# _gs_chain_id_set — sorted unique Change-Ids of the chain.
_gs_chain_id_set() {
  local sha
  # shellcheck disable=SC2119
  for sha in $(gs_chain_commits); do
    gs_change_ids_of "$sha"
  done | LC_ALL=C sort -u
  return 0
}

gs_snapshot_write() {
  local dir
  dir=$(gs_state_dir)
  [ -n "$dir" ] || return 0
  _gs_chain_id_set > "$dir/chain-ids" 2>/dev/null || true
  return 0
}

gs_snapshot_diff() {
  local file old cur diff
  _gs_state_path
  [ -n "$_gs_state_path_out" ] || return 0
  file="$_gs_state_path_out/chain-ids"
  [ -f "$file" ] || return 0
  old=$(LC_ALL=C sort -u "$file" 2>/dev/null) || old=''
  cur=$(_gs_chain_id_set)
  # Both sets are sorted; tag the lines (O = snapshot, N = current) so one awk
  # pass (BSD awk: no multi-line -v values) prints lost then new, in order.
  diff=$({ printf '%s\n' "$old" | sed 's/^/O /'; printf '%s\n' "$cur" | sed 's/^/N /'; } | awk '
    $1 == "O" && $2 != "" { o[$2] = 1; oa[++oc] = $2 }
    $1 == "N" && $2 != "" { n[$2] = 1; na[++nc] = $2 }
    END {
      for (i = 1; i <= oc; i++) if (!(oa[i] in n)) print "lost: " oa[i]
      for (i = 1; i <= nc; i++) if (!(na[i] in o)) print "new: " na[i]
    }')
  [ -n "$diff" ] || return 0
  printf '%s\n' "$diff"
  return 1
}

# ---------------------------------------------------------------- session marker

# _gs_session_id <id> — file-name-safe id in _gs_session_id_out.
_gs_session_id() {
  local id="${1:-}"
  id=${id//[![:alnum:]._-]/_}
  [ -n "$id" ] || id=unknown
  _gs_session_id_out=$id
  return 0
}

gs_session_mark() {
  local dir
  _gs_session_id "${1:-}"
  dir=$(gs_state_dir)
  [ -n "$dir" ] || return 0
  : > "$dir/session-$_gs_session_id_out" 2>/dev/null || true
  return 0
}

gs_session_marked() {
  _gs_session_id "${1:-}"
  _gs_state_path
  [ -n "$_gs_state_path_out" ] || return 1
  [ -f "$_gs_state_path_out/session-$_gs_session_id_out" ]
}

# ---------------------------------------------------------------- shell parsing

# _gs_unquote <word> — shell-unquoted value in _gs_uq (quotes and backslashes
# removed; $… expansions are left untouched).
_gs_unquote() {
  local raw="$1" i=0 n c q='' out=''
  case "$raw" in
    *[\"\'\\]*) ;;
    *) _gs_uq=$raw; return 0 ;;
  esac
  n=${#raw}
  while [ "$i" -lt "$n" ]; do
    c=${raw:i:1}
    if [ "$q" = "'" ]; then
      if [ "$c" = "'" ]; then q=''; else out+=$c; fi
      i=$((i+1))
      continue
    fi
    if [ "$q" = '"' ]; then
      if [ "$c" = '"' ]; then
        q=''
        i=$((i+1))
      elif [ "$c" = "\\" ]; then
        case "${raw:i+1:1}" in
          '"'|"\\"|'$'|'`') out+=${raw:i+1:1}; i=$((i+2)) ;;
          *) out+=$c; i=$((i+1)) ;;
        esac
      else
        out+=$c
        i=$((i+1))
      fi
      continue
    fi
    case "$c" in
      "\\") out+=${raw:i+1:1}; i=$((i+2)) ;;
      "'") q="'"; i=$((i+1)) ;;
      '"') q='"'; i=$((i+1)) ;;
      *) out+=$c; i=$((i+1)) ;;
    esac
  done
  _gs_uq=$out
  return 0
}

# _gs_join_dir <base> <rel> — compose a cd/-C target in _gs_joined
# ("." stays ".", absolute targets replace, "~" expands to $HOME).
_gs_join_dir() {
  local base="$1" rel="$2" tilde='~'
  case "$rel" in
    "$tilde") rel=${HOME:-} ;;
    "$tilde/"*) rel="${HOME:-}${rel#"$tilde"}" ;;
  esac
  while [ "${rel#./}" != "$rel" ]; do rel=${rel#./}; done
  while [ "${#rel}" -gt 1 ] && [ "${rel%/}" != "$rel" ]; do rel=${rel%/}; done
  if [ -z "$rel" ] || [ "$rel" = . ]; then
    _gs_joined=$base
  elif [ "${rel#/}" != "$rel" ] || [ "$base" = . ]; then
    _gs_joined=$rel
  else
    _gs_joined="${base%/}/$rel"
  fi
  return 0
}

# _gs_process_segment <word…> — one simple command (raw words). Updates
# _gs_cur_dir on cd/pushd; prints "dir<TAB>verb<TAB>args" when it is a git call.
_gs_process_segment() {
  local -a w=("$@")
  local nw=$# k=0 m uq name dir verb='' args=''
  # leading keywords, wrapper commands (and their options) and VAR=x assignments
  while [ "$k" -lt "$nw" ]; do
    _gs_unquote "${w[k]}"; uq=$_gs_uq
    case "$uq" in
      if|then|else|elif|do|while|until|'!'|'{'|'}')
        k=$((k+1)) ;;
      time|command|exec|env|sudo|doas|nohup|nice|builtin)
        k=$((k+1))
        while [ "$k" -lt "$nw" ]; do
          _gs_unquote "${w[k]}"
          case "$_gs_uq" in -?*) k=$((k+1)) ;; *) break ;; esac
        done ;;
      *=*)
        case "${uq%%=*}" in
          ''|*[![:alnum:]_]*) break ;;
          *) k=$((k+1)) ;;
        esac ;;
      *) break ;;
    esac
  done
  [ "$k" -lt "$nw" ] || return 0
  _gs_unquote "${w[k]}"; name=$_gs_uq
  case "$name" in
    cd|pushd)
      k=$((k+1))
      while [ "$k" -lt "$nw" ]; do
        _gs_unquote "${w[k]}"; uq=$_gs_uq
        case "$uq" in
          -) return 0 ;;              # cd - : previous dir, unknown
          --) k=$((k+1)); break ;;
          -?*) k=$((k+1)) ;;          # -L -P -e -@
          *) break ;;
        esac
      done
      if [ "$k" -lt "$nw" ]; then
        _gs_unquote "${w[k]}"; uq=$_gs_uq
      else
        uq='~'
      fi
      _gs_join_dir "$_gs_cur_dir" "$uq"
      _gs_cur_dir=$_gs_joined
      return 0 ;;
  esac
  case "${name##*/}" in git) ;; *) return 0 ;; esac
  dir=$_gs_cur_dir
  k=$((k+1))
  # git global options
  while [ "$k" -lt "$nw" ]; do
    _gs_unquote "${w[k]}"; uq=$_gs_uq
    case "$uq" in
      -C)
        if [ $((k+1)) -lt "$nw" ]; then
          _gs_unquote "${w[k+1]}"
          _gs_join_dir "$dir" "$_gs_uq"
          dir=$_gs_joined
        fi
        k=$((k+2)) ;;
      -c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--attr-source|--shell-path)
        k=$((k+2)) ;;
      --git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--config-env=*|--attr-source=*|--shell-path=*|--exec-path=*|--list-cmds=*)
        k=$((k+1)) ;;
      -p|--paginate|-P|--no-pager|--no-replace-objects|--no-lazy-fetch|--no-optional-locks|--no-advice|--bare|--literal-pathspecs|--no-literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs)
        k=$((k+1)) ;;
      *) break ;;
    esac
  done
  if [ "$k" -lt "$nw" ]; then
    _gs_unquote "${w[k]}"; verb=$_gs_uq
    m=$((k+1))
    if [ "$m" -lt "$nw" ]; then
      args=${w[m]}
      m=$((m+1))
      while [ "$m" -lt "$nw" ]; do
        args+=" ${w[m]}"
        m=$((m+1))
      done
    fi
  fi
  printf '%s\t%s\t%s\n' "$dir" "$verb" "$args"
  return 0
}

# _gs_scan <text> <start> <dir> <mode> — tokenise <text> from index <start>
# into simple commands and hand each to _gs_process_segment. mode: top (stop at
# end), paren (stop at the unmatched ")"), bt (stop at the closing backtick).
# Sets _gs_pos to the index of the terminator (or ${#text}). Recurses for
# $(…), `…` and (…) so nested commands are seen and subshell cd's do not leak.
_gs_scan() {
  local text="$1" i="$2" dir="$3" mode="$4"
  local n=${#text} c buf='' q='' j raw line term strip rest tab=$'\t'
  local -a words=() hd_term=() hd_strip=()
  local nwords=0 nhd=0 h

  _gs_flush_word() {
    if [ -n "$buf" ]; then words[nwords]=$buf; nwords=$((nwords+1)); buf=''; fi
  }
  _gs_flush_segment() {
    _gs_flush_word
    if [ "$nwords" -gt 0 ]; then
      _gs_cur_dir=$dir
      _gs_process_segment "${words[@]}"
      dir=$_gs_cur_dir
      words=()
      nwords=0
    fi
  }

  _gs_pos=$n
  while [ "$i" -lt "$n" ]; do
    c=${text:i:1}
    if [ "$q" = "'" ]; then
      if [ "$c" = "'" ]; then q=''; fi
      if [ "$c" = $'\n' ]; then buf+=' '; else buf+=$c; fi
      i=$((i+1))
      continue
    fi
    # q is '' or '"'
    case "$c" in
      "\\")
        if [ "${text:i+1:1}" = $'\n' ]; then
          i=$((i+2))                     # line continuation
        else
          buf+=${text:i:2}
          i=$((i+2))
        fi
        continue ;;
      '"')
        if [ "$q" = '"' ]; then q=''; else q='"'; fi
        buf+=$c
        i=$((i+1))
        continue ;;
      "'")
        if [ -z "$q" ]; then q="'"; fi
        buf+=$c
        i=$((i+1))
        continue ;;
      '$')
        if [ "${text:i+1:1}" = '(' ]; then
          _gs_scan "$text" $((i+2)) "$dir" paren
          j=$_gs_pos
          raw=${text:i:j-i+1}
          buf+=${raw//$'\n'/ }
          i=$((j+1))
        else
          buf+=$c
          i=$((i+1))
        fi
        continue ;;
      '`')
        if [ "$mode" = bt ]; then
          _gs_flush_segment
          _gs_pos=$i
          return 0
        fi
        _gs_scan "$text" $((i+1)) "$dir" bt
        j=$_gs_pos
        raw=${text:i:j-i+1}
        buf+=${raw//$'\n'/ }
        i=$((j+1))
        continue ;;
    esac
    if [ "$q" = '"' ]; then
      if [ "$c" = $'\n' ]; then buf+=' '; else buf+=$c; fi
      i=$((i+1))
      continue
    fi
    # unquoted
    case "$c" in
      ' '|$'\t')
        _gs_flush_word
        i=$((i+1)) ;;
      $'\n')
        _gs_flush_segment
        i=$((i+1))
        # skip pending heredoc bodies
        h=0
        while [ "$h" -lt "$nhd" ]; do
          term=${hd_term[h]}
          strip=${hd_strip[h]}
          while [ "$i" -lt "$n" ]; do
            rest=${text:i}
            line=${rest%%$'\n'*}
            i=$((i+${#line}+1))
            if [ "$strip" = 1 ]; then
              while [ "${line#"$tab"}" != "$line" ]; do line=${line#"$tab"}; done
            fi
            [ "$line" != "$term" ] || break
          done
          h=$((h+1))
        done
        nhd=0
        hd_term=()
        hd_strip=() ;;
      ';'|'|')
        _gs_flush_segment
        i=$((i+1)) ;;
      '&')
        if [ "${text:i+1:1}" = '>' ]; then
          buf+=$c
          i=$((i+1))
        elif [ "$i" -gt 0 ] && { [ "${text:i-1:1}" = '>' ] || [ "${text:i-1:1}" = '<' ]; }; then
          buf+=$c
          i=$((i+1))
        else
          _gs_flush_segment
          i=$((i+1))
        fi ;;
      '(')
        if [ -z "$buf" ]; then
          _gs_flush_segment
          _gs_scan "$text" $((i+1)) "$dir" paren
          i=$((_gs_pos+1))
        elif [ "${text:i+1:1}" = ')' ]; then
          buf+='()'
          i=$((i+2))
        else
          buf+=$c
          i=$((i+1))
        fi ;;
      ')')
        _gs_flush_segment
        if [ "$mode" = paren ]; then
          _gs_pos=$i
          return 0
        fi
        i=$((i+1)) ;;
      '#')
        if [ -z "$buf" ]; then
          rest=${text:i}
          line=${rest%%$'\n'*}
          i=$((i+${#line}))
        else
          buf+=$c
          i=$((i+1))
        fi ;;
      '<'|'>')
        if [ "$c" = '<' ] && [ "${text:i+1:1}" = '<' ] && [ "${text:i+2:1}" != '<' ]; then
          # heredoc: keep the operator and delimiter in the word, remember the
          # terminator so the body lines are skipped after the next newline
          _gs_flush_word
          buf='<<'
          i=$((i+2))
          strip=0
          if [ "${text:i:1}" = '-' ]; then buf+='-'; strip=1; i=$((i+1)); fi
          while [ "${text:i:1}" = ' ' ] || [ "${text:i:1}" = $'\t' ]; do
            buf+=${text:i:1}
            i=$((i+1))
          done
          raw=''
          while [ "$i" -lt "$n" ]; do
            c=${text:i:1}
            case "$c" in
              ' '|$'\t'|$'\n'|';'|'|'|'&'|'('|')'|'<'|'>') break ;;
            esac
            raw+=$c
            i=$((i+1))
          done
          buf+=$raw
          _gs_unquote "$raw"
          hd_term[nhd]=$_gs_uq
          hd_strip[nhd]=$strip
          nhd=$((nhd+1))
        else
          if [ -n "$buf" ]; then
            case "${buf:${#buf}-1:1}" in
              [0-9]|'&') ;;
              *) _gs_flush_word ;;
            esac
          fi
          buf+=$c
          i=$((i+1))
        fi ;;
      *)
        buf+=$c
        i=$((i+1)) ;;
    esac
  done
  _gs_flush_segment
  _gs_pos=$n
  return 0
}

gs_parse_git_cmd() {
  local cmd="${1:-}"
  [ -n "$cmd" ] || return 0
  _gs_cur_dir=.
  _gs_pos=0
  _gs_scan "$cmd" 0 . top
  return 0
}

# _gs_split_words <string> — whitespace-separated raw words (quote-aware) into
# _gs_words[] / _gs_nwords.
_gs_split_words() {
  local s="$1" i=0 n c q='' buf=''
  _gs_words=()
  _gs_nwords=0
  n=${#s}
  while [ "$i" -lt "$n" ]; do
    c=${s:i:1}
    if [ "$q" = "'" ]; then
      if [ "$c" = "'" ]; then q=''; fi
      buf+=$c
      i=$((i+1))
      continue
    fi
    case "$c" in
      "\\") buf+=${s:i:2}; i=$((i+2)) ;;
      '"') if [ "$q" = '"' ]; then q=''; else q='"'; fi; buf+=$c; i=$((i+1)) ;;
      "'") if [ -z "$q" ]; then q="'"; fi; buf+=$c; i=$((i+1)) ;;
      ' '|$'\t'|$'\n')
        if [ -n "$q" ]; then
          buf+=$c
        elif [ -n "$buf" ]; then
          _gs_words[_gs_nwords]=$buf
          _gs_nwords=$((_gs_nwords+1))
          buf=''
        fi
        i=$((i+1)) ;;
      *) buf+=$c; i=$((i+1)) ;;
    esac
  done
  if [ -n "$buf" ]; then
    _gs_words[_gs_nwords]=$buf
    _gs_nwords=$((_gs_nwords+1))
  fi
  return 0
}

gs_git_args_have() {
  local args="${1:-}" flag="${2:-}" k=0 u letter rest
  [ -n "$args" ] && [ -n "$flag" ] || return 1
  _gs_split_words "$args"
  while [ "$k" -lt "$_gs_nwords" ]; do
    _gs_unquote "${_gs_words[k]}"; u=$_gs_uq
    k=$((k+1))
    [ "$u" != '--' ] || return 1
    [ "$u" != "$flag" ] || return 0
    case "$flag" in
      --*=)
        case "$u" in "$flag"*) return 0 ;; esac ;;
      --*)
        case "$u" in "$flag="*) return 0 ;; esac ;;
      -?)
        letter=${flag#-}
        case "$u" in
          --*) ;;
          -*)
            rest=${u#-}
            case "$rest" in
              *[![:alnum:]]*) ;;
              *"$letter"*) return 0 ;;
            esac ;;
        esac ;;
    esac
  done
  return 1
}
