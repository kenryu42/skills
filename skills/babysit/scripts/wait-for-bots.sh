#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: wait-for-bots.sh [PR] [--once] [--timeout MINUTES] [--interval SECONDS]" >&2
  exit 1
}

pr=""
once=0
timeout=40
interval=60
while [ $# -gt 0 ]; do
  case "$1" in
    --once) once=1 ;;
    --timeout) [ $# -ge 2 ] || usage; timeout="$2"; shift ;;
    --interval) [ $# -ge 2 ] || usage; interval="$2"; shift ;;
    -*) usage ;;
    *) pr="$1" ;;
  esac
  shift
done

repo="$(git remote get-url origin | sed -E 's#(\.git)?/*$##; s#.*[:/]([^/]+/[^/]+)$#\1#')"
if [ -z "$pr" ]; then
  branch="$(git branch --show-current)"
  pr="$(gh api "repos/$repo/pulls?head=${repo%%/*}:$branch&state=open" --jq '.[0].number // empty')"
  [ -n "$pr" ] || { echo "no open PR for $branch in $repo" >&2; exit 1; }
fi

program='
def bots: [
  {key: "coderabbit", check: "CodeRabbit", login: "coderabbitai"},
  {key: "greptile", check: "Greptile Review", login: "greptile-apps"},
  {key: "pullfrog", check: "pullfrog", login: "pullfrog"}
];

.pr as $pr
| ([.runs[] | {kind: "run", name, status, conclusion}] + [.statuses[] | {kind: "status", name: .context, state, description}]) as $head
| (.seen + [$head[].name]) as $seen

| [$head[] | select([.name] | inside([bots[].check]) | not)] as $ci
| ([$ci[] | select(.kind == "run" and .status != "completed" or .state == "pending") | .name] | unique) as $running
| ([$ci[] | select(.conclusion == "failure" or .conclusion == "timed_out" or .conclusion == "cancelled"
      or .conclusion == "action_required" or .conclusion == "startup_failure" or .state == "failure" or .state == "error") | .name] | unique) as $failing
| (if $running != [] then "running (\($running | join(", ")))"
   elif $failing != [] then "failing (\($failing | join(", ")))"
   elif $ci == [] then "none"
   else "passing" end) as $ci_line

| [bots[] | . as $bot | ([$head[] | select(.name == $bot.check)] | last) as $c | {key, status: (
    if any($seen[]; . == $bot.check) | not then "not installed"
    elif $c == null then (if $bot.key == "greptile" then "not triggered" else "waiting" end)
    elif $c.kind == "status" then
      if $c.state == "pending" then "running"
      elif $c.state == "success" and $c.description == "Review completed" then "done"
      elif $c.state == "success" then "paused (\($c.description))"
      else "error (\($c.description))" end
    elif $c.status != "completed" then "running"
    else "done (\($c.conclusion))" end)}] as $bots

| ([.reviews[] | select((.login | sub("\\[bot\\]$"; "")) as $l | any(bots[]; .login == $l)) | .at] | min) as $first_review
| (if $first_review == null then 0
   else [.commits[] | select(. > $first_review)] | length end) as $fixes

| ((if $running != [] then ["ci"] else [] end)
   + [$bots[] | select(.status == "waiting" or .status == "running" or .status == "not triggered") | .key]) as $waiting

| "PR #\($ENV.PR) head \($pr.sha[0:8]) (\(if $pr.merged then "MERGED" else $pr.state | ascii_upcase end), merge \($pr.mergeable_state | ascii_upcase))",
  "ci: \($ci_line)",
  ($bots[] | "\(.key): \(.status)"),
  "fix commits since first bot review: \($fixes)",
  (if $pr.state != "open" or $waiting == [] then "settled" else "waiting on: \($waiting | join(", "))" end)
'

checks() {
  local runs statuses
  runs="$(gh api "repos/$repo/commits/$1/check-runs?per_page=100" --jq '[.check_runs[] | {name, status, conclusion}]')" || return 1
  statuses="$(gh api "repos/$repo/commits/$1/status?per_page=100" --jq '[.statuses[] | {context, state, description}]')" || return 1
  jq -n --argjson runs "$runs" --argjson statuses "$statuses" '{$runs, $statuses}'
}

recent_check_names() {
  local shas sha all=""
  shas="$(gh api "repos/$repo/pulls?state=all&per_page=10" --jq '.[].head.sha')" || return 1
  for sha in $shas; do
    all+="$(checks "$sha")" || return 1
  done
  jq -s '[.[] | .runs[].name, .statuses[].context] | unique' <<<"$all"
}

snapshot() {
  local pull head commits reviews
  pull="$(gh api "repos/$repo/pulls/$pr" --jq '{state, merged, mergeable_state, sha: .head.sha}')" || return 1
  head="$(checks "$(jq -r .sha <<<"$pull")")" || return 1
  commits="$(gh api --paginate "repos/$repo/pulls/$pr/commits?per_page=100" --jq '[.[].commit.author.date]')" || return 1
  reviews="$(gh api --paginate "repos/$repo/pulls/$pr/reviews?per_page=100" --jq '[.[] | {login: .user.login, at: .submitted_at}]')" || return 1
  jq -n --argjson pr "$pull" --argjson head "$head" --argjson seen "$seen" \
    --argjson commits "$(jq -s add <<<"$commits")" --argjson reviews "$(jq -s add <<<"$reviews")" \
    '$head + {$pr, $seen, $commits, $reviews}'
}

seen=""
out=""
start=$SECONDS
while :; do
  if { [ -n "$seen" ] || seen="$(recent_check_names)"; } && json="$(snapshot)"; then
    out="$(PR="$pr" jq -r "$program" <<<"$json")"
    last="${out##*$'\n'}"
    if [ "$last" = settled ]; then
      echo "$out"
      exit 0
    fi
    if [ "$once" = 1 ]; then
      echo "$out"
      exit 2
    fi
  elif [ "$once" = 1 ]; then
    exit 1
  else
    echo "gh failed; retrying" >&2
  fi
  if [ $((SECONDS - start)) -ge $((timeout * 60)) ]; then
    if [ -z "$out" ]; then
      echo "gh kept failing until the $timeout min timeout" >&2
      exit 1
    fi
    echo "${out%$'\n'*}"
    echo "timed out after $timeout min ${out##*$'\n'}"
    exit 2
  fi
  sleep "$interval"
done
