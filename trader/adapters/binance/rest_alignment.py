"""
REST Alignment Coordinator
===========================
REST 对齐协调器，负责与 Binance REST API 同步账户数据。

功能：
- 定期/触发式对齐
- 优先级调度（P0/P1/P2）
- 限流和退避
- 429/418 错误处理
- 输出 RestAlignmentSnapshot
"""
import asyncio
import logging
import time
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Callable
from urllib.parse import urlencode

if TYPE_CHECKING:
    import aiohttp

from trader.adapters.binance.rate_limit import RestRateBudget, Priority, RateBudgetConfig
from trader.adapters.binance.backoff import BackoffController, BackoffConfig
from trader.adapters.binance.proxy_failover import get_proxy_failover_controller


logger = logging.getLogger(__name__)


@dataclass
class RestAlignmentSnapshot:
    """REST 对齐快照"""
    open_orders: List[Dict[str, Any]]
    account: Dict[str, Any]
    trades: Optional[List[Dict[str, Any]]]
    exchange_ts_ms: int
    local_ts_ms: int
    alignment_reason: str


@dataclass
class AlignmentConfig:
    """对齐配置"""
    base_url: str = "https://testnet.binance.vision/api"
    recv_window_ms: int = 10000
    proxy_url: Optional[str] = None
    p0_interval_seconds: float = 60.0
    p1_interval_seconds: float = 120.0
    p2_interval_seconds: float = 300.0
    min_alignment_interval: float = 30.0
    alignment_timeout: float = 10.0


@dataclass
class AlignmentMetrics:
    """对齐指标"""
    total_alignments: int = 0
    successful_alignments: int = 0
    failed_alignments: int = 0
    last_alignment_ts: float = 0.0
    last_alignment_reason: str = ""
    last_error: Optional[str] = None
    last_rest_success_ts_ms: int = 0


class RESTAlignmentCoordinator:
    """
    REST 对齐协调器

    管理与 Binance REST API 的同步，确保数据一致性。
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        rate_budget: Optional[RestRateBudget] = None,
        backoff: Optional[BackoffController] = None,
        config: Optional[AlignmentConfig] = None,
    ):
        self._api_key = api_key
        self._secret_key = secret_key
        self._rate_budget = rate_budget or RestRateBudget()
        self._backoff = backoff or BackoffController()
        self._config = config or AlignmentConfig()

        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False

        self._metrics = AlignmentMetrics()
        self._last_alignment_ts = 0.0
        self._timestamp_offset_ms: int = 0
        self._proxy_failover = get_proxy_failover_controller()

        self._snapshot_handlers: List[Callable[[RestAlignmentSnapshot], None]] = []

        self._alignment_tasks: Dict[str, asyncio.Task] = {}
        self._alignment_lock = asyncio.Lock()

    def register_snapshot_handler(self, handler: Callable[[RestAlignmentSnapshot], None]) -> None:
        """注册快照处理器"""
        self._snapshot_handlers.append(handler)

    async def start(self) -> None:
        """启动协调器"""
        if self._running:
            return

        import aiohttp
        self._running = True
        self._session = aiohttp.ClientSession(trust_env=True)
        await self._sync_server_time_offset()
        logger.info("[RESTAlignment] Started")

    def _resolve_proxy(self) -> Optional[str]:
        """解析代理配置（支持主备自动切换）。"""
        return self._proxy_failover.select_proxy(self._config.proxy_url)

    async def _sync_server_time_offset(self) -> None:
        """同步服务器时间偏移，降低 -1021 风险。"""
        if self._session is None:
            return
        import aiohttp
        url = f"{self._config.base_url}/v3/time"
        proxy = self._resolve_proxy()
        try:
            async with self._session.get(
                url,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=self._config.alignment_timeout),
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                server_ms = int(data.get("serverTime", 0))
                if server_ms <= 0:
                    return
                local_ms = int(time.time() * 1000)
                self._timestamp_offset_ms = server_ms - local_ms
                logger.info(
                    "[RESTAlignment] Time offset synced: offset_ms=%s",
                    self._timestamp_offset_ms,
                )
                self._proxy_failover.report_success(proxy)
        except Exception as e:
            # 时间同步失败不应阻断主流程，但需要可观测。
            self._proxy_failover.report_failure(proxy)
            logger.warning("[RESTAlignment] Time sync failed: %s", e)
            return

    async def stop(self) -> None:
        """停止协调器"""
        self._running = False

        for task in self._alignment_tasks.values():
            task.cancel()

        if self._session:
            await self._session.close()
            self._session = None

        logger.info("[RESTAlignment] Stopped")

    async def force_alignment(self, reason: str, priority: Priority = Priority.P0) -> Optional[RestAlignmentSnapshot]:
        """
        强制对齐

        Args:
            reason: 对齐原因
            priority: 请求优先级

        Returns:
            RestAlignmentSnapshot 或 None
        """
        async with self._alignment_lock:
            now = time.time()
            if now - self._last_alignment_ts < self._config.min_alignment_interval:
                logger.info(f"[RESTAlignment] Skipping alignment: too soon since last ({now - self._last_alignment_ts:.1f}s)")
                return None

            self._last_alignment_ts = now
            return await self._do_alignment(reason, priority)

    async def force_alignment_p0(self, reason: str) -> Optional[RestAlignmentSnapshot]:
        """
        强制 P0 对齐（永不跳过，受 rate budget/backoff 影响）

        Args:
            reason: 对齐原因

        Returns:
            RestAlignmentSnapshot 或 None
        """
        async with self._alignment_lock:
            self._last_alignment_ts = time.time()
            return await self._do_alignment(reason, Priority.P0)

    async def _do_alignment(
        self,
        reason: str,
        priority: Priority = Priority.P0
    ) -> Optional[RestAlignmentSnapshot]:
        """
        执行对齐

        Args:
            reason: 对齐原因
            priority: 请求优先级

        Returns:
            RestAlignmentSnapshot
        """
        logger.info(f"[RESTAlignment] Starting alignment: reason={reason}, priority={priority.name}")

        open_orders = None
        account = None
        trades = None

        try:
            if priority == Priority.P0:
                open_orders = await self._fetch_open_orders(Priority.P0)
                account = await self._fetch_account(Priority.P0)

            elif priority == Priority.P1:
                open_orders = await self._fetch_open_orders(Priority.P1)
                account = await self._fetch_account(Priority.P1)
                trades = await self._fetch_my_trades(Priority.P1)

            else:
                open_orders = await self._fetch_open_orders(Priority.P2)
                account = await self._fetch_account(Priority.P2)
                trades = await self._fetch_my_trades(Priority.P2)

            exchange_ts = int(time.time() * 1000)
            snapshot = RestAlignmentSnapshot(
                open_orders=open_orders or [],
                account=account or {},
                trades=trades,
                exchange_ts_ms=exchange_ts,
                local_ts_ms=int(time.time() * 1000),
                alignment_reason=reason
            )

            self._metrics.total_alignments += 1
            self._metrics.successful_alignments += 1
            self._metrics.last_alignment_ts = time.time()
            self._metrics.last_alignment_reason = reason
            self._metrics.last_error = None
            self._metrics.last_rest_success_ts_ms = int(time.time() * 1000)

            self._backoff.reset("alignment")

            for handler in self._snapshot_handlers:
                try:
                    handler(snapshot)
                except Exception as e:
                    logger.error(f"[RESTAlignment] Handler error: {e}")

            logger.info(
                f"[RESTAlignment] Alignment completed: "
                f"orders={len(open_orders or [])}, reason={reason}"
            )

            return snapshot

        except Exception as e:
            logger.error(f"[RESTAlignment] Alignment failed: {e}")
            self._metrics.failed_alignments += 1
            self._metrics.last_error = str(e)
            return None

    async def _fetch_open_orders(self, priority: Priority) -> List[Dict[str, Any]]:
        """获取未结订单"""
        endpoint = "/v3/openOrders"
        return await self._signed_request("GET", endpoint, priority=priority)

    async def _fetch_account(self, priority: Priority) -> Dict[str, Any]:
        """获取账户信息"""
        endpoint = "/v3/account"
        return await self._signed_request("GET", endpoint, priority=priority)

    async def _fetch_my_trades(self, priority: Priority, limit: int = 100) -> List[Dict[str, Any]]:
        """获取成交记录"""
        endpoint = "/v3/myTrades"
        return await self._signed_request(
            "GET",
            endpoint,
            params={"limit": limit},
            priority=priority
        )

    async def _signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        priority: Priority = Priority.P2
    ) -> Any:
        """
        发送签名请求
        """
        import aiohttp
        params = params or {}
        headers = {"X-MBX-APIKEY": self._api_key}

        task_name = f"rest_{endpoint}"

        current_recv_window_ms = int(self._config.recv_window_ms)
        for attempt in range(5):
            req_params = dict(params)
            req_params["timestamp"] = int(time.time() * 1000) + self._timestamp_offset_ms
            req_params["recvWindow"] = current_recv_window_ms

            query_string = urlencode(sorted(req_params.items()))
            signature = hmac.new(
                self._secret_key.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            url = f"{self._config.base_url}{endpoint}?{query_string}&signature={signature}"

            if not await self._rate_budget.acquire_async(cost=1, priority=priority, timeout=30.0):
                delay = self._backoff.next_delay(task_name, retry_after_s=None)
                logger.warning(f"[RESTAlignment] Rate limit wait: {delay:.2f}s")
                await asyncio.sleep(delay)
                continue

            try:
                proxy = self._resolve_proxy()
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self._config.alignment_timeout),
                    proxy=proxy,
                ) as resp:
                    self._proxy_failover.report_success(proxy)
                    if resp.status == 200:
                        data = await resp.json()
                        self._backoff.reset(task_name)
                        return data

                    elif resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        retry_after_s = int(retry_after) if retry_after else None

                        logger.warning(f"[RESTAlignment] 429 received: retry_after={retry_after_s}")
                        self._rate_budget.on_429(retry_after_s)

                        if priority != Priority.P0:
                            logger.warning(f"[RESTAlignment] Downgrading to P0")
                            priority = Priority.P0

                        delay = self._backoff.next_delay(task_name, retry_after_s)
                        await asyncio.sleep(delay)
                        continue

                    elif resp.status == 418:
                        logger.error("[RESTAlignment] 418 received: IP banned")
                        self._rate_budget.on_418()
                        self._backoff.next_delay(task_name)
                        raise Exception("IP banned (418)")

                    else:
                        error_text = await resp.text()
                        if resp.status == 400 and '"code":-1021' in error_text:
                            logger.warning(
                                "[RESTAlignment] -1021 detected (attempt=%s, recvWindow=%sms), "
                                "syncing time and retrying",
                                attempt + 1,
                                current_recv_window_ms,
                            )
                            await self._sync_server_time_offset()
                            current_recv_window_ms = min(60000, max(current_recv_window_ms * 2, 10000))
                            continue
                        logger.error(f"[RESTAlignment] Request failed: {resp.status} - {error_text}")
                        raise Exception(f"HTTP {resp.status}: {error_text}")

            except asyncio.TimeoutError:
                self._proxy_failover.report_failure(proxy)
                logger.warning(f"[RESTAlignment] Request timeout")
                delay = self._backoff.next_delay(task_name)
                await asyncio.sleep(delay)
                continue

            except aiohttp.ClientError as e:
                self._proxy_failover.report_failure(proxy)
                logger.warning(f"[RESTAlignment] Client error: {e}")
                delay = self._backoff.next_delay(task_name)
                await asyncio.sleep(delay)
                continue

        raise Exception("Max retries reached")

    def get_metrics(self) -> Dict[str, Any]:
        """获取指标"""
        return {
            "total_alignments": self._metrics.total_alignments,
            "successful_alignments": self._metrics.successful_alignments,
            "failed_alignments": self._metrics.failed_alignments,
            "last_alignment_ts": self._metrics.last_alignment_ts,
            "last_alignment_reason": self._metrics.last_alignment_reason,
            "last_error": self._metrics.last_error,
            "last_rest_success_ts_ms": self._metrics.last_rest_success_ts_ms,
            "rate_budget": self._rate_budget.get_state(),
        }
