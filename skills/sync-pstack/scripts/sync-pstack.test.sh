#!/usr/bin/env bash
set -uo pipefail

script="$(cd "$(dirname "$0")" && pwd)/sync-pstack.sh"
failures=0
work=""

setup() {
  work="$(mktemp -d)"
  git init -q -b main "$work/upstream"
  git -C "$work/upstream" config user.email t@t
  git -C "$work/upstream" config user.name t
  git init -q -b main "$work/ours"
  git -C "$work/ours" config user.email t@t
  git -C "$work/ours" config user.name t
  export PSTACK_UPSTREAM="$work/upstream"
  export XDG_CACHE_HOME="$work/cache"
}

teardown() {
  rm -rf "$work"
  unset PSTACK_UPSTREAM XDG_CACHE_HOME
}

up_write() {
  mkdir -p "$(dirname "$work/upstream/pstack/skills/$1")"
  printf '%s' "$2" >"$work/upstream/pstack/skills/$1"
}

up_commit() {
  git -C "$work/upstream" add -A
  git -C "$work/upstream" commit -qm "${1:-change}"
  git -C "$work/upstream" rev-parse HEAD
}

ours_write() {
  mkdir -p "$(dirname "$work/ours/skills/$1")"
  printf '%s' "$2" >"$work/ours/skills/$1"
}

lock() {
  printf '%s\n' "$@" >"$work/ours/pstack.lock"
}

ours_commit() {
  git -C "$work/ours" add -A
  git -C "$work/ours" commit -qm ours
}

sync() {
  (cd "$work/ours" && bash "$script" "$@") >"$work/out" 2>&1
  echo $? >"$work/code"
}

expect_code() {
  local got
  got="$(cat "$work/code")"
  [ "$got" = "$1" ] || { echo "  exit $got, want $1"; sed 's/^/    /' "$work/out"; return 1; }
}

expect_out() {
  grep -qF -- "$1" "$work/out" || { echo "  output lacks: $1"; sed 's/^/    /' "$work/out"; return 1; }
}

expect_file() {
  local got want
  got="$(cat "$work/ours/skills/$1")"
  want="$(printf '%s' "$2")"
  [ "$got" = "$want" ] || { echo "  skills/$1 is:"; sed 's/^/    /' <<<"$got"; echo "  want:"; sed 's/^/    /' <<<"$want"; return 1; }
}

expect_lock() {
  grep -qx "$1" "$work/ours/pstack.lock" || { echo "  lock lacks: $1"; sed 's/^/    /' "$work/ours/pstack.lock"; return 1; }
}

base_doc=$'# Demo\n\nSpawn a Task subagent.\n\nKeep this line.\n\nWrite `.cursor/skills/verify-<app>/SKILL.md`.\n'
port_doc=$'# Demo\n\nSpawn a subagent with the Agent tool.\n\nKeep this line.\n\nWrite `.agents/skills/verify-<app>/SKILL.md`.\n'

test_clean_merge_keeps_port_edits_and_applies_upstream() {
  up_write demo/SKILL.md "$base_doc"
  local base head
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  ours_write demo/LICENSE.txt "mine"
  lock "demo $base"
  ours_commit
  up_write demo/SKILL.md $'# Demo\n\nSpawn a Task subagent.\n\nKeep this line, upstream improved it.\n\nWrite `.cursor/skills/verify-<app>/SKILL.md`.\n'
  head="$(up_commit improve)"
  sync
  expect_code 0 &&
    expect_file demo/SKILL.md $'# Demo\n\nSpawn a subagent with the Agent tool.\n\nKeep this line, upstream improved it.\n\nWrite `.agents/skills/verify-<app>/SKILL.md`.' &&
    expect_file demo/LICENSE.txt "mine" &&
    expect_out "merged demo" &&
    expect_lock "demo $head"
}

test_same_line_edit_conflicts() {
  up_write demo/SKILL.md "$base_doc"
  local base
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  lock "demo $base"
  ours_commit
  up_write demo/SKILL.md $'# Demo\n\nSpawn a Task subagent on the cheap model.\n\nKeep this line.\n\nWrite `.cursor/skills/verify-<app>/SKILL.md`.\n'
  up_commit tweak >/dev/null
  sync
  expect_code 1 &&
    expect_out "conflict demo/SKILL.md" &&
    grep -q '^<<<<<<< ours' "$work/ours/skills/demo/SKILL.md" ||
    { echo "  no conflict markers"; return 1; }
}

test_upstream_added_and_deleted_files() {
  up_write demo/SKILL.md "$base_doc"
  up_write demo/references/old.md "old"
  local base head
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  ours_write demo/references/old.md "old"
  lock "demo $base"
  ours_commit
  git -C "$work/upstream" rm -q pstack/skills/demo/references/old.md
  up_write demo/references/new.md "new"
  head="$(up_commit files)"
  sync
  expect_code 0 &&
    expect_out "added demo/references/new.md" &&
    expect_out "removed demo/references/old.md" &&
    expect_file demo/references/new.md "new" &&
    [ ! -e "$work/ours/skills/demo/references/old.md" ] &&
    expect_lock "demo $head"
}

test_upstream_delete_of_locally_edited_file_is_flagged() {
  up_write demo/SKILL.md "$base_doc"
  up_write demo/references/old.md "old"
  local base
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  ours_write demo/references/old.md "old, ported"
  lock "demo $base"
  ours_commit
  git -C "$work/upstream" rm -q pstack/skills/demo/references/old.md
  up_commit delete >/dev/null
  sync
  expect_code 1 &&
    expect_out "flag demo/references/old.md: deleted upstream, edited here" &&
    expect_file demo/references/old.md "old, ported"
}

test_up_to_date_changes_nothing() {
  up_write demo/SKILL.md "$base_doc"
  local base
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  lock "demo $base"
  ours_commit
  sync
  expect_code 0 &&
    expect_out "up to date" &&
    [ -z "$(git -C "$work/ours" status --porcelain)" ] ||
    { echo "  working tree changed"; git -C "$work/ours" status --short; return 1; }
}

test_skill_removed_upstream_is_reported_and_kept() {
  up_write demo/SKILL.md "$base_doc"
  up_write other/SKILL.md "other"
  local base
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  lock "demo $base"
  ours_commit
  git -C "$work/upstream" rm -rq pstack/skills/demo
  up_commit gone >/dev/null
  sync
  expect_code 1 &&
    expect_out "flag demo: removed upstream" &&
    expect_file demo/SKILL.md "$port_doc" &&
    expect_lock "demo $base"
}

test_merge_that_lets_cursor_text_through_fails_the_check() {
  up_write demo/SKILL.md "$base_doc"
  local base
  base="$(up_commit base)"
  ours_write demo/SKILL.md "$port_doc"
  lock "demo $base"
  ours_commit
  up_write demo/SKILL.md "$base_doc"$'\nRead `~/.cursor/rules/pstack-models.mdc`.\n'
  up_commit leak >/dev/null
  sync
  expect_code 1 &&
    expect_out "cursor demo/SKILL.md:9:"
}

test_check_flags_cursor_terms_and_markers_but_not_precursor() {
  ours_write demo/SKILL.md $'# Demo\n\nA precursor to the change.\n\nUse `subagent_type: generalPurpose`.\n'
  ours_write other/SKILL.md $'<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> upstream\n'
  ours_write fine/SKILL.md $'# Fine\n\nSpawn subagents with the Agent tool.\n'
  lock "demo abc" "other abc" "fine abc"
  sync check
  expect_code 1 &&
    expect_out "cursor demo/SKILL.md:5:" &&
    expect_out "marker other/SKILL.md:1:" &&
    ! grep -q "demo/SKILL.md:3:" "$work/out" &&
    ! grep -q "fine/" "$work/out" ||
    { sed 's/^/    /' "$work/out"; return 1; }
}

test_add_imports_a_new_skill_at_upstream_head() {
  up_write fresh/SKILL.md $'# Fresh\n\nSee `.cursor/skills/x/`.\n'
  local head
  head="$(up_commit fresh)"
  lock "# tracked pstack skills"
  ours_commit
  sync add fresh
  expect_code 0 &&
    expect_file fresh/SKILL.md $'# Fresh\n\nSee `.agents/skills/x/`.' &&
    expect_out "added fresh" &&
    expect_lock "fresh $head"
}

for t in $(declare -F | awk '{print $3}' | grep '^test_'); do
  setup
  if "$t"; then
    echo "ok   $t"
  else
    echo "FAIL $t"
    failures=$((failures + 1))
  fi
  teardown
done

[ "$failures" -eq 0 ] || { echo "$failures failed"; exit 1; }
echo "all passed"
