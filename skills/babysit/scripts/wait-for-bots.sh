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

repo="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
[ -n "$pr" ] || pr="$(gh pr view --json number -q .number)"

query='
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      state
      mergeStateStatus
      headRefOid
      head: commits(last: 1) {
        nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
          __typename
          ... on CheckRun { name status conclusion }
          ... on StatusContext { context state description }
        } } } } }
      }
      commits(last: 100) {
        nodes { commit { authoredDate } }
      }
      reviews(last: 100) { nodes { author { login } submittedAt } }
    }
    pullRequests(last: 10) {
      nodes { commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
        __typename
        ... on CheckRun { name }
        ... on StatusContext { context }
      } } } } } } }
    }
  }
}'

program='
def ctx_name: .name // .context;
def bots: [
  {key: "coderabbit", check: "CodeRabbit", login: "coderabbitai"},
  {key: "greptile", check: "Greptile Review", login: "greptile-apps"},
  {key: "pullfrog", check: "pullfrog", login: "pullfrog"}
];

.data.repository as $repo
| $repo.pullRequest as $pr
| ($pr.head.nodes[0].commit.statusCheckRollup.contexts.nodes // []) as $head
| ([$repo.pullRequests.nodes[].commits.nodes[].commit.statusCheckRollup.contexts.nodes[]? | ctx_name] + [$head[] | ctx_name]) as $seen

| [$head[] | select([ctx_name] | inside([bots[].check]) | not)] as $ci
| ([$ci[] | select(.status != null and .status != "COMPLETED" or .state == "PENDING" or .state == "EXPECTED") | ctx_name] | unique) as $running
| ([$ci[] | select(.conclusion == "FAILURE" or .conclusion == "TIMED_OUT" or .conclusion == "CANCELLED"
      or .conclusion == "ACTION_REQUIRED" or .conclusion == "STARTUP_FAILURE" or .state == "FAILURE" or .state == "ERROR") | ctx_name] | unique) as $failing
| (if $running != [] then "running (\($running | join(", ")))"
   elif $failing != [] then "failing (\($failing | join(", ")))"
   elif $ci == [] then "none"
   else "passing" end) as $ci_line

| [bots[] | . as $bot | ([$head[] | select(ctx_name == $bot.check)] | last) as $c | {key, status: (
    if any($seen[]; . == $bot.check) | not then "not installed"
    elif $c == null then (if $bot.key == "greptile" then "not triggered" else "waiting" end)
    elif $c.__typename == "StatusContext" then
      if $c.state == "PENDING" or $c.state == "EXPECTED" then "running"
      elif $c.state == "SUCCESS" and $c.description == "Review completed" then "done"
      elif $c.state == "SUCCESS" then "paused (\($c.description))"
      else "error (\($c.description))" end
    elif $c.status != "COMPLETED" then "running"
    else "done (\($c.conclusion | ascii_downcase))" end)}] as $bots

| ([$pr.reviews.nodes[] | select(.author.login as $l | any(bots[]; .login == $l)) | .submittedAt] | min) as $first_review
| (if $first_review == null then 0
   else [$pr.commits.nodes[].commit | select(.authoredDate > $first_review)] | length end) as $fixes

| ((if $running != [] then ["ci"] else [] end)
   + [$bots[] | select(.status == "waiting" or .status == "running" or .status == "not triggered") | .key]) as $waiting

| "PR #\($ENV.PR) head \($pr.headRefOid[0:8]) (\($pr.state), merge \($pr.mergeStateStatus))",
  "ci: \($ci_line)",
  ($bots[] | "\(.key): \(.status)"),
  "fix commits since first bot review: \($fixes)",
  (if $pr.state != "OPEN" or $waiting == [] then "settled" else "waiting on: \($waiting | join(", "))" end)
'

snapshot() {
  gh api graphql -F owner="${repo%%/*}" -F name="${repo#*/}" -F number="$pr" -f query="$query"
}

out=""
start=$SECONDS
while :; do
  if json="$(snapshot)"; then
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
