#!/usr/bin/env bash
# PreToolUse hook: block PR creation and pushes to PR branches if the HEAD
# commit of the TARGETED repo lacks the Pre-Submit: pass trailer.
#
# Copy this file to your repo's .claude/hooks/ directory.
# Add to .claude/settings.json:
#   "hooks": {
#     "PreToolUse": [{
#       "matcher": "Bash",
#       "hooks": [{"type": "command", "command": ".claude/hooks/check-presubmit-trailer.sh"}]
#     }]
#   }
#
# Two properties this hook must hold, both of which earlier versions broke:
#
#   1. It judges the repo the COMMAND acts on, not the session's cwd. A session
#      rooted in one repo routinely runs `cd <other-repo> && gh pr create`, and
#      checking the session's HEAD there is wrong twice over: it blocks the
#      other repo on an unrelated commit, and it would clear a PR whose real
#      HEAD has no trailer.
#
#   2. It only enforces on repos that OPTED IN by installing this hook. Without
#      that, a session in a gated repo imposes the regime on every repo it
#      touches -- including ones with no /pre-submit skill to satisfy it.

CMD=$(jq -r '.tool_input.command' 2>/dev/null)

# Match against the command with quoted spans blanked out. This hook greps a
# raw command string, so without this, ANY command that merely quotes the text
# it looks for trips the gate -- committing a message that mentions
# `gh pr create`, echoing it, writing it into a file. That is not theoretical:
# it fired on the very commit that introduced this fix.
#
# Not a shell parser and not trying to be (heredocs, nested quoting and
# `$(...)` still fall through). It removes the overwhelmingly common case --
# text inside a -m message or a PR body -- and nothing else. The authoritative
# gate belongs in CI, where the PR's real head commit can be inspected; this
# hook is fast local feedback.
CMD_BARE=$(printf '%s' "$CMD" | sed -e 's/"[^"]*"/ /g' -e "s/'[^']*'/ /g")

# Resolve the directory the command will actually run in. `cd <dir> && ...` and
# `git -C <dir> ...` both retarget it away from the session cwd.
resolve_target_dir() {
    local cmd="$1" dir=""
    if [[ "$cmd" =~ git[[:space:]]+-C[[:space:]]+([^[:space:]\;\&\|]+) ]]; then
        dir="${BASH_REMATCH[1]}"
    elif [[ "$cmd" =~ ^[[:space:]]*cd[[:space:]]+([^[:space:]\;\&\|]+) ]]; then
        dir="${BASH_REMATCH[1]}"
    fi
    # Strip one layer of surrounding quotes if present.
    dir="${dir%\"}"; dir="${dir#\"}"
    dir="${dir%\'}"; dir="${dir#\'}"
    if [ -n "$dir" ] && [ -d "$dir" ]; then
        printf '%s' "$dir"
        return 0
    fi
    printf '.'
}

deny() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}' "$1"
    exit 0
}

# Allows (exit 0) when the target repo has not opted in, or when the trailer is
# present on its HEAD. Denies with $1 otherwise.
check_trailer() {
    local reason="$1" target root
    target=$(resolve_target_dir "$CMD_BARE")

    root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null) || exit 0
    [ -n "$root" ] || exit 0

    # Opt-in gate: enforce only where this hook is installed.
    [ -f "$root/.claude/hooks/check-presubmit-trailer.sh" ] || exit 0

    if git -C "$target" log -1 --format=%B 2>/dev/null \
        | grep -qE '^Pre-Submit:[[:space:]]*pass[[:space:]]*$'; then
        exit 0
    fi
    deny "$reason"
}

# Check 1: PR creation or readying.
# NOTE: `gh pr create -R owner/repo` can target a repo unrelated to the local
# checkout; the local HEAD is then the wrong thing to judge. That form is rare
# here and is deliberately left alone rather than guessed at.
if echo "$CMD_BARE" | grep -qE '(^|[[:space:];&|])gh[[:space:]]+pr[[:space:]]+(create|ready)'; then
    check_trailer "HEAD commit missing Pre-Submit: pass trailer. Run /pre-submit first."
fi

# Check 2: git push to non-main branches.
# Matched UNANCHORED. The previous `^git push` meant any prefix defeated it --
# `cd <repo> && git push` and `git -C <repo> push` both sailed through, so the
# guard silently protected nothing while appearing to be on.
if echo "$CMD_BARE" | grep -qE '(^|[[:space:];&|])git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?push'; then
    # Skip pushes that delete branches or push tags.
    if echo "$CMD_BARE" | grep -qE '\-\-delete|:refs/tags/'; then
        exit 0
    fi

    TARGET=$(resolve_target_dir "$CMD_BARE")
    BRANCH=$(git -C "$TARGET" rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
        exit 0
    fi

    check_trailer "HEAD commit missing Pre-Submit: pass trailer. Add the trailer before pushing to a PR branch. Run /pre-submit or create an empty commit with the trailer."
fi

# Not a checked command -- allow.
exit 0
