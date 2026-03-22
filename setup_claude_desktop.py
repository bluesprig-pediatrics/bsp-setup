#!/usr/bin/env python3
"""Configure Claude Desktop for the BlueSprig environment.

Detects whether the user has GitHub CLI installed and configures
credentials accordingly:
  - Developer (gh CLI): uses `gh auth token` for GitHub credentials
  - Non-GitHub user: prompts for a shared PAT

Usage:
    python3 setup_claude_desktop.py
    python3 setup_claude_desktop.py --dry-run
    python3 setup_claude_desktop.py --brand-only
"""

import argparse
import getpass
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORG = "bluesprig-pediatrics"
MCP_DIR = Path.home() / ".claude" / "mcp-servers"

REPOS = {
    "bsp-shared": {"private": True},
    "bsp-analytics": {"private": True},
    "bsp-brand": {"private": True},  # internal — non-GitHub users need PAT
}

# ---------------------------------------------------------------------------
# Platform helpers (preserved from bsp-analytics setup script)
# ---------------------------------------------------------------------------


def _find_msix_config_dir() -> "Path | None":
    """Find the MSIX virtualized config dir for Claude Desktop on Windows.

    Claude Desktop installed via MSIX (the standard Windows installer) uses
    filesystem virtualization. The app reads config from a path like:
        %LOCALAPPDATA%/Packages/Claude_<id>/LocalCache/Roaming/Claude/
    but the "Edit Config" button and docs point to %APPDATA%/Claude/.

    If we write to the wrong path, MCP servers silently fail to load.
    See: https://github.com/anthropics/claude-code/issues/26073
    """
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if not local_appdata:
        return None
    packages_dir = Path(local_appdata) / "Packages"
    if not packages_dir.exists():
        return None
    # Find Claude's MSIX package folder (name starts with "Claude_")
    for entry in packages_dir.iterdir():
        if entry.name.startswith("Claude_") and entry.is_dir():
            msix_config = entry / "LocalCache" / "Roaming" / "Claude"
            return msix_config
    return None


def get_config_paths() -> "list[Path]":
    """Return Claude Desktop config file path(s) for this OS.

    On Windows with MSIX installs, we write to BOTH the standard
    %APPDATA% path and the MSIX virtualized path to ensure the app
    finds the config regardless of install method.

    Returns a list of paths to write (usually 1, up to 2 on Windows MSIX).
    """
    system = platform.system()
    if system == "Darwin":
        return [
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        ]
    elif system == "Windows":
        paths = []
        # Standard path (works for non-MSIX installs)
        appdata = os.environ.get("APPDATA", "")
        standard = Path(appdata) / "Claude" / "claude_desktop_config.json"
        paths.append(standard)
        # MSIX virtualized path (required for Store/MSIX installs)
        msix_dir = _find_msix_config_dir()
        if msix_dir is not None:
            msix_path = msix_dir / "claude_desktop_config.json"
            if msix_path != standard:
                paths.append(msix_path)
        return paths
    elif system == "Linux":
        return [
            Path.home()
            / ".config"
            / "Claude"
            / "claude_desktop_config.json"
        ]
    else:
        print(f"Unsupported platform: {system}")
        sys.exit(1)


def _find_uv() -> str:
    """Return the full path to uv, or 'uv' if it's on PATH."""
    uv_path = shutil.which("uv")
    if uv_path:
        return uv_path
    # Common Windows install locations
    if platform.system() == "Windows":
        candidate = Path.home() / ".local" / "bin" / "uv.exe"
        if candidate.exists():
            return str(candidate)
        candidate = Path.home() / ".cargo" / "bin" / "uv.exe"
        if candidate.exists():
            return str(candidate)
    return "uv"


def _write_config(config_path: Path, servers: dict) -> None:
    """Write MCP server config to a single config file path."""
    # Read existing config or start fresh
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        backup = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup)
        print(f"  Backed up existing config to {backup.name}")
    else:
        existing = {}
        config_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Creating new config at {config_path}")

    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"].update(servers)

    config_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# New functions for multi-user setup
# ---------------------------------------------------------------------------


def detect_user_type() -> str:
    """Detect whether the user has GitHub CLI authenticated.

    Returns "developer" if `gh auth status` succeeds, "non_github" otherwise.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return "developer"
    except FileNotFoundError:
        pass
    return "non_github"


def prompt_tokens(user_type: str, brand_only: bool) -> dict:
    """Prompt for required credentials based on user type and mode.

    Returns a dict with keys like "motherduck_token", "github_pat".
    Uses getpass for masked input — token values are never printed.
    """
    tokens = {}

    if not brand_only:
        print()
        print("  MotherDuck token required for data warehouse access.")
        print("  Get your read-only token from the data engineering team.")
        print("  (Input is masked — characters won't appear as you type.)")
        token = getpass.getpass(
            "  Paste your MOTHERDUCK_TOKEN (or press Enter to skip): "
        ).strip()
        if token:
            tokens["motherduck_token"] = token
        else:
            print("  Skipped — MotherDuck server will not be configured.")

    if user_type == "non_github":
        print()
        print("  GitHub CLI not detected — a shared PAT is required to")
        print("  clone private repos and enable GitHub integrations.")
        print("  Get the shared fine-grained PAT from your team lead.")
        print("  (Input is masked — characters won't appear as you type.)")
        token = getpass.getpass(
            "  Paste your GITHUB_TOKEN (or press Enter to skip): "
        ).strip()
        if token:
            tokens["github_pat"] = token
        else:
            print("  Skipped — private repo cloning may fail.")

    return tokens


def clone_repos(
    user_type: str, tokens: dict, brand_only: bool, dry_run: bool
) -> None:
    """Clone MCP server repos into ~/.claude/mcp-servers/.

    For non-GitHub users, injects PAT into clone URLs for authentication.
    Pulls latest if already cloned. Runs `uv sync` after cloning.
    """
    repos_to_clone = ["bsp-brand"] if brand_only else list(REPOS.keys())
    github_pat = tokens.get("github_pat", "")

    MCP_DIR.mkdir(parents=True, exist_ok=True)

    for repo_name in repos_to_clone:
        repo_dir = MCP_DIR / repo_name

        # Build clone URL
        if user_type == "non_github" and github_pat:
            clone_url = (
                f"https://x-access-token:{github_pat}@github.com"
                f"/{ORG}/{repo_name}.git"
            )
        else:
            clone_url = f"https://github.com/{ORG}/{repo_name}.git"

        if repo_dir.exists():
            print(f"  {repo_name}: already cloned, pulling latest...")
            if dry_run:
                print(f"    [dry-run] would run: git -C {repo_dir} pull")
                continue
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "pull"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"    WARNING: git pull failed: {result.stderr.strip()}")
        else:
            # Log the clone URL without the token
            safe_url = f"https://github.com/{ORG}/{repo_name}.git"
            print(f"  {repo_name}: cloning from {safe_url}...")
            if dry_run:
                print(f"    [dry-run] would clone to {repo_dir}")
                continue
            result = subprocess.run(
                ["git", "clone", clone_url, str(repo_dir)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"    ERROR: clone failed: {result.stderr.strip()}")
                continue

        # Run uv sync in the cloned repo
        uv_cmd = _find_uv()
        print(f"  {repo_name}: running uv sync...")
        if dry_run:
            print(f"    [dry-run] would run: {uv_cmd} sync in {repo_dir}")
            continue
        result = subprocess.run(
            [uv_cmd, "sync"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # uv sync may fail for repos without pyproject.toml — that's OK
            if "no `pyproject.toml`" in result.stderr.lower() or (
                "error" in result.stderr.lower()
                and "pyproject" in result.stderr.lower()
            ):
                print(f"    {repo_name}: no pyproject.toml, skipping uv sync")
            else:
                print(
                    f"    WARNING: uv sync failed: {result.stderr.strip()}"
                )


def write_env_files(tokens: dict, dry_run: bool) -> None:
    """Write .env files for MCP servers that need them.

    - bsp-analytics/.env: MOTHERDUCK_TOKEN and GITHUB_TOKEN
    - bsp-shared/.env: GITHUB_TOKEN (for submit_issue proxy)
    """
    md_token = tokens.get("motherduck_token", "")
    github_pat = tokens.get("github_pat", "")

    # bsp-analytics .env — needs both tokens
    analytics_dir = MCP_DIR / "bsp-analytics"
    if analytics_dir.exists():
        env_path = analytics_dir / ".env"
        lines = [
            "# BlueSprig Analytics — Environment Variables",
            "# IMPORTANT: Never commit .env — it contains credentials",
            "",
        ]
        if md_token:
            lines.append("# Read-only MotherDuck token")
            lines.append(f"MOTHERDUCK_TOKEN={md_token}")
            lines.append("")
        if github_pat:
            lines.append("# Shared fine-grained PAT for insight submission")
            lines.append(f"GITHUB_TOKEN={github_pat}")
            lines.append("")

        if md_token or github_pat:
            if dry_run:
                print(f"  [dry-run] would write {env_path}")
            else:
                env_path.write_text("\n".join(lines), encoding="utf-8")
                print(f"  Wrote {env_path}")

    # bsp-shared .env — needs GITHUB_TOKEN for submit_issue
    shared_dir = MCP_DIR / "bsp-shared"
    if shared_dir.exists() and github_pat:
        env_path = shared_dir / ".env"
        lines = [
            "# BlueSprig Shared — Environment Variables",
            "# IMPORTANT: Never commit .env — it contains credentials",
            "",
            "# Shared fine-grained PAT for GitHub integrations",
            f"GITHUB_TOKEN={github_pat}",
            "",
        ]
        if dry_run:
            print(f"  [dry-run] would write {env_path}")
        else:
            env_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"  Wrote {env_path}")


def build_server_configs(
    user_type: str, uv_cmd: str, tokens: dict, brand_only: bool
) -> dict:
    """Build the mcpServers config dict for Claude Desktop.

    Developer path: uses `gh auth token` wrapper via /bin/sh for GITHUB_TOKEN.
    Non-GitHub path: reads token from .env files.
    """
    servers = {}
    md_token = tokens.get("motherduck_token", "")
    github_pat = tokens.get("github_pat", "")

    # Use forward slashes for cross-platform JSON paths
    mcp_path = str(MCP_DIR).replace("\\", "/")

    # --- bsp-brand (always configured) ---
    brand_dir = f"{mcp_path}/bsp-brand/mcp"
    servers["bsp-brand"] = {
        "command": uv_cmd,
        "args": [
            "--directory",
            brand_dir,
            "run",
            "python",
            "bsp_brand_server.py",
        ],
    }

    if brand_only:
        return servers

    # --- bsp-shared ---
    shared_dir = f"{mcp_path}/bsp-shared"
    if user_type == "developer":
        # Developer path: gh auth token wrapper via shell
        gh_wrapper = (
            f"export GITHUB_TOKEN=$(gh auth token) && "
            f"exec {shlex.quote(uv_cmd)} --directory "
            f"{shlex.quote(shared_dir)} run python -m bsp_shared"
        )
        servers["bsp-shared"] = {
            "command": "/bin/sh",
            "args": ["-c", gh_wrapper],
        }
    else:
        # Non-GitHub path: token from .env
        shared_config = {
            "command": uv_cmd,
            "args": [
                "--directory",
                shared_dir,
                "run",
                "python",
                "-m",
                "bsp_shared",
            ],
        }
        if github_pat:
            shared_config["env"] = {"GITHUB_TOKEN": github_pat}
        servers["bsp-shared"] = shared_config

    # --- bsp-analytics ---
    analytics_dir = f"{mcp_path}/bsp-analytics"
    analytics_config = {
        "command": uv_cmd,
        "args": [
            "--directory",
            analytics_dir,
            "run",
            "python",
            "-m",
            "bsp_server",
        ],
    }
    analytics_env = {}
    if md_token:
        analytics_env["MOTHERDUCK_TOKEN"] = md_token
    if user_type == "developer":
        # Developer path: gh auth token wrapper for analytics too
        env_parts = ""
        if md_token:
            env_parts = f"export MOTHERDUCK_TOKEN={shlex.quote(md_token)} && "
        gh_wrapper = (
            f"{env_parts}"
            f"export GITHUB_TOKEN=$(gh auth token) && "
            f"exec {shlex.quote(uv_cmd)} --directory "
            f"{shlex.quote(analytics_dir)} run python -m bsp_server"
        )
        servers["bsp-analytics"] = {
            "command": "/bin/sh",
            "args": ["-c", gh_wrapper],
        }
    else:
        if github_pat:
            analytics_env["GITHUB_TOKEN"] = github_pat
        if analytics_env:
            analytics_config["env"] = analytics_env
        servers["bsp-analytics"] = analytics_config

    # --- MotherDuck ---
    if md_token:
        servers["motherduck"] = {
            "command": uv_cmd,
            "args": [
                "tool",
                "run",
                "mcp-server-motherduck",
                "--db-path",
                "md:bluesprig-production-lakehouse",
            ],
            "env": {"motherduck_token": md_token},
        }

    return servers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Set up Claude Desktop for the BlueSprig environment."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be configured without making changes.",
    )
    parser.add_argument(
        "--brand-only",
        action="store_true",
        help="Only configure brand tools (no analytics tokens needed).",
    )
    args = parser.parse_args()

    config_paths = get_config_paths()
    uv_cmd = _find_uv()

    print()
    print("BlueSprig — Claude Desktop Setup")
    print("=" * 40)
    print(f"  uv command:   {uv_cmd}")
    print(f"  MCP dir:      {MCP_DIR}")
    for p in config_paths:
        print(f"  Config file:  {p}")
    if len(config_paths) > 1:
        print()
        print("  NOTE: Detected MSIX (Windows Store) install of Claude Desktop.")
        print("  Writing config to both standard and MSIX-virtualized paths")
        print("  to ensure the app finds it regardless of install method.")

    if args.dry_run:
        print()
        print("  *** DRY RUN — no files will be written ***")

    # Verify prerequisites
    if not shutil.which("git"):
        print()
        print("  ERROR: git not found on PATH.")
        print("  Install git: https://git-scm.com/downloads")
        sys.exit(1)

    if not shutil.which("uv") and uv_cmd == "uv":
        print()
        print("  ERROR: uv not found on PATH.")
        print("  Install: brew install uv (macOS) or winget install astral-sh.uv (Windows)")
        sys.exit(1)

    # --- Step 1: Detect user type ---
    print()
    print("Step 1: Detecting environment")
    print("-" * 40)
    user_type = detect_user_type()
    if user_type == "developer":
        print("  GitHub CLI detected — using gh auth token for credentials.")
    else:
        print("  GitHub CLI not detected — will prompt for shared PAT.")

    # --- Step 2: Collect tokens ---
    print()
    print("Step 2: Credentials")
    print("-" * 40)
    if args.dry_run:
        tokens = {}
        if not args.brand_only:
            print("  [dry-run] would prompt for MOTHERDUCK_TOKEN")
        if user_type == "non_github":
            print("  [dry-run] would prompt for GITHUB_TOKEN")
    else:
        tokens = prompt_tokens(user_type, args.brand_only)

    # --- Step 3: Clone repos ---
    print()
    print("Step 3: Clone MCP server repos")
    print("-" * 40)
    clone_repos(user_type, tokens, args.brand_only, args.dry_run)

    # --- Step 4: Write .env files ---
    print()
    print("Step 4: Environment files")
    print("-" * 40)
    if tokens:
        write_env_files(tokens, args.dry_run)
    else:
        print("  No tokens to write.")

    # --- Step 5: Configure Claude Desktop ---
    print()
    print("Step 5: Claude Desktop config")
    print("-" * 40)

    servers = build_server_configs(uv_cmd=uv_cmd, user_type=user_type, tokens=tokens, brand_only=args.brand_only)

    if args.dry_run:
        print()
        print("  [dry-run] Would configure these MCP servers:")
        # Print config but redact any token values
        safe_servers = json.loads(json.dumps(servers))
        for name, cfg in safe_servers.items():
            if "env" in cfg:
                for key in cfg["env"]:
                    cfg["env"][key] = "***"
            # Redact tokens from shell command args
            if "args" in cfg:
                cfg["args"] = [
                    a if "gh auth token" not in a and "$(" not in a
                    else "[shell wrapper with gh auth token]"
                    for a in cfg["args"]
                ]
        print(json.dumps({"mcpServers": safe_servers}, indent=2))
    else:
        for config_path in config_paths:
            print(f"\n  Writing to: {config_path}")
            _write_config(config_path, servers)

    # --- Done ---
    print()
    print("=" * 40)
    configured = list(servers.keys())
    if args.dry_run:
        print("Dry run complete — no changes were made.")
    else:
        print("Setup complete!")
    print()
    print(f"  MCP servers: {', '.join(configured)}")
    print()
    if not args.dry_run:
        if platform.system() == "Windows":
            print("Next steps:")
            print("  1. Fully close Claude Desktop (right-click tray icon > Quit)")
            print("  2. Reopen Claude Desktop")
            print("  3. Start a new conversation")
        else:
            print("Next steps:")
            print("  1. Restart Claude Desktop (Cmd+Q, reopen)")
            print("  2. Start a new conversation")
    print()


if __name__ == "__main__":
    main()
