#!/usr/bin/env python3
"""
Session-Learning 脚本
=====================

从开发会话中提取经验，沉淀到Skills目录。

使用方法:
    python scripts/session_learn.py extract --file ./session_log.md
    python scripts/session_learn.py apply --skill backtesting --pattern "effective_qty"
    python scripts/session_learn.py list

原理:
    每次解决问题的经验都沉淀下来，AI不会在多轮对话后越跑越偏。
    这实现了TRAE Loop的Session-Learning机制，让AI拥有"长期记忆"。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


SKILLS_DIR = Path(__file__).parent.parent / "skills"
SKILLS_META_DIR = SKILLS_DIR / "_meta"
EXPERIENCES_DIR = SKILLS_DIR / "_experiences"


@dataclass
class Experience:
    """经验条目"""

    id: str
    skill: str
    pattern: str
    description: str
    bug_example: str
    fix_example: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: list[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        return f"""- id: {self.id}
  skill: {self.skill}
  pattern: {self.pattern}
  description: {self.description}
  bug_example: |
    {self._indent(self.bug_example, 4)}
  fix_example: |
    {self._indent(self.fix_example, 4)}
  created_at: {self.created_at}
  tags: {self.tags}
"""

    @staticmethod
    def _indent(text: str, spaces: int) -> str:
        indent = " " * spaces
        return "\n".join(f"{indent}{line}" for line in text.split("\n"))


class ExperienceStore:
    """经验存储管理"""

    def __init__(self) -> None:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)

    def add_experience(self, exp: Experience) -> None:
        """添加新经验"""
        experiences_file = EXPERIENCES_DIR / f"{exp.skill}.yaml"
        experiences_file.parent.mkdir(parents=True, exist_ok=True)

        with open(experiences_file, "a", encoding="utf-8") as f:
            f.write(exp.to_yaml())

        self._update_skill_meta(exp.skill, exp.pattern)

    def _update_skill_meta(self, skill: str, pattern: str) -> None:
        """更新Skills元数据，标记新经验"""
        skill_meta_dir = SKILLS_DIR / skill / "resources"
        skill_meta_dir.mkdir(parents=True, exist_ok=True)
        patterns_file = skill_meta_dir / "_patterns.yaml"

        patterns = []
        if patterns_file.exists():
            with open(patterns_file, encoding="utf-8") as f:
                content = f.read()
                if content.strip():
                    patterns = self._parse_patterns(content)

        patterns.append(
            {
                "pattern": pattern,
                "learned_at": datetime.now().isoformat(),
            }
        )

        with open(patterns_file, "w", encoding="utf-8") as f:
            f.write("patterns:\n")
            for p in patterns:
                f.write(f"  - pattern: {p['pattern']}\n")
                f.write(f"    learned_at: {p['learned_at']}\n")

    @staticmethod
    def _parse_patterns(content: str) -> list[dict[str, str]]:
        patterns = []
        current_pattern: dict[str, str] = {}
        for line in content.split("\n"):
            if line.startswith("  - pattern:"):
                if current_pattern:
                    patterns.append(current_pattern)
                current_pattern = {"pattern": line.split(":", 1)[1].strip()}
            elif line.startswith("    learned_at:"):
                current_pattern["learned_at"] = line.split(":", 1)[1].strip()
        if current_pattern:
            patterns.append(current_pattern)
        return patterns

    def list_experiences(self, skill: str | None = None) -> list[Experience]:
        """列出所有经验"""
        experiences = []
        if skill:
            files = [EXPERIENCES_DIR / f"{skill}.yaml"]
        else:
            files = EXPERIENCES_DIR.glob("*.yaml")

        for exp_file in files:
            if exp_file.exists():
                experiences.extend(self._parse_experiences(exp_file))

        return experiences

    @staticmethod
    def _parse_experiences(file_path: Path) -> list[Experience]:
        experiences = []
        content = file_path.read_text(encoding="utf-8")

        current_exp: dict[str, Any] = {}
        current_field = None
        current_value = []

        for line in content.split("\n"):
            if line.startswith("- id:"):
                if current_exp and current_field:
                    current_exp[current_field] = "\n".join(current_value).strip()
                    experiences.append(Experience(**current_exp))
                current_exp = {"id": line.split(":", 1)[1].strip()}
                current_field = None
                current_value = []
            elif line.startswith("  skill:"):
                current_exp["skill"] = line.split(":", 1)[1].strip()
            elif line.startswith("  pattern:"):
                current_exp["pattern"] = line.split(":", 1)[1].strip()
            elif line.startswith("  description:"):
                current_field = "description"
                current_value = [line.split(":", 1)[1].strip()]
            elif line.startswith("  bug_example:"):
                current_field = "bug_example"
                current_value = []
            elif line.startswith("  fix_example:"):
                if current_field:
                    current_exp[current_field] = "\n".join(current_value).strip()
                current_field = "fix_example"
                current_value = [line.split(":", 1)[1].strip()]
            elif line.startswith("  created_at:"):
                current_exp["created_at"] = line.split(":", 1)[1].strip()
            elif line.startswith("  tags:"):
                current_exp["tags"] = []
            elif current_field and line.startswith("    "):
                current_value.append(line.strip())
            elif current_field and not line.startswith("    ") and line.strip():
                if current_exp.get(current_field) is None:
                    current_exp[current_field] = (
                        "\n".join(current_value).strip() if current_value else ""
                    )
                current_field = None
                current_value = []

        if current_exp and current_field:
            current_exp[current_field] = "\n".join(current_value).strip()
        if current_exp and "id" in current_exp:
            experiences.append(Experience(**current_exp))

        return experiences


def cmd_extract(args: argparse.Namespace) -> int:
    """从会话日志中提取经验"""
    session_file = Path(args.file)
    if not session_file.exists():
        print(f"Error: File not found: {session_file}")
        return 1

    content = session_file.read_text(encoding="utf-8")

    store = ExperienceStore()

    if args.auto:
        experiences = _auto_extract(content, args.skill)
        for exp in experiences:
            store.add_experience(exp)
            print(f"Extracted: {exp.pattern}")
        print(f"\nTotal: {len(experiences)} experiences added")
    else:
        print("Use --auto to extract patterns automatically")
        print("Or provide --pattern, --description, etc. for manual extraction")

    return 0


def _auto_extract(content: str, skill: str | None = None) -> list[Experience]:
    """自动从会话内容中提取经验"""
    experiences = []

    bug_markers = ["错误示例", "Bug:", "Wrong:", "❌", "bug pattern"]
    fix_markers = ["正确示例", "Fix:", "Correct:", "✅", "fix pattern", "应该"]

    lines = content.split("\n")
    current_bug = []
    current_fix = []
    capture_mode = None

    for line in lines:
        if any(m in line for m in bug_markers):
            capture_mode = "bug"
            current_bug = []
        elif any(m in line for m in fix_markers):
            capture_mode = "fix"
            current_fix = []
        elif capture_mode == "bug":
            current_bug.append(line)
        elif capture_mode == "fix":
            current_fix.append(line)

    if current_bug and current_fix:
        experiences.append(
            Experience(
                id=f"exp-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                skill=skill or "general",
                pattern=_extract_pattern_name(current_bug),
                description="从会话中自动提取的编码模式",
                bug_example="\n".join(current_bug),
                fix_example="\n".join(current_fix),
                tags=["auto-extracted"],
            )
        )

    return experiences


def _extract_pattern_name(bug_content: list[str]) -> str:
    """从Bug内容中提取模式名称"""
    for line in bug_content:
        if "effective_qty" in line.lower():
            return "effective_qty_none_check"
        if "rejection" in line.lower():
            return "rejection_reason_null"
        if "kill" in line.lower() and "switch" in line.lower():
            return "killswitch_check"
    return "general_pattern"


def cmd_apply(args: argparse.Namespace) -> int:
    """应用经验到当前任务"""
    store = ExperienceStore()
    experiences = store.list_experiences(args.skill)

    matched = [e for e in experiences if args.pattern.lower() in e.pattern.lower()]

    if not matched:
        print(f"No experiences found matching: {args.pattern}")
        return 1

    print(f"Found {len(matched)} matching experiences:\n")
    for exp in matched:
        print(f"## {exp.pattern}")
        print(f"**Skill**: {exp.skill}")
        print(f"**Description**: {exp.description}")
        print(f"\n### Bug Example")
        print(exp.bug_example)
        print(f"\n### Fix Example")
        print(exp.fix_example)
        print("---")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """列出所有经验"""
    store = ExperienceStore()
    experiences = store.list_experiences(args.skill)

    if not experiences:
        print("No experiences found")
        return 0

    print(f"Total: {len(experiences)} experiences\n")
    for exp in experiences:
        print(f"[{exp.skill}] {exp.pattern} - {exp.description[:50]}...")

    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """初始化Skills目录结构"""
    SKILLS_META_DIR.mkdir(parents=True, exist_ok=True)
    EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)

    index_file = SKILLS_META_DIR / "index.yaml"
    if not index_file.exists():
        index_file.write_text(
            """# Skills Metadata Index
skills: []
"""
        )

    readme_file = EXPERIENCES_DIR / "README.md"
    if not readme_file.exists():
        readme_file.write_text(
            """# Experiences

自动积累的编码经验和模式。

使用方法:
    python scripts/session_learn.py list
    python scripts/session_learn.py apply --skill backtesting --pattern "effective_qty"
"""
        )

    print("Skills directory initialized")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session-Learning: 从开发会话中提取经验，沉淀到Skills目录"
    )
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract", help="从会话日志提取经验")
    extract_parser.add_argument("--file", required=True, help="会话日志文件路径")
    extract_parser.add_argument("--auto", action="store_true", help="自动提取模式")
    extract_parser.add_argument("--skill", help="关联的Skill名称")

    apply_parser = subparsers.add_parser("apply", help="应用经验到当前任务")
    apply_parser.add_argument("--skill", help="Skill名称")
    apply_parser.add_argument("--pattern", required=True, help="模式关键词")

    list_parser = subparsers.add_parser("list", help="列出所有经验")
    list_parser.add_argument("--skill", help="按Skill过滤")

    init_parser = subparsers.add_parser("init", help="初始化Skills目录")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "extract": cmd_extract,
        "apply": cmd_apply,
        "list": cmd_list,
        "init": cmd_init,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
