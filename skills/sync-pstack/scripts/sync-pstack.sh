#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: sync-pstack.sh [check | add <skill>]" >&2
  exit 2
}

repo="$(git rev-parse --show-toplevel)"
lockfile="$repo/pstack.lock"
skills="$repo/skills"
upstream="${PSTACK_UPSTREAM:-https://github.com/cursor/plugins.git}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/sync-pstack/plugins"
[ -f "$lockfile" ] || { echo "no pstack.lock in $repo" >&2; exit 2; }

cursor_terms='(^|[^[:alnum:]_])cursor($|[^[:alnum:]_])|\.mdc($|[^[:alnum:]_])|pstack-models|generalpurpose|task tool|task subagent|`task`|readonly: (true|false)|grok-[0-9]|composer-[0-9]|(claude|gpt)-[a-z0-9.-]*-(max|xhigh)(-fast)?($|[^[:alnum:]_])'
attention=0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

tracked() {
  awk '!/^#/ && NF >= 2 { print $1, $2, (NF >= 3 ? $3 : "pstack/skills/" $1) }' "$lockfile"
}

set_lock() {
  awk -v s="$1" -v sha="$2" '$1 == s { $2 = sha } { print }' "$lockfile" >"$work/lock"
  cat "$work/lock" >"$lockfile"
}

note() {
  echo "$*"
  case "$1" in conflict | flag | cursor | marker) attention=1 ;; esac
}

fetch_upstream() {
  if [ -d "$cache/.git" ]; then
    git -C "$cache" remote set-url origin "$upstream"
    git -C "$cache" fetch -q origin
    git -C "$cache" remote set-head origin -a >/dev/null
  else
    mkdir -p "$(dirname "$cache")"
    git clone -q "$upstream" "$cache"
  fi
  git -C "$cache" rev-parse origin/HEAD
}

upstream_type() {
  git -C "$cache" cat-file -t "$1:$2" 2>/dev/null || true
}

is_text() {
  [ ! -s "$1" ] || LC_ALL=C grep -qI '' "$1"
}

export_entry() {
  local sha="$1" upath="$2" name="$3" dest="$4"
  mkdir -p "$dest"
  case "$(upstream_type "$sha" "$upath")" in
    tree) git -C "$cache" archive "$sha" "$upath" | tar -x -C "$dest" --strip-components="$(awk -F/ '{ print NF }' <<<"$upath")" ;;
    blob) git -C "$cache" show "$sha:$upath" >"$dest/$name" ;;
  esac
  find "$dest" -type f | while read -r f; do
    if is_text "$f"; then
      sed 's#\.cursor/skills/#.agents/skills/#g' "$f" >"$f.tmp"
      mv "$f.tmp" "$f"
    fi
  done
}

merge_entry() {
  local local="$1" upath="$2" base="$3" head="$4" name="" prefix="$1" scratch b t o f rel
  if [ "$(upstream_type "$head" "$upath")" = blob ]; then
    name="$(basename "$local")"
    prefix="$(dirname "$local")"
  fi
  scratch="$(mktemp -d "$work/entry.XXXXXX")"
  b="$scratch/base"
  t="$scratch/theirs"
  o="$skills/$prefix"
  export_entry "$base" "$upath" "$name" "$b"
  export_entry "$head" "$upath" "$name" "$t"
  (cd "$b" && find . -type f; cd "$t" && find . -type f) | sed 's#^\./##' | sort -u | while read -r f; do
    rel="$prefix/$f"
    if [ -f "$b/$f" ] && [ -f "$t/$f" ] && cmp -s "$b/$f" "$t/$f"; then
      continue
    fi
    if [ ! -f "$t/$f" ]; then
      if [ ! -f "$o/$f" ]; then
        continue
      elif cmp -s "$o/$f" "$b/$f"; then
        rm "$o/$f"
        note removed "$rel"
      else
        note flag "$rel: deleted upstream, edited here"
      fi
    elif [ ! -f "$o/$f" ]; then
      if [ -f "$b/$f" ]; then
        note flag "$rel: changed upstream, deleted here"
      else
        mkdir -p "$(dirname "$o/$f")"
        cp "$t/$f" "$o/$f"
        note added "$rel"
      fi
    elif cmp -s "$o/$f" "$t/$f"; then
      continue
    elif [ -f "$b/$f" ] && cmp -s "$o/$f" "$b/$f"; then
      cp "$t/$f" "$o/$f"
      note updated "$rel"
    elif ! is_text "$o/$f" || ! is_text "$t/$f"; then
      note flag "$rel: binary changed upstream and here"
    else
      [ -f "$b/$f" ] || : >"$b/$f"
      if git merge-file -L ours -L base -L upstream "$o/$f" "$b/$f" "$t/$f"; then
        note updated "$rel"
      else
        note conflict "$rel"
      fi
    fi
  done
}

check() {
  local s
  tracked | while read -r s _ _; do
    [ -e "$skills/$s" ] || continue
    (cd "$skills" && grep -rnIiE "$cursor_terms" "$s" || true) | sed 's/^/cursor /'
    (cd "$skills" && grep -rnIE '^(<<<<<<<|>>>>>>>)( |$)' "$s" || true) | sed 's/^/marker /'
  done | sort -u >"$work/check"
  if [ -s "$work/check" ]; then
    cat "$work/check"
    attention=1
  fi
}

sync_all() {
  local head s base upath changed=0
  head="$(fetch_upstream)"
  echo "upstream $head"
  tracked >"$work/tracked"
  while read -r s base upath; do
    if [ -z "$(upstream_type "$head" "$upath")" ]; then
      note flag "$s: removed upstream"
      continue
    fi
    if [ "$(git -C "$cache" rev-parse "$base:$upath")" != "$(git -C "$cache" rev-parse "$head:$upath")" ]; then
      echo "merged $s"
      git -C "$cache" log --format="  %h %s" "$base..$head" -- "$upath"
      merge_entry "$s" "$upath" "$base" "$head" >"$work/notes"
      cat "$work/notes"
      grep -qE '^(conflict|flag) ' "$work/notes" && attention=1
      set_lock "$s" "$head"
      changed=1
    fi
  done <"$work/tracked"
  [ "$changed" = 1 ] || echo "up to date"
}

add_skill() {
  local s="$1" head
  grep -qE "^$s( |$)" "$lockfile" && { echo "$s is already tracked" >&2; exit 2; }
  [ ! -e "$skills/$s" ] || { echo "skills/$s already exists" >&2; exit 2; }
  head="$(fetch_upstream)"
  [ "$(upstream_type "$head" "pstack/skills/$s")" = tree ] || { echo "pstack has no skill $s" >&2; exit 2; }
  export_entry "$head" "pstack/skills/$s" "" "$skills/$s"
  echo "$s $head" >>"$lockfile"
  echo "added $s"
}

case "${1:-}" in
  "") sync_all ;;
  check) [ $# -eq 1 ] || usage ;;
  add) [ $# -eq 2 ] || usage; add_skill "$2" ;;
  *) usage ;;
esac
check
exit "$attention"
