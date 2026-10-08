#!/usr/bin/env bash
set -euo pipefail

count() {
  git diff --cached --numstat -- "$@" | awk '$1 != "-" { a += $1; d += $2 } END { printf "%d %d\n", a, d }'
}

read -r src_a src_d < <(count src/)
read -r tests_a tests_d < <(count tests/)
read -r md_a md_d < <(count '*.md')

printf '%s %s %s\n' src/ "$src_a" "$src_d" tests/ "$tests_a" "$tests_d" '*.md' "$md_a" "$md_d" | awk '
{ label[NR] = $1; add[NR] = $2; del[NR] = $3; ta += $2; td += $3 }
END {
  n = NR
  label[n + 1] = "Total"; add[n + 1] = ta; del[n + 1] = td
  for (i = 1; i <= n + 1; i++) {
    if (length("+" add[i]) > wa) wa = length("+" add[i])
    if (length("-" del[i]) > wd) wd = length("-" del[i])
  }
  print "LOC:"
  for (i = 1; i <= n + 1; i++) {
    net = add[i] - del[i]
    printf "  %-7s %*s / %*s = net %s%d\n", label[i], wa, "+" add[i], wd, "-" del[i], (net >= 0 ? "+" : ""), net
  }
}'
