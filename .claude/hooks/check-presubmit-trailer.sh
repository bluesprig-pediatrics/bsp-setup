#!/usr/bin/env bash
# PreToolUse hook: block PR creation and pushes to PR branches if HEAD commit
# lacks the Pre-Submit: pass trailer.
# Copy this file to your repo's .claude/hooks/ directory.
# Add to .claude/settings.json:
#   "hooks": {
#     "PreToolUse": [{
#       "matcher": "Bash",
#       "hooks": [{"type": "command", "command": ".claude/hooks/check-presubmit-trailer.sh"}]
#     }]
#   }

CMD=$(jq -r '.tool_input.command' 2>/dev/null)

# Check 1: PR creation or readying
if echo "$CMD" | grep -qE "gh pr (create|ready)"; then
  if git log -1 --format=%B 2>/dev/null | grep -qE '^Pre-Submit:\s*pass\s*$'; then
    exit 0
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"HEAD commit missing Pre-Submit: pass trailer. Run /pre-submit first."}}'
  exit 0
fi

# Check 2: git push to non-main branches
if echo "$CMD" | grep -qE "^git push"; then
  # Skip pushes that delete branches or push tags
  if echo "$CMD" | grep -qE "\-\-delete|:refs/tags/"; then
    exit 0
  fi

  # Skip pushes to main/master
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    exit 0
  fi

  if git log -1 --format=%B 2>/dev/null | grep -qE '^Pre-Submit:\s*pass\s*$'; then
    exit 0
  fi

  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"HEAD commit missing Pre-Submit: pass trailer. Add the trailer before pushing to a PR branch. Run /pre-submit or create an empty commit with the trailer."}}'
  exit 0
fi

# Not a checked command — allow
exit 0
