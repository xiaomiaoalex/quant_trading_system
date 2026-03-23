#!/usr/bin/env python3
"""
自动化项目状态更新脚本

功能：
1. 运行关键测试文件
2. 解析 pytest 输出，提取每个文件的通过/失败数量
3. 读取 PROJECT_STATUS.md，用新的测试结果替换旧数据
4. 更新 "最后更新时间" 为当前时间
"""

import subprocess
import re
import os
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
STATUS_FILE = PROJECT_ROOT / "PROJECT_STATUS.md"

# 需要运行的测试文件列表
TEST_FILES = [
    "trader/tests/test_feature_store.py",
    "trader/tests/test_reconciler.py",
    "trader/tests/test_depth_checker.py",
    "trader/tests/test_time_window_policy.py",
    "trader/tests/test_binance_connector.py",
    "trader/tests/test_binance_private_stream.py",
    "trader/tests/test_binance_degraded_cascade.py",
    "trader/tests/test_deterministic_layer.py",
    "trader/tests/test_hard_properties.py",
]

# 模块名称映射
MODULE_MAP = {
    "test_feature_store.py": "Feature Store",
    "test_reconciler.py": "Reconciler",
    "test_depth_checker.py": "深度检查",
    "test_time_window_policy.py": "时间窗口",
    "test_binance_connector.py": "Binance Connector",
    "test_binance_private_stream.py": "Binance Private Stream",
    "test_binance_degraded_cascade.py": "Degraded Cascade",
    "test_deterministic_layer.py": "Deterministic Layer",
    "test_hard_properties.py": "Hard Properties",
}


def run_pytest(test_file: str) -> dict:
    """
    运行单个测试文件并解析结果

    Returns:
        dict: {"passed": int, "failed": int, "total": int, "error": str or None}
    """
    test_path = PROJECT_ROOT / test_file
    if not test_path.exists():
        return {"passed": 0, "failed": 0, "total": 0, "error": f"文件不存在: {test_file}"}

    print(f"  运行测试: {test_file}")

    try:
        result = subprocess.run(
            [
                str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"),
                "-m", "pytest",
                str(test_path),
                "-v", "--tb=short",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=300,  # 5分钟超时
        )

        output = result.stdout + result.stderr

        # 解析 pytest 输出
        # 匹配格式: "X passed", "X failed", "X passed, Y failed"
        passed = 0
        failed = 0

        # 查找总结行，常见格式:
        # - "10 passed in 1.23s"
        # - "5 passed, 1 failed in 1.23s"
        # - "10 passed, 1 failed, 2 errors in 1.23s"

        summary_pattern = r"(\d+)\s+passed"
        passed_matches = re.findall(summary_pattern, output)
        if passed_matches:
            passed = int(passed_matches[-1])  # 取最后一个匹配（总结行）

        failed_pattern = r"(\d+)\s+failed"
        failed_matches = re.findall(failed_pattern, output)
        if failed_matches:
            failed = int(failed_matches[-1])

        # 如果没有找到 passed 尝试其他解析方式
        if passed == 0 and failed == 0:
            # 尝试解析 "X passed Y failed" 格式
            combined_pattern = r"(\d+)\s+passed[,;\s]+(\d+)\s+failed"
            combined_match = re.search(combined_pattern, output)
            if combined_match:
                passed = int(combined_match.group(1))
                failed = int(combined_match.group(2))

        total = passed + failed

        if result.returncode != 0 and total == 0:
            # 测试运行失败但没有解析到结果
            # 检查是否是完全通过或完全失败
            if "passed" in output.lower():
                # 可能全是 passed
                pass
            elif "failed" in output.lower():
                # 尝试从错误信息中提取
                error_match = re.search(r"(\d+)\s+failed", output)
                if error_match:
                    failed = int(error_match.group(1))

        return {
            "passed": passed,
            "failed": failed,
            "total": total,
            "error": None
        }

    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "total": 0, "error": "测试超时"}
    except Exception as e:
        return {"passed": 0, "failed": 0, "total": 0, "error": str(e)}


def parse_test_results() -> dict:
    """
    运行所有测试文件并收集结果
    """
    results = {}

    for test_file in TEST_FILES:
        filename = os.path.basename(test_file)
        module_name = MODULE_MAP.get(filename, filename)

        print(f"\n[{len(results) + 1}/{len(TEST_FILES)}] {module_name}")
        result = run_pytest(test_file)
        results[filename] = {
            "module": module_name,
            "test_file": test_file,
            **result
        }

        if result["error"]:
            print(f"    ❌ 错误: {result['error']}")
        else:
            status = "✅" if result["failed"] == 0 else "❌"
            print(f"    {status} 通过: {result['passed']}, 失败: {result['failed']}, 总计: {result['total']}")

    return results


def update_status_file(results: dict):
    """
    更新 PROJECT_STATUS.md 文件
    """
    print("\n" + "=" * 60)
    print("更新 PROJECT_STATUS.md")
    print("=" * 60)

    if not STATUS_FILE.exists():
        print(f"❌ 错误: {STATUS_FILE} 不存在")
        return False

    # 读取当前文件内容
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 获取当前时间（北京时间）
    now = datetime.now()
    # 假设当前是北京时间 (UTC+8)
    # 由于 datetime.now() 返回本地时间，而系统时区可能不同
    # 我们直接使用当前时间并在注释中标明
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 更新最后更新时间
    # 匹配格式: "最后更新时间: YYYY-MM-DD HH:MM:SS (北京时间)"
    time_pattern = r"(最后更新时间[:：]?\s*)(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*\(北京时间\)"
    time_replacement = rf"\g<1>{time_str} (北京时间)"
    content = re.sub(time_pattern, time_replacement, content)

    # 更新测试结果表格
    # 表格格式:
    # | Task | 模块 | 测试文件 | 测试数 | 通过数 | 状态 | 最后验证 |
    # |------|------|----------|--------|--------|------|----------|
    # | 1.1 | Feature Store | test_feature_store.py | 14 | 14 | ✅ | <!--日期--> |

    # 为每个测试文件更新对应的行
    task_mapping = {
        "test_feature_store.py": ("1.1", "Feature Store"),
        "test_reconciler.py": ("1.2", "Reconciler"),
        "test_depth_checker.py": ("1.3", "深度检查"),
        "test_time_window_policy.py": ("1.4", "时间窗口"),
        "test_binance_connector.py": ("B1", "Binance Connector"),
        "test_binance_private_stream.py": ("B2", "Binance Private Stream"),
        "test_binance_degraded_cascade.py": ("B3", "Degraded Cascade"),
        "test_deterministic_layer.py": ("D1", "Deterministic Layer"),
        "test_hard_properties.py": ("D2", "Hard Properties"),
    }

    # 更新日期部分
    date_str = now.strftime("%Y-%m-%d")

    for filename, data in results.items():
        if filename in task_mapping:
            task_id, module_name = task_mapping[filename]
            test_count = data["total"]
            passed_count = data["passed"]
            failed_count = data["failed"]

            # 状态符号
            if failed_count > 0:
                status = "❌"
            elif test_count == 0:
                status = "⚠️"
            else:
                status = "✅"

            # 构造正则表达式来匹配该行
            # 匹配: | {task_id} | {module_name} | test_xxx.py | {digits} | {digits} | {status} | <!--日期--> |
            row_pattern = rf"(\|\s*{re.escape(task_id)}\s*\|\s*{re.escape(module_name)}\s*\|\s*){re.escape(filename)}(\s*\|\s*)\d+(\s*\|\s*)\d+(\s*\|\s*)[❌✅⚠️](\s*\|\s*)<!--日期-->"
            row_replacement = rf"\g<1>{filename}\g<2>{test_count}\g<3>{passed_count}\g<4>{status}\g<5><!--{date_str}-->"

            new_content = re.sub(row_pattern, row_replacement, content)
            if new_content != content:
                print(f"  ✅ 更新: {module_name} -> {passed_count}/{test_count} 通过")
                content = new_content
            else:
                # 尝试更宽松的匹配
                print(f"  ⚠️ 未找到匹配行: {module_name} ({filename})")

    # 计算总计
    total_passed = sum(r["passed"] for r in results.values())
    total_tests = sum(r["total"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())

    # 更新总计行（如果有）
    # 匹配: "**Phase 1 核心验证总计：XXX/XXX 测试通过**"
    total_pattern = r"(\*\*Phase \d+[^：]*：)(\d+)/(\d+)\s+测试通过\*\*"
    if total_tests > 0:
        total_replacement = rf"\g<1>{total_passed}/{total_tests} 测试通过**"
        content = re.sub(total_pattern, total_replacement, content)
        print(f"\n  总计: {total_passed}/{total_tests} 通过, {total_failed} 失败")

    # 写入更新后的内容
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n✅ {STATUS_FILE} 已更新")
    return True


def main():
    """
    主函数
    """
    print("=" * 60)
    print("项目状态自动更新脚本")
    print("=" * 60)
    print(f"\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
    print(f"项目目录: {PROJECT_ROOT}")
    print(f"状态文件: {STATUS_FILE}")
    print(f"\n将运行 {len(TEST_FILES)} 个测试文件:\n")

    for i, test_file in enumerate(TEST_FILES, 1):
        print(f"  {i}. {test_file}")

    print("\n" + "-" * 60)

    # 运行测试并收集结果
    results = parse_test_results()

    # 显示汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    total_passed = 0
    total_failed = 0
    total_tests = 0

    for filename, data in results.items():
        status = "✅" if data["failed"] == 0 else "❌"
        print(f"  {status} {data['module']}: {data['passed']}/{data['total']} 通过")
        total_passed += data["passed"]
        total_failed += data["failed"]
        total_tests += data["total"]

    print(f"\n总计: {total_passed}/{total_tests} 通过, {total_failed} 失败")

    # 更新状态文件
    update_status_file(results)

    print("\n" + "=" * 60)
    print("脚本执行完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
