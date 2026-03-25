"""
Signal Sandbox 验证测试
用于验证 tools/signal_sandbox.py 的正确性
"""

import sys
sys.path.insert(0, ".")

import pytest
from decimal import Decimal
from tools.signal_sandbox import (
    run_signal_replay,
    detect_future_leaks,
    generate_report,
    SandboxResult,
    _generate_mock_price_samples,
    _convert_to_pv_sample,
    _convert_to_vol_sample,
    run_signal_replay_volume,
    run_signal_replay_price_volume,
)


@pytest.fixture
def prices():
    """生成模拟价格数据用于测试"""
    start_ts = 1700000000000
    end_ts = start_ts + 2 * 60 * 60 * 1000  # 2小时
    return _generate_mock_price_samples("BTCUSDT", start_ts, end_ts, interval_ms=60000, base_price=50000)


def test_signal_replay():
    """测试信号回放功能"""
    print("=" * 60)
    print("测试信号回放功能")
    print("=" * 60)
    
    # 生成模拟数据 - 使用更长的时间范围来生成足够的样本
    # EMA 需要 21 个样本 (slow_period=20 + 1), 使用 2小时数据 (120分钟 = 120个1分钟样本)
    start_ts = 1700000000000
    end_ts = start_ts + 2 * 60 * 60 * 1000  # 2小时
    prices = _generate_mock_price_samples("BTCUSDT", start_ts, end_ts, interval_ms=60000, base_price=50000)
    
    print(f"生成了 {len(prices)} 个价格样本")
    print(f"价格范围: {prices[0].close_price} - {prices[-1].close_price}")
    
    # 测试 EMA_CROSSOVER
    print("\n--- 测试 EMA_CROSSOVER ---")
    ema_results = run_signal_replay("BTCUSDT", prices, "EMA_CROSSOVER")
    print(f"生成了 {len(ema_results)} 个 EMA 信号")
    if ema_results:
        print(f"第一个信号: timestamp={ema_results[0].timestamp}, valid={ema_results[0].is_valid}")
        print(f"最后一个信号: timestamp={ema_results[-1].timestamp}, valid={ema_results[-1].is_valid}")
    
    # 测试 PRICE_MOMENTUM
    print("\n--- 测试 PRICE_MOMENTUM ---")
    momentum_results = run_signal_replay("BTCUSDT", prices, "PRICE_MOMENTUM")
    print(f"生成了 {len(momentum_results)} 个动量信号")
    if momentum_results:
        print(f"最后一个信号: {momentum_results[-1].value}")
    
    # 测试 BOLLINGER_BAND
    print("\n--- 测试 BOLLINGER_BAND ---")
    bb_results = run_signal_replay("BTCUSDT", prices, "BOLLINGER_BAND")
    print(f"生成了 {len(bb_results)} 个布林带信号")
    if bb_results:
        print(f"最后一个信号: {bb_results[-1].value}")
    
    return prices


def test_future_leak_detection(prices):
    """测试未来函数泄漏检测"""
    print("\n" + "=" * 60)
    print("测试未来函数泄漏检测")
    print("=" * 60)
    
    # 测试 EMA 泄漏检测
    print("\n--- 测试 EMA 泄漏检测 ---")
    ema_results = run_signal_replay("BTCUSDT", prices, "EMA_CROSSOVER")
    if ema_results:
        leak_report = detect_future_leaks(ema_results)
        print(f"信号: {leak_report.signal_name}")
        print(f"存在泄漏: {leak_report.has_leak}")
        print(f"泄漏程度: {leak_report.leak_severity}")
        print(f"泄漏点数: {len(leak_report.leak_points)}")
        print(f"描述: {leak_report.description}")
    
    # 测试动量泄漏检测
    print("\n--- 测试 Momentum 泄漏检测 ---")
    momentum_results = run_signal_replay("BTCUSDT", prices, "PRICE_MOMENTUM")
    if momentum_results:
        leak_report = detect_future_leaks(momentum_results)
        print(f"信号: {leak_report.signal_name}")
        print(f"存在泄漏: {leak_report.has_leak}")
        print(f"泄漏程度: {leak_report.leak_severity}")
        print(f"泄漏点数: {len(leak_report.leak_points)}")
        print(f"描述: {leak_report.description}")


def test_report_generation(prices):
    """测试报告生成"""
    print("\n" + "=" * 60)
    print("测试报告生成")
    print("=" * 60)
    
    # 创建测试结果
    ema_results = run_signal_replay("BTCUSDT", prices, "EMA_CROSSOVER")
    bb_results = run_signal_replay("BTCUSDT", prices, "BOLLINGER_BAND")
    
    # 检测泄漏
    leak_reports = []
    for results in [ema_results, bb_results]:
        if results:
            leak_reports.append(detect_future_leaks(results))
    
    # 创建 SandboxResult
    sandbox_result = SandboxResult(
        symbol="BTCUSDT",
        start_ts=1700000000000,
        end_ts=1700000000000 + 2 * 60 * 60 * 1000,
        signals_tested=["EMA_CROSSOVER", "BOLLINGER_BAND"],
        test_results=ema_results + bb_results,
        leak_reports=leak_reports,
    )
    
    # 生成文本报告
    print("\n--- 文本报告 ---")
    text_report = generate_report(sandbox_result, output_format="text")
    print(text_report)
    
    # 生成 JSON 报告
    print("\n--- JSON 报告 ---")
    json_report = generate_report(sandbox_result, output_format="json")
    print(json_report)


def test_volume_signals():
    """测试成交量信号"""
    print("\n" + "=" * 60)
    print("测试成交量信号")
    print("=" * 60)
    
    # 生成模拟数据 - 使用更长的时间范围
    start_ts = 1700000000000
    end_ts = start_ts + 2 * 60 * 60 * 1000  # 2小时
    prices = _generate_mock_price_samples("BTCUSDT", start_ts, end_ts, interval_ms=60000, base_price=50000)
    pv_samples = [_convert_to_pv_sample(p) for p in prices]
    vol_samples = [_convert_to_vol_sample(p) for p in prices]
    
    # 测试 VOLUME_EXPANSION
    print("\n--- 测试 VOLUME_EXPANSION ---")
    vol_results = run_signal_replay_volume("BTCUSDT", vol_samples, "VOLUME_EXPANSION")
    print(f"生成了 {len(vol_results)} 个成交量扩张信号")
    if vol_results:
        print(f"最后一个信号: {vol_results[-1].value}")
        leak_report = detect_future_leaks(vol_results)
        print(f"泄漏检测: has_leak={leak_report.has_leak}, severity={leak_report.leak_severity}")
    
    # 测试 VOLATILITY_COMPRESSION
    print("\n--- 测试 VOLATILITY_COMPRESSION ---")
    vol_results = run_signal_replay_price_volume("BTCUSDT", pv_samples, "VOLATILITY_COMPRESSION")
    print(f"生成了 {len(vol_results)} 个波动率压缩信号")
    if vol_results:
        print(f"最后一个信号: {vol_results[-1].value}")
        leak_report = detect_future_leaks(vol_results)
        print(f"泄漏检测: has_leak={leak_report.has_leak}, severity={leak_report.leak_severity}")


def main():
    """主测试函数"""
    print("Signal Sandbox 验证测试")
    print()
    
    try:
        # 测试信号回放
        prices = test_signal_replay()
        
        # 测试未来函数泄漏检测
        test_future_leak_detection(prices)
        
        # 测试报告生成
        test_report_generation(prices)
        
        # 测试成交量信号
        test_volume_signals()
        
        print("\n" + "=" * 60)
        print("所有测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
