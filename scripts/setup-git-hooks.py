#!/usr/bin/env python3
"""
Install Git Hooks

This script installs Git hooks from .githooks/ directory into .git/hooks/.

Usage:
    python scripts/setup-git-hooks.py
"""

import os
import stat
import subprocess
import sys
from pathlib import Path


def make_executable(path: Path):
    """Make a file executable."""
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_hook(hook_name: str) -> bool:
    """Install a single hook."""
    source = Path(f".githooks/{hook_name}")
    target = Path(f".git/hooks/{hook_name}")
    
    if not source.exists():
        print(f"⚠️  Source hook not found: {source}")
        return False
    
    # Create .git/hooks directory if it doesn't exist
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if hook already exists and is not our marker
    if target.exists():
        with open(target, 'r') as f:
            first_line = f.readline()
        if "# Installed by setup-git-hooks.py" not in first_line:
            print(f"⚠️  Hook already exists and was not installed by us: {target}")
            print(f"    To overwrite, manually remove {target} and run this script again")
            return False
    
    # Create wrapper script
    wrapper = f"""\#!/bin/sh
# Installed by setup-git-hooks.py
# Source: .githooks/{hook_name}

# Find the script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Run the hook
python3 "$PROJECT_ROOT/.githooks/{hook_name}" "$@"
"""
    
    with open(target, 'w') as f:
        f.write(wrapper)
    
    make_executable(target)
    print(f"✅ Installed: {hook_name}")
    return True


def main():
    print("Git Hooks Installer")
    print("=" * 40)
    
    # Check if in git repository
    if not Path(".git").exists():
        print("❌ Not a git repository")
        sys.exit(1)
    
    # Create .githooks directory if it doesn't exist
    githooks_dir = Path(".githooks")
    githooks_dir.mkdir(exist_ok=True)
    
    # Install hooks
    hooks_to_install = ["commit-msg"]
    
    for hook in hooks_to_install:
        install_hook(hook)
    
    print("\n✅ Git hooks installation complete")
    print("\nNote: If you're using Windows (WSL) or Git Bash,")
    print("the hooks should work automatically.")
    print("For Windows CMD/PowerShell, you may need to configure")
    print("core.hooksPath to point to .githooks/")


if __name__ == "__main__":
    main()
