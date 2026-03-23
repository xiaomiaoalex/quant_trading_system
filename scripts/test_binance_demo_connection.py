"""
test_binance_demo_connection.py
===============================

Binance Spot Demo / Spot Testnet 连接测试脚本

功能：
1. 验证基础网络连通性（ping / time）
2. 验证账户鉴权（connect / account）
3. 验证账户查询（positions / open orders）
4. 验证订单生命周期（place -> get -> cancel）

环境变量：
- BINANCE_API_KEY
- BINANCE_SECRET_KEY
- BINANCE_PROXY_URL          可选，例如 http://127.0.0.1:7890
- BINANCE_ENV                可选，demo 或 testnet，默认 demo

运行方式：
.venv\\Scripts\\python.exe scripts\\test_binance_demo_connection.py
"""

import asyncio
import os
import sys
import time
import traceback
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)


def build_config(
    api_key: str,
    secret_key: str,
    proxy_url: str | None,
    env_name: str,
) -> BinanceSpotDemoBrokerConfig:
    env_name = env_name.strip().lower()

    if env_name == "testnet":
        return BinanceSpotDemoBrokerConfig.for_testnet(
            api_key=api_key,
            secret_key=secret_key,
            timeout=15.0,
            max_retries=3,
            recv_window=5000,
            proxy_url=proxy_url,
            verify_ssl=True,
        )

    if env_name == "demo":
        return BinanceSpotDemoBrokerConfig.for_demo(
            api_key=api_key,
            secret_key=secret_key,
            timeout=15.0,
            max_retries=3,
            recv_window=5000,
            proxy_url=proxy_url,
            verify_ssl=True,
        )

    raise ValueError(f"Unsupported BINANCE_ENV: {env_name}. Use 'demo' or 'testnet'.")


async def test_connection() -> bool:
    load_dotenv()

    api_key = os.getenv("BINANCE_API_KEY")
    secret_key = os.getenv("BINANCE_SECRET_KEY")
    proxy_url = os.getenv("BINANCE_PROXY_URL")
    env_name = os.getenv("BINANCE_ENV", "demo").strip().lower()

    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url

    if not api_key or not secret_key:
        print("❌ 错误：未找到 API 密钥")
        print("请在 .env 文件中设置：")
        print("   BINANCE_API_KEY=...")
        print("   BINANCE_SECRET_KEY=...")
        print("可选：")
        print("   BINANCE_ENV=demo 或 testnet")
        print("   BINANCE_PROXY_URL=http://127.0.0.1:7890")
        return False

    if api_key in {"your_demo_api_key_here", "your_testnet_api_key_here"}:
        print("❌ 错误：请在 .env 文件中填入真实 API 密钥")
        return False

    try:
        config = build_config(
            api_key=api_key,
            secret_key=secret_key,
            proxy_url=proxy_url,
            env_name=env_name,
        )
    except Exception as exc:
        print(f"❌ 配置错误：{exc}")
        return False

    broker = BinanceSpotDemoBroker(config)

    print("=" * 64)
    print("Binance Spot Broker 连接测试")
    print("=" * 64)
    print(f"环境:         {env_name}")
    print(f"Broker Name:  {config.broker_name}")
    print(f"REST API:     {config.base_url}")
    print(f"WebSocket:    {config.ws_url}")
    if proxy_url:
        print(f"Proxy:        {proxy_url}")
    else:
        print("Proxy:        <未设置>")
    print()

    try:
        print("1) 测试 ping ...")
        ok = await broker.ping()
        print(f"   ✅ ping 成功: {ok}")

        print("\n2) 测试 server time ...")
        server_time = await broker.get_server_time()
        print(f"   ✅ serverTime: {server_time}")

        print("\n3) 建立连接并拉取账户 ...")
        await broker.connect()
        print("   ✅ connect 成功")

    except Exception as exc:
        print(f"❌ 基础连接失败：{exc}")
        print(traceback.format_exc())
        try:
            await broker.disconnect()
        except Exception:
            pass
        return False

    try:
        print("\n4) 查询账户信息 ...")
        account = await broker.get_account()
        print(f"   总权益:   {account.total_equity} {account.currency}")
        print(f"   可用资金: {account.available_cash} {account.currency}")
    except Exception as exc:
        print(f"   ⚠️ 获取账户信息失败：{exc}")

    try:
        print("\n5) 查询持仓/余额视图 ...")
        positions = await broker.get_positions()
        if positions:
            print(f"   持仓数量: {len(positions)}")
            for pos in positions:
                print(
                    f"   - {pos.symbol}: quantity={pos.quantity}, "
                    f"avg_price={pos.avg_price}, unrealized_pnl={pos.unrealized_pnl}"
                )
        else:
            print("   无持仓（Spot Demo/Testnet 初始为空是正常的）")
    except Exception as exc:
        print(f"   ⚠️ 获取持仓失败：{exc}")

    try:
        print("\n6) 查询未结订单 ...")
        open_orders = await broker.get_open_orders()
        print(f"   未结订单数量: {len(open_orders)}")
    except Exception as exc:
        print(f"   ⚠️ 获取未结订单失败：{exc}")

    # 订单生命周期测试
    test_symbol = "BTCUSDT"
    test_price = Decimal("50000")
    test_qty = Decimal("0.001")
    test_client_order_id = f"spot_test_{int(time.time())}"

    try:
        print("\n7) 测试下单（LIMIT BUY BTCUSDT）...")
        print(f"   symbol={test_symbol}, qty={test_qty}, price={test_price}")
        print(f"   client_order_id={test_client_order_id}")

        order = await broker.place_order(
            symbol=test_symbol,
            side="BUY",
            order_type="LIMIT",
            quantity=test_qty,
            price=test_price,
            client_order_id=test_client_order_id,
        )

        print("   ✅ 下单成功")
        print(f"   broker_order_id: {order.broker_order_id}")
        print(f"   client_order_id: {order.client_order_id}")
        print(f"   status:          {order.status}")
        print(f"   quantity:        {order.quantity}")
        print(f"   filled_quantity: {order.filled_quantity}")

        print("\n8) 查询订单 ...")
        fetched = await broker.get_order(
            client_order_id=test_client_order_id,
            symbol=test_symbol,
        )
        if fetched:
            print("   ✅ 查询成功")
            print(f"   status:          {fetched.status}")
            print(f"   broker_order_id: {fetched.broker_order_id}")
        else:
            print("   ⚠️ 查询返回为空")

        print("\n9) 撤单 ...")
        cancelled = await broker.cancel_order(
            client_order_id=test_client_order_id,
            symbol=test_symbol,
        )
        if cancelled:
            print("   ✅ 撤单成功")
        else:
            print("   ⚠️ 撤单返回 False")

    except Exception as exc:
        print(f"   ⚠️ 下单/查单/撤单链路失败：{exc}")
        print(traceback.format_exc())

    print("\n10) 断开连接 ...")
    try:
        await broker.disconnect()
        print("   ✅ 已断开")
    except Exception as exc:
        print(f"   ⚠️ 断开连接失败：{exc}")

    print("\n✅ 测试完成")
    return True


if __name__ == "__main__":
    result = asyncio.run(test_connection())
    sys.exit(0 if result else 1)