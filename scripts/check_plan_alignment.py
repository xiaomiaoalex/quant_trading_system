#!/usr/bin/env python3
"""
Project Plan Alignment Checker

Compares git log against PLAN.md to verify task completion and detect drift.

Usage:
    python scripts/check_plan_alignment.py [--since="7 days ago"]
"""

import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def run_git_log(since: str = "30 days ago") -> list[dict]:
    """Get commit history with task IDs."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%H %s", "--all"],
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace"
        )
    except subprocess.CalledProcessError:
        print("❌ Error: Not a git repository or git command failed")
        sys.exit(1)
    
    commits = []
    stdout = result.stdout or ""
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            sha, message = parts
            # Extract task ID from commit message
            # Match patterns like: Task 1.4, Task1.4, task_1.4, task-1.4
            task_match = re.search(r"[Tt]ask[_-]?\s*(\d+\.\d+)", message)
            task_id = f"Task{task_match.group(1)}" if task_match else None
            commits.append({
                "sha": sha[:8],
                "message": message,
                "task_id": task_id
            })
    return commits


def parse_plan_tasks() -> dict:
    """Parse PLAN.md to extract all tasks and their status."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        print("⚠️  PLAN.md not found")
        return {}
    
    content = plan_path.read_text(encoding="utf-8")
    tasks = {}
    
    # Match Task patterns: Task 1.1, Task 1.2, Task10.3-P, etc.
    task_pattern = re.compile(r"### Task (\d+)\.(\d+)([A-Z]*)", re.MULTILINE)
    
    for match in task_pattern.finditer(content):
        major = match.group(1)
        minor = match.group(2)
        suffix = match.group(3)
        task_id = f"Task{major}.{minor}{suffix}" if suffix else f"Task{major}.{minor}"
        
        # Check if task is marked as completed in PLAN.md
        # Look for [x] or ✓ or "已完成" after the task heading
        task_start = match.end()
        next_task = task_pattern.search(content, task_start)
        task_section = content[task_start:next_task.start() if next_task else len(content)]
        
        # Check completion status
        is_completed = "[x]" in task_section[:200] or "✓" in task_section[:200] or "已完成" in task_section[:200]
        
        tasks[task_id] = {
            "completed_in_plan": is_completed,
            "section": task_section[:100].strip()
        }
    
    return tasks


def main():
    since = "30 days ago"
    if len(sys.argv) > 1 and sys.argv[1] == "--since":
        if len(sys.argv) > 2:
            since = sys.argv[2]
    
    print("=" * 60)
    print("Project Plan Alignment Report")
    print(f"Period: Last {since}")
    print(f"Generated: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Get commits
    commits = run_git_log(since)
    
    # Get tasks from PLAN.md
    tasks = parse_plan_tasks()
    
    # Aggregate commits by task
    task_commits = {}
    no_task_commits = []
    
    for commit in commits:
        if commit["task_id"]:
            if commit["task_id"] not in task_commits:
                task_commits[commit["task_id"]] = []
            task_commits[commit["task_id"]].append(commit)
        else:
            no_task_commits.append(commit)
    
    # Report
    print(f"\n📊 Commits Analyzed: {len(commits)}")
    print(f"📋 Tasks Found in PLAN.md: {len(tasks)}")
    
    print("\n" + "=" * 60)
    print("Task Progress")
    print("=" * 60)
    
    for task_id in sorted(tasks.keys(), key=lambda x: (int(x[4:].split('.')[0]), float(x[4:].split('.')[1].rstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')))):
        commits_for_task = task_commits.get(task_id, [])
        status_icon = "✅" if tasks[task_id]["completed_in_plan"] else "⏳"
        print(f"\n{status_icon} {task_id}: {len(commits_for_task)} commits")
        if commits_for_task:
            for c in commits_for_task[:3]:
                print(f"    - {c['sha']} {c['message'][:50]}...")
        elif not tasks[task_id]["completed_in_plan"]:
            print(f"    ⚠️  No commits found but marked as incomplete")
    
    if no_task_commits:
        print("\n" + "=" * 60)
        print(f"⚠️  Commits Without Task ID ({len(no_task_commits)})")
        print("=" * 60)
        for c in no_task_commits[:5]:
            print(f"    - {c['sha']} {c['message'][:60]}")
        if len(no_task_commits) > 5:
            print(f"    ... and {len(no_task_commits) - 5} more")
    
    # Check for drift
    print("\n" + "=" * 60)
    print("Drift Detection")
    print("=" * 60)
    
    drift_detected = False
    for task_id, task_info in tasks.items():
        commits_for_task = task_commits.get(task_id, [])
        if commits_for_task and task_info["completed_in_plan"]:
            print(f"⚠️  {task_id} has commits but marked COMPLETED in PLAN.md")
            drift_detected = True
    
    if not drift_detected:
        print("✅ No drift detected")
    
    print("\n" + "=" * 60)
    print("Recommendations")
    print("=" * 60)
    print("1. If tasks are complete, update PLAN.md with [x] checkmarks")
    print("2. Ensure all commits follow: type(task-X.Y): description format")
    print("3. Run: python scripts/check_core_no_io.py before merge")


if __name__ == "__main__":
    main()
