#!/usr/bin/env python3
"""
经验查询脚本
============
根据关键词查询经验文档，帮助agent在特定场景下获取相关经验。

Usage:
    python scripts/get_experience.py <keyword>
    
Examples:
    python scripts/get_experience.py powershell
    python scripts/get_experience.py async
    python scripts/get_experience.py 幂等性
"""
import sys
import re
from pathlib import Path

EXPERIENCE_DOC = Path(__file__).parent.parent / "docs" / "EXPERIENCE_SUMMARY.md"

def search_experience(keyword: str) -> str:
    """根据关键词搜索经验文档"""
    if not EXPERIENCE_DOC.exists():
        return f"经验文档不存在: {EXPERIENCE_DOC}"
    
    content = EXPERIENCE_DOC.read_text(encoding="utf-8")
    
    # 搜索包含关键词的章节
    lines = content.split("\n")
    results = []
    current_section = None
    current_section_lines = []
    in_section = False
    
    for line in lines:
        # 检测章节标题
        if line.startswith("## ") or line.startswith("### "):
            if in_section and current_section_lines:
                results.append("\n".join(current_section_lines))
            current_section = line
            current_section_lines = [line]
            in_section = keyword.lower() in line.lower()
        elif in_section:
            current_section_lines.append(line)
        elif keyword.lower() in line.lower():
            # 关键词在正文但不在标题中
            if not in_section:
                current_section = "相关段落"
                current_section_lines = [line]
                in_section = True
    
    if current_section_lines and in_section:
        results.append("\n".join(current_section_lines))
    
    if not results:
        # 宽松匹配 - 只搜索关键词
        matches = []
        for i, line in enumerate(lines):
            if keyword.lower() in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                context = lines[start:end]
                matches.append(f"...\n{' '.join(context)}\n...")
        
        if matches:
            return f"在文档中找到 {len(matches)} 处提及 '{keyword}':\n\n" + "\n---\n".join(matches[:5])
        return f"未找到关于 '{keyword}' 的经验"
    
    return "\n---\n".join(results[:3])  # 最多返回3个章节

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    keyword = sys.argv[1]
    result = search_experience(keyword)
    print(result)

if __name__ == "__main__":
    main()