"""
网络诊断脚本 - 测试直连 vs 代理
==============================
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp
from dotenv import load_dotenv

async def test_connections():
    load_dotenv()

    proxy_url = os.getenv("BINANCE_PROXY_URL")
    print(f"代理配置: {proxy_url}")

    print("\n" + "="*60)
    print("测试 1: 不使用代理")
    print("="*60)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://httpbin.org/ip", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"  httpbin.org: {resp.status} - {await resp.text()}")
    except Exception as e:
        print(f"  httpbin.org 失败: {e}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://testnet.binance.vision/api/v3/ping",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"  testnet.binance.vision: {resp.status} - {await resp.text()}")
    except Exception as e:
        print(f"  testnet.binance.vision 失败: {e}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://demo.binance.com/api/v3/ping",
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                print(f"  demo.binance.com: {resp.status} - {await resp.text()}")
    except Exception as e:
        print(f"  demo.binance.com 失败: {e}")

    if proxy_url:
        print("\n" + "="*60)
        print("测试 2: 使用代理 (HTTP_PROXY / HTTPS_PROXY 环境变量)")
        print("="*60)

        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://httpbin.org/ip", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    print(f"  httpbin.org: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"  httpbin.org 失败: {e}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://testnet.binance.vision/api/v3/ping",
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    print(f"  testnet.binance.vision: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"  testnet.binance.vision 失败: {e}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://demo.binance.com/api/v3/ping",
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    print(f"  demo.binance.com: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"  demo.binance.com 失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_connections())
