# bsp-setup

Minimal public repo containing the BlueSprig environment setup script.

## Rules

- **PUBLIC REPO (RULE #0)**: This repo is public. Every change must be checked for:
  - No PHI/PII in code, comments, or examples
  - No proprietary business logic
  - No internal infrastructure URLs (GitHub org repo URLs are OK)
  - No tokens, credentials, or secrets
  - No MCP server code, Claude workflows, or tool implementations
  - Commit messages must not reference internal business context
- **Stdlib only** — this script must not require any pip/uv dependencies.
  Use only Python standard library modules.
- **Cross-platform** — must work on macOS and Windows. No GNU-only flags.
