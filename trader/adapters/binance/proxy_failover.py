"""
Proxy Failover Controller
=========================
统一管理 Binance 访问链路的主备代理切换。

设计目标：
- 主代理失败后自动切换到备代理
- 冷却期后自动恢复主代理优先
- 对调用方保持最小侵入（select + report_success + report_failure）
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from threading import RLock
from typing import Dict, List, Optional


@dataclass(slots=True)
class ProxyFailoverConfig:
    """代理故障切换配置。"""

    failure_threshold: int = 2
    cooldown_seconds: float = 30.0


@dataclass(slots=True)
class ProxyRuntimeState:
    """单代理运行时状态。"""

    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_ts: float = 0.0
    cooldown_until_ts: float = 0.0


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class ProxyFailoverController:
    """
    主备代理切换控制器（线程安全）。

    代理优先级：
    1) explicit_proxy（代码显式配置）
    2) BINANCE_PROXY_URL（主）
    3) BINANCE_BACKUP_PROXY_URL（备）
    4) BINANCE_PROXY / HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
    """

    def __init__(self, config: Optional[ProxyFailoverConfig] = None):
        cfg = config or ProxyFailoverConfig()
        self._config = ProxyFailoverConfig(
            failure_threshold=_parse_int_env(
                "BINANCE_PROXY_FAILOVER_THRESHOLD",
                cfg.failure_threshold,
            ),
            cooldown_seconds=_parse_float_env(
                "BINANCE_PROXY_FAILOVER_COOLDOWN_SECONDS",
                cfg.cooldown_seconds,
            ),
        )
        self._states: Dict[str, ProxyRuntimeState] = {}
        self._last_selected_proxy: Optional[str] = None
        self._lock = RLock()

    @staticmethod
    def _normalize(proxy: Optional[str]) -> Optional[str]:
        if proxy is None:
            return None
        cleaned = str(proxy).strip()
        return cleaned or None

    def _build_candidates(self, explicit_proxy: Optional[str] = None) -> List[str]:
        ordered = [
            self._normalize(explicit_proxy),
            self._normalize(os.environ.get("BINANCE_PROXY_URL")),
            self._normalize(os.environ.get("BINANCE_BACKUP_PROXY_URL")),
            self._normalize(os.environ.get("BINANCE_PROXY")),
            self._normalize(os.environ.get("HTTPS_PROXY")),
            self._normalize(os.environ.get("HTTP_PROXY")),
            self._normalize(os.environ.get("ALL_PROXY")),
        ]
        dedup: List[str] = []
        seen: set[str] = set()
        for item in ordered:
            if item is None or item in seen:
                continue
            seen.add(item)
            dedup.append(item)
        return dedup

    def _ensure_state(self, proxy: str) -> ProxyRuntimeState:
        state = self._states.get(proxy)
        if state is None:
            state = ProxyRuntimeState()
            self._states[proxy] = state
        return state

    def select_proxy(self, explicit_proxy: Optional[str] = None) -> Optional[str]:
        """选择当前可用代理。"""
        candidates = self._build_candidates(explicit_proxy)
        if not candidates:
            return None

        now = time.time()
        with self._lock:
            for proxy in candidates:
                self._ensure_state(proxy)

            for proxy in candidates:
                st = self._states[proxy]
                if st.cooldown_until_ts <= now:
                    self._last_selected_proxy = proxy
                    return proxy

            # 全部处于冷却期时，选择最早恢复的那个。
            best = min(candidates, key=lambda p: self._states[p].cooldown_until_ts)
            self._last_selected_proxy = best
            return best

    def report_success(self, proxy: Optional[str]) -> None:
        """上报成功，清零连续失败并解除冷却。"""
        target = self._normalize(proxy)
        if target is None:
            return
        with self._lock:
            st = self._ensure_state(target)
            st.total_successes += 1
            st.consecutive_failures = 0
            st.cooldown_until_ts = 0.0

    def report_failure(self, proxy: Optional[str]) -> None:
        """上报失败，达到阈值后进入冷却并触发切换。"""
        target = self._normalize(proxy)
        if target is None:
            return

        now = time.time()
        with self._lock:
            st = self._ensure_state(target)
            st.total_failures += 1
            st.consecutive_failures += 1
            st.last_failure_ts = now

            if st.consecutive_failures >= self._config.failure_threshold:
                st.cooldown_until_ts = max(
                    st.cooldown_until_ts,
                    now + self._config.cooldown_seconds,
                )
                st.consecutive_failures = 0

    def get_state(self, explicit_proxy: Optional[str] = None) -> Dict[str, object]:
        """导出可观测状态。"""
        candidates = self._build_candidates(explicit_proxy)
        now = time.time()

        with self._lock:
            proxies: Dict[str, object] = {}
            for proxy in candidates:
                st = self._ensure_state(proxy)
                proxies[proxy] = {
                    "consecutive_failures": st.consecutive_failures,
                    "total_failures": st.total_failures,
                    "total_successes": st.total_successes,
                    "last_failure_ts": st.last_failure_ts if st.last_failure_ts > 0 else None,
                    "cooldown_until_ts": st.cooldown_until_ts if st.cooldown_until_ts > now else None,
                    "in_cooldown": st.cooldown_until_ts > now,
                }

            return {
                "failure_threshold": self._config.failure_threshold,
                "cooldown_seconds": self._config.cooldown_seconds,
                "candidates": candidates,
                "active_proxy": (
                    self._last_selected_proxy if self._last_selected_proxy in candidates else None
                ),
                "proxies": proxies,
            }


_controller: Optional[ProxyFailoverController] = None
_controller_lock = RLock()


def get_proxy_failover_controller() -> ProxyFailoverController:
    """获取全局单例控制器。"""
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = ProxyFailoverController()
        return _controller


def reset_proxy_failover_controller() -> None:
    """重置全局单例（测试用）。"""
    global _controller
    with _controller_lock:
        _controller = None
