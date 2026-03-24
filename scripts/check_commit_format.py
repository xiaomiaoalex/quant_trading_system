#!/usr/bin/env python3
"""
Commit Message Linter

Validates that commit messages follow the Conventional Commits format with Task ID.

Usage:
    python scripts/check_commit_format.py < commit_message_file
    python scripts/check_commit_format.py "feat(task-1.4): implementation"
"""

import re
import sys


# Conventional Commits types
TYPES = ["feat", "fix", "refactor", "test", "docs", "chore", "perf", "ci", "build", "revert"]

# Commit message pattern: type(task-id): description
# Examples:
#   feat(task-1.4): implement time window risk
#   fix(task-1.2): reconcile edge case
#   chore: update dependencies
PATTERN = re.compile(
    r"^(" + "|".join(TYPES) + r")(?:\([^)]+\))?: .+"
)


def validate_commit_message(message: str) -> tuple[bool, str]:
    """Validate a single commit message."""
    message = message.strip()
    
    if not message:
        return False, "Empty commit message"
    
    lines = message.split("\n")
    first_line = lines[0]
    
    # Check if it starts with a type
    if not any(first_line.startswith(t) for t in TYPES):
        return False, f"Message must start with one of: {', '.join(TYPES)}"
    
    # Check format
    if not PATTERN.match(first_line):
        return False, f"Format must be: type(task-id): description\nExample: feat(task-1.4): implement feature"
    
    # Check for task ID in parentheses (required for feat/fix/refactor/test/docs)
    type_match = re.match(r"^(" + "|".join(TYPES) + r")", first_line)
    if type_match:
        commit_type = type_match.group(1)
        if commit_type in ["feat", "fix", "refactor", "test", "docs"]:
            if "(task-" not in first_line:
                return False, f"{commit_type} commits must include task ID: (task-X.Y)"
    
    return True, "Valid"


def main():
    if len(sys.argv) > 1:
        # Direct argument mode
        message = " ".join(sys.argv[1:])
    else:
        # Stdin mode (for git hooks)
        message = sys.stdin.read().strip()
    
    valid, reason = validate_commit_message(message)
    
    if valid:
        print(f"✅ {reason}")
        sys.exit(0)
    else:
        print(f"❌ {reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
