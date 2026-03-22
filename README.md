# bsp-setup

Setup script for BlueSprig Claude Desktop environment.

## Usage

```bash
git clone https://github.com/bluesprig-pediatrics/bsp-setup.git
cd bsp-setup
python3 setup_claude_desktop.py
```

The script will:
1. Detect your environment (GitHub CLI or shared token)
2. Prompt for required credentials (masked input)
3. Clone and configure MCP servers
4. Set up Claude Desktop

## Options

- `--dry-run` — show what would be configured without making changes
- `--brand-only` — only set up brand tools (no analytics tokens needed)

## Requirements

- Python 3.11+
- git
- uv (`brew install uv` on macOS, `winget install astral-sh.uv` on Windows)
