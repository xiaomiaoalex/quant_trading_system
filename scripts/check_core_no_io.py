#!/usr/bin/env python3
"""
Architecture Compliance Check: Core Plane No IO

Verifies that no IO operations (network, file, DB, env vars) exist in core/ plane.

Usage:
    python scripts/check_core_no_io.py [path]
"""

import ast
import os
import sys
from pathlib import Path


# IO-related imports and functions that are forbidden in Core plane
FORBIDDEN_IO_PATTERNS = {
    # Network libraries
    "requests",
    "aiohttp",
    "httpx",
    "urllib",
    "urllib3",
    "curl",
    # File operations
    "open",
    "Path",
    # Environment
    "getenv",
    "environ",
    # Asyncpg (PostgreSQL)
    "asyncpg",
    # Sleep (time-based side effects)
    "sleep",
    # Time-related that could cause IO
    "time.time",
    "time.sleep",
}


class CoreIOVisitor(ast.NodeVisitor):
    """AST visitor that detects IO patterns in Core plane code."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.violations = []
        self._in_import = False

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.name.split(".")[0]
            if name in FORBIDDEN_IO_PATTERNS:
                self.violations.append(
                    f"  Line {node.lineno}: Import '{name}' (IO operation)"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module = node.module.split(".")[0]
            if module in FORBIDDEN_IO_PATTERNS:
                self.violations.append(
                    f"  Line {node.lineno}: From '{node.module}' import (IO operation)"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check for open()
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            self.violations.append(
                f"  Line {node.lineno}: open() call (file IO)"
            )
        elif isinstance(node.func, ast.Attribute):
            # Check for time.sleep, os.getenv etc
            attr_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id == "time" and attr_name == "sleep":
                    self.violations.append(
                        f"  Line {node.lineno}: time.sleep() (blocking IO)"
                    )
                elif node.func.value.id == "os" and attr_name in ("getenv", "environ"):
                    self.violations.append(
                        f"  Line {node.lineno}: os.{attr_name}() (environment access)"
                    )
        self.generic_visit(node)


def check_file(filepath: str) -> list[str]:
    """Check a single Python file for IO violations."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
        visitor = CoreIOVisitor(filepath)
        visitor.visit(tree)
        return visitor.violations
    except SyntaxError as e:
        return [f"  Syntax error: {e}"]
    except Exception as e:
        return [f"  Error parsing: {e}"]


def check_directory(core_dir: str) -> dict[str, list[str]]:
    """Check all Python files in a directory."""
    violations = {}
    core_path = Path(core_dir)
    
    if not core_path.exists():
        print(f"Warning: {core_dir} does not exist")
        return violations
    
    for py_file in core_path.rglob("*.py"):
        # Skip __pycache__ and test files
        if "__pycache__" in str(py_file) or py_file.name.startswith("test_"):
            continue
        
        file_violations = check_file(str(py_file))
        if file_violations:
            violations[str(py_file)] = file_violations
    
    return violations


def main():
    # Default to checking trader/core/
    path = sys.argv[1] if len(sys.argv) > 1 else "trader/core"
    
    print(f"Checking architecture compliance: Core plane no IO")
    print(f"Target: {path}")
    print("-" * 60)
    
    violations = check_directory(path)
    
    if violations:
        print("❌ ARCHITECTURE VIOLATION: IO operations found in Core plane")
        print()
        for filepath, file_violations in violations.items():
            print(f"{filepath}:")
            for v in file_violations:
                print(v)
        print()
        print("Core plane violations must be fixed before merge.")
        sys.exit(1)
    else:
        print("✅ Core plane is clean: no IO operations detected")
        sys.exit(0)


if __name__ == "__main__":
    main()
