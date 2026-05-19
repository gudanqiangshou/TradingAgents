#!/usr/bin/env bash
# One-command sync of this fork with upstream TauricResearch/TradingAgents.
#
# Flow: fetch upstream -> merge -> run safety tests -> push fork -> restart
# backend. Stops hard (no push, no restart) on a dirty tree, a merge
# conflict, or a test failure, and tells you exactly what to do.
#
# Why the test gate: web/state_tracker.py is a port of cli/main.py's
# agent-status detection. If upstream rewrites the CLI streaming logic the
# port can silently drift, so we never deploy an upstream bump that fails
# the web suite.
set -euo pipefail

REPO="/Users/a1/TradingAgents"
PY="$REPO/.venv/bin/python"
SERVICE="com.tradingagents.web"

cd "$REPO"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

say "1/6 Checking working tree is clean"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short
  die "You have uncommitted changes to tracked files. Commit or stash them
     first, then re-run. (Untracked files like reports/ are fine.)"
fi

say "2/6 Fetching upstream"
git fetch upstream

behind=$(git rev-list --count main..upstream/main)
if [ "$behind" -eq 0 ]; then
  printf '\n\033[1;32m✓ Already up to date with upstream. Nothing to do.\033[0m\n'
  exit 0
fi
echo "upstream is $behind commit(s) ahead — merging"

say "3/6 Merging upstream/main"
if ! git merge --no-edit upstream/main; then
  git merge --abort 2>/dev/null || true
  die "Merge conflict (likely in cli/main.py or tradingagents/__init__.py
     where you have customizations). Run 'git merge upstream/main' manually,
     resolve the conflicts, then re-run this script — it will skip the
     already-done merge and continue from the tests."
fi

say "4/6 Running safety tests (web port + your CLI customization)"
if ! "$PY" -m pytest tests/web/ tests/test_cli_backend_url_override.py -q; then
  die "Tests FAILED against the new upstream. The merge is in your working
     tree but NOT pushed and the live site was NOT restarted. Investigate:
     upstream may have changed cli/main.py streaming logic that web/
     state_tracker.py mirrors. Fix, commit, then re-run from step 5
     (git push origin main && launchctl kickstart ...)."
fi

say "5/6 Pushing merged main to your fork (origin)"
git push origin main

say "6/6 Restarting backend so the live site runs the new code"
launchctl kickstart -k "gui/$(id -u)/$SERVICE"
sleep 4
code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' https://ta.nibajie.cc/ || echo "000")
if [ "$code" = "200" ]; then
  printf '\n\033[1;32m✓ Updated, tested, pushed, live. https://ta.nibajie.cc is healthy.\033[0m\n'
else
  die "Code updated & pushed, but https://ta.nibajie.cc returned HTTP $code.
     Check: tail ~/.tradingagents/logs/web-stderr.log"
fi
