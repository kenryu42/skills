#!/usr/bin/env bash
set -uo pipefail

script="$(cd "$(dirname "$0")" && pwd)/wait-for-bots.sh"
failures=0
work=""

setup() {
  work="$(mktemp -d)"
  mkdir -p "$work/bin"
  git init -q -b feature "$work/repo"
  git -C "$work/repo" remote add origin https://github.com/kenryu42/demo.git
  cat >"$work/bin/gh" <<'EOF'
#!/usr/bin/env bash
[ "$1" = api ] || { echo "unexpected gh $*" >&2; exit 1; }
shift
filter=.
endpoint=""
while [ $# -gt 0 ]; do
  case "$1" in
    --jq) filter="$2"; shift ;;
    --paginate) ;;
    *) endpoint="$1" ;;
  esac
  shift
done
if [ "$endpoint" = repos/kenryu42/demo/pulls/42 ]; then
  echo $(($(cat "$STUB_DIR/calls" 2>/dev/null || echo 0) + 1)) >"$STUB_DIR/calls"
fi
n="$(cat "$STUB_DIR/calls" 2>/dev/null || echo 1)"
if [ -f "$STUB_DIR/snap$n.fail" ]; then
  echo "HTTP 502: Bad Gateway" >&2
  exit 1
fi
case "$endpoint" in
  "repos/kenryu42/demo/pulls?head=kenryu42:feature&state=open") query='[{number: 42}]' ;;
  "repos/kenryu42/demo/pulls?state=all&per_page=10") query='[{head: {sha: "recent"}}]' ;;
  "repos/kenryu42/demo/commits/recent/check-runs?per_page=100") query='{check_runs: [.installed[] | select(. != "CodeRabbit") | {name: .}]}' ;;
  "repos/kenryu42/demo/commits/recent/status?per_page=100") query='{statuses: [.installed[] | select(. == "CodeRabbit") | {context: .}]}' ;;
  repos/kenryu42/demo/pulls/42) query=.pull ;;
  "repos/kenryu42/demo/commits/abcdef1234567890/check-runs?per_page=100") query='{check_runs}' ;;
  "repos/kenryu42/demo/commits/abcdef1234567890/status?per_page=100") query='{statuses}' ;;
  "repos/kenryu42/demo/pulls/42/commits?per_page=100") query=.commits ;;
  "repos/kenryu42/demo/pulls/42/reviews?per_page=100") query=.reviews ;;
  *) echo "unexpected gh api $endpoint" >&2; exit 1 ;;
esac
jq -c "$query" "$STUB_DIR/snap$n.json" | jq -rc "$filter"
EOF
  chmod +x "$work/bin/gh"
}

teardown() {
  rm -rf "$work"
}

cr() { jq -nc --arg s "$1" --arg d "$2" '{kind:"status",context:"CodeRabbit",state:($s | ascii_downcase),description:$d}'; }
run() { jq -nc --arg n "$1" --arg s "$2" --arg c "${3:-}" '{kind:"run",name:$n,status:($s | ascii_downcase),conclusion:(if $c == "" then null else $c | ascii_downcase end)}'; }
ci_pass() { run full-check COMPLETED SUCCESS; }
all_bots='["CodeRabbit","Greptile Review","pullfrog"]'

# snapshot <n> <head checks json> [installed names json] [commits json] [reviews json] [state]
snapshot() {
  jq -n \
    --argjson head "$2" \
    --argjson installed "${3:-$all_bots}" \
    --argjson commits "${4:-[]}" \
    --argjson reviews "${5:-[]}" \
    --arg state "${6:-OPEN}" \
    '{
      pull: {state: (if $state == "OPEN" then "open" else "closed" end), merged: ($state == "MERGED"),
        mergeable_state: "clean", head: {sha: "abcdef1234567890"}},
      check_runs: [$head[] | select(.kind == "run") | del(.kind)],
      statuses: [$head[] | select(.kind == "status") | del(.kind)],
      installed: $installed,
      commits: [$commits[] | {commit: {author: {date: .authored}, committer: {date: .committed}}}],
      reviews: [$reviews[] | {user: {login: .login}, submitted_at: .at}]
    }' >"$work/snap$1.json"
}

contexts() { jq -sc '.' <<<"$*"; }

expect() {
  local name="$1" want_code="$2" want_out="$3" want_err="${4:-}"
  shift 4
  local out err code
  out="$(cd "$work/repo" && STUB_DIR="$work" PATH="$work/bin:$PATH" "$script" "$@" 2>"$work/stderr")"
  code=$?
  err="$(cat "$work/stderr")"
  if [ "$code" != "$want_code" ] || [ "$out" != "$want_out" ] || [ "$err" != "$want_err" ]; then
    failures=$((failures + 1))
    printf 'FAIL %s\n  exit: want %s got %s\n  stdout want:\n%s\n  stdout got:\n%s\n  stderr want:\n%s\n  stderr got:\n%s\n' \
      "$name" "$want_code" "$code" "$want_out" "$out" "$want_err" "$err"
  else
    printf 'ok   %s\n' "$name"
  fi
}

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')" "$(run 'Greptile Review' COMPLETED SUCCESS)" "$(run pullfrog COMPLETED SUCCESS)")"
expect "settled when CI and every bot finished on the head" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: done (success)
pullfrog: done (success)
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review paused')" "$(run 'Greptile Review' COMPLETED SUCCESS)" "$(run pullfrog COMPLETED SUCCESS)")"
expect "a paused CodeRabbit is settled but not reported as done" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: paused (Review paused)
greptile: done (success)
pullfrog: done (success)
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')")" '["CodeRabbit"]'
expect "bots absent from the repo's recent PRs are not waited on" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(run full-check IN_PROGRESS)" "$(cr PENDING 'Review in progress')" "$(run pullfrog IN_PROGRESS)")"
expect "a single snapshot names what is still pending" 2 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: running (full-check)
coderabbit: running
greptile: not triggered
pullfrog: running
fix commits since first bot review: 0
waiting on: ci, coderabbit, greptile, pullfrog" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(run full-check COMPLETED FAILURE)" "$(run lint COMPLETED SKIPPED)" "$(cr SUCCESS 'Review completed')" "$(run 'Greptile Review' COMPLETED SUCCESS)" "$(run pullfrog COMPLETED SUCCESS)")"
expect "failing CI is named and still settles" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: failing (full-check)
coderabbit: done
greptile: done (success)
pullfrog: done (success)
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
reviews='[{"login":"kenryu42","at":"2026-10-07T08:00:00Z"},{"login":"pullfrog[bot]","at":"2026-10-07T08:53:39Z"},{"login":"greptile-apps[bot]","at":"2026-10-07T08:55:01Z"}]'

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')" "$(run 'Greptile Review' COMPLETED SUCCESS)" "$(run pullfrog COMPLETED SUCCESS)")" "$all_bots" \
  '[{"authored":"2026-10-07T08:41:41Z","committed":"2026-10-07T12:00:00Z","suites":0},
    {"authored":"2026-10-07T09:21:59Z","committed":"2026-10-07T12:00:00Z","suites":0},
    {"authored":"2026-10-07T09:41:59Z","committed":"2026-10-07T12:00:00Z","suites":0},
    {"authored":"2026-10-07T10:17:15Z","committed":"2026-10-07T12:00:00Z","suites":13}]' \
  "$reviews"
expect "fix commits still count after a restack rewrote the branch" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: done (success)
pullfrog: done (success)
fix commits since first bot review: 3
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')" "$(run 'Greptile Review' COMPLETED SUCCESS)" "$(run pullfrog COMPLETED SUCCESS)")" "$all_bots" \
  '[{"authored":"2026-10-07T08:41:41Z","committed":"2026-10-07T12:00:00Z","suites":13}]' \
  "$reviews"
expect "a restack alone adds no fix commits" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: done (success)
pullfrog: done (success)
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(run full-check IN_PROGRESS)")" '[]' '[]' '[]' MERGED
expect "a merged PR settles immediately" 0 "PR #42 head abcdef12 (MERGED, merge CLEAN)
ci: running (full-check)
coderabbit: not installed
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
settled" "" 42 --once
teardown

setup
snapshot 1 "$(contexts "$(run full-check IN_PROGRESS)" "$(cr PENDING 'Review in progress')")" '["CodeRabbit"]'
touch "$work/snap2.fail"
snapshot 3 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')")" '["CodeRabbit"]'
expect "polls through a transient gh failure until settled" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
settled" "HTTP 502: Bad Gateway
gh failed; retrying" 42 --interval 0
teardown

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr PENDING 'Review in progress')")" '["CodeRabbit"]'
expect "gives up at the timeout and names what never reported" 2 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: running
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
timed out after 0 min waiting on: coderabbit" "" 42 --timeout 0 --interval 0
teardown

setup
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')")" '["CodeRabbit"]'
expect "resolves the current branch's PR when none is given" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
settled" "" --once
teardown

setup
git -C "$work/repo" remote set-url origin http://local_proxy@127.0.0.1:41271/git/kenryu42/demo
snapshot 1 "$(contexts "$(ci_pass)" "$(cr SUCCESS 'Review completed')")" '["CodeRabbit"]'
expect "reads the repository from a proxied git remote" 0 "PR #42 head abcdef12 (OPEN, merge CLEAN)
ci: passing
coderabbit: done
greptile: not installed
pullfrog: not installed
fix commits since first bot review: 0
settled" "" 42 --once
teardown

[ "$failures" -eq 0 ] || { echo "$failures failed"; exit 1; }
echo "all passed"
