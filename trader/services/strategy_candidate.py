from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from trader.api.models.schemas import (
    BacktestGateResult,
    PromotePaperError,
    PromotePaperResponse,
    StrategyCandidate,
    StrategyCandidateCreateRequest,
    StrategyCandidateStatus,
    StrategyRegisterRequest,
)
from trader.services.strategy import StrategyService
from trader.storage.in_memory import ControlPlaneInMemoryStorage, get_storage

# 按 candidate_id 粒度加锁，防止并发 promote
_promote_locks: dict[str, asyncio.Lock] = {}


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class StrategyCandidateService:
    """Control-plane lifecycle service for research-to-runtime strategy candidates."""

    _allowed_transitions: dict[str, set[str]] = {
        "DRAFT": {"DEBUG_PASSED", "BACKTEST_RUNNING", "REJECTED"},
        "DEBUG_PASSED": {"BACKTEST_RUNNING", "REJECTED"},
        "BACKTEST_RUNNING": {"BACKTEST_PASSED", "REJECTED"},
        "BACKTEST_PASSED": {"VALIDATION_PASSED", "REJECTED"},
        "VALIDATION_PASSED": {"APPROVED_FOR_PAPER", "REJECTED"},
        "APPROVED_FOR_PAPER": {"PAPER_RUNNING", "STOPPED", "PAUSED_BY_RISK"},
        "PAPER_RUNNING": {"PAUSED_BY_RISK", "STOPPED"},
        "PAUSED_BY_RISK": {"PAPER_RUNNING", "STOPPED"},
        "STOPPED": {"PAPER_RUNNING"},
        "REJECTED": set(),
    }
    _delete_protected_statuses = {"APPROVED_FOR_PAPER", "PAPER_RUNNING", "PAUSED_BY_RISK"}

    def __init__(self, storage: ControlPlaneInMemoryStorage | None = None):
        self._storage = storage or get_storage()

    def create_candidate(self, request: StrategyCandidateCreateRequest) -> StrategyCandidate:
        if self._storage.get_strategy(request.strategy_id) is None:
            StrategyService().register_strategy(
                StrategyRegisterRequest(
                    strategy_id=request.strategy_id,
                    name=request.name or request.strategy_id,
                    description=request.description,
                    entrypoint=f"dynamic:{request.strategy_id}",
                )
            )

        feature_version = "dev_smoke"
        if request.dataset is not None:
            feature_version = request.dataset.feature_version

        candidate = self._storage.create_strategy_candidate(
            {
                "strategy_id": request.strategy_id,
                "name": request.name,
                "description": request.description,
                "code": request.code,
                "code_version": request.code_version,
                "config": request.config,
                "dataset": request.dataset.model_dump() if request.dataset else None,
                "feature_version": feature_version,
            }
        )
        self._append_lifecycle_event(candidate, None, "DRAFT", "candidate_created")
        return StrategyCandidate(**candidate)

    def list_candidates(
        self, strategy_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[StrategyCandidate]:
        return [
            StrategyCandidate(**candidate)
            for candidate in self._storage.list_strategy_candidates(strategy_id, status, limit)
        ]

    def get_candidate(self, candidate_id: str) -> StrategyCandidate | None:
        candidate = self._storage.get_strategy_candidate(candidate_id)
        if candidate is None:
            return None
        return StrategyCandidate(**candidate)

    def delete_candidate(self, candidate_id: str) -> bool:
        candidate = self._require_candidate(candidate_id)
        status = str(candidate.get("status", "DRAFT"))
        if status in self._delete_protected_statuses:
            raise ValueError(
                f"Cannot delete StrategyCandidate in {status}; stop or detach runtime first"
            )

        deployment_id = candidate.get("deployment_id")
        if deployment_id:
            deployment = self._storage.get_deployment(str(deployment_id))
            deployment_status = str((deployment or {}).get("status", "")).upper()
            if deployment_status in {"RUNNING", "PAUSED"}:
                raise ValueError(
                    f"Cannot delete StrategyCandidate with active deployment {deployment_id}"
                )

        self._storage.append_event(
            {
                "stream_key": f"strategy_candidate:{candidate_id}",
                "event_type": "strategy_candidate.deleted",
                "schema_version": 1,
                "trace_id": f"candidate:{candidate_id}",
                "ts_ms": _utc_now_ms(),
                "source": "strategy_candidate_service",
                "payload": {
                    "candidate_id": candidate_id,
                    "strategy_id": candidate.get("strategy_id"),
                    "status": status,
                    "deployment_id": deployment_id,
                    "reason": "user_deleted",
                },
            }
        )
        deleted = self._storage.delete_strategy_candidate(candidate_id)
        return deleted is not None

    def mark_debug_passed(
        self, candidate_id: str, code_version: int | None = None
    ) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        updates: dict[str, Any] = {}
        if code_version is not None:
            updates["code_version"] = code_version
        return self._transition(candidate, "DEBUG_PASSED", "debug_passed", updates)

    def mark_backtest_running(self, candidate_id: str, backtest_run_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(
            candidate,
            "BACKTEST_RUNNING",
            "backtest_submitted",
            {"backtest_run_id": backtest_run_id},
        )

    def mark_backtest_passed(self, candidate_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(candidate, "BACKTEST_PASSED", "backtest_passed")

    def mark_backtest_failed(self, candidate_id: str, reason: str = "backtest_failed") -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(
            candidate,
            "REJECTED",
            reason,
            allow_reject=True,
        )

    def validate_candidate(self, candidate_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        current_status = str(candidate.get("status", "DRAFT"))
        if current_status not in {"BACKTEST_PASSED", "REJECTED"}:
            raise ValueError(
                f"validate_candidate can only be called from BACKTEST_PASSED or REJECTED, "
                f"got {current_status}"
            )

        backtest_run_id = candidate.get("backtest_run_id")
        failed_rules: list[str] = []
        metrics: dict[str, Any] = {}
        evidence_refs: dict[str, str] = {}

        if not backtest_run_id:
            failed_rules.append("missing_backtest_run_id")
        else:
            backtest = self._storage.get_backtest(str(backtest_run_id))
            if backtest is None:
                failed_rules.append("backtest_not_found")
            else:
                metrics = dict(backtest.get("metrics") or {})
                evidence_refs["backtest_run_id"] = str(backtest_run_id)
                if backtest.get("status") != "COMPLETED":
                    failed_rules.append("backtest_not_completed")
                data_mode = metrics.get("backtest_data_mode") or backtest.get("data_mode")
                if data_mode != "real_feature_store":
                    failed_rules.append("dev_smoke_backtest_not_deployable")

                # 使用风控后指标进行验证（如果存在），否则回退到 raw metrics
                risk_adjusted_metrics = metrics.get("risk_adjusted_metrics") or {}
                risk_mode = metrics.get("risk_mode") or backtest.get("risk_mode")

                # max_drawdown：优先使用风控后指标
                max_drawdown_pct = float(
                    risk_adjusted_metrics.get("max_drawdown", metrics.get("max_drawdown_pct", 0.0)) or 0.0
                )
                if max_drawdown_pct > 25.0:
                    failed_rules.append("max_drawdown_exceeded")

                quality_score = (
                    metrics.get("data_quality_summary", {}).get("quality_score")
                    if isinstance(metrics.get("data_quality_summary"), dict)
                    else None
                )
                if quality_score is not None and float(quality_score) < 0.8:
                    failed_rules.append("data_quality_below_threshold")

                # total_return：优先使用风控后指标
                total_return = float(
                    risk_adjusted_metrics.get("total_return", metrics.get("total_return", 0.0)) or 0.0
                )
                if total_return <= 0:
                    failed_rules.append("cost_stress_non_positive")

                # 如果 risk_mode 是 raw_only，额外提醒
                if risk_mode == "raw_only":
                    failed_rules.append("raw_only_backtest_not_deployable")

        validation = BacktestGateResult(
            passed=len(failed_rules) == 0,
            failed_rules=failed_rules,
            metrics=metrics,
            evidence_refs=evidence_refs,
        )
        target_status: StrategyCandidateStatus = (
            "VALIDATION_PASSED" if validation.passed else "REJECTED"
        )
        return self._transition(
            candidate,
            target_status,
            "validation_passed" if validation.passed else "validation_failed",
            {"validation": validation.model_dump()},
            allow_reject=True,
        )

    def approve_for_paper(self, candidate_id: str, deployment_id: str) -> StrategyCandidate:
        candidate = self._require_candidate(candidate_id)
        return self._transition(
            candidate,
            "APPROVED_FOR_PAPER",
            "promoted_to_paper",
            {"deployment_id": deployment_id},
        )

    def _require_candidate(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._storage.get_strategy_candidate(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def _transition(
        self,
        candidate: dict[str, Any],
        to_status: StrategyCandidateStatus,
        reason: str,
        updates: dict[str, Any] | None = None,
        allow_reject: bool = False,
    ) -> StrategyCandidate:
        from_status = str(candidate.get("status", "DRAFT"))
        allowed = self._allowed_transitions.get(from_status, set())
        if to_status not in allowed and not (allow_reject and to_status == "REJECTED"):
            raise ValueError(f"Illegal transition {from_status} -> {to_status}")
        data = {**(updates or {}), "status": to_status}
        updated = self._storage.update_strategy_candidate(str(candidate["candidate_id"]), data)
        if updated is None:
            raise KeyError(str(candidate["candidate_id"]))
        self._append_lifecycle_event(updated, from_status, to_status, reason)
        return StrategyCandidate(**updated)

    async def promote_to_paper(self, candidate_id: str) -> PromotePaperResponse:
        """原子编排：检查状态 -> 保存代码版本 -> 注册策略 -> 创建 deployment -> load -> approve。

        运行态原子：code_version/audit 保留，runtime + deployment 必须干净（成功全有，失败全无）。
        """
        # 按 candidate_id 粒度加锁防并发
        if candidate_id not in _promote_locks:
            _promote_locks[candidate_id] = asyncio.Lock()
        lock = _promote_locks[candidate_id]

        if lock.locked():
            # 并发第二个请求，快速 409
            from fastapi import HTTPException
            raise HTTPException(
                status_code=409,
                detail=PromotePaperError(
                    error_code="PROMOTE_CONFLICT",
                    detail="Another promote is already in progress for this candidate",
                    candidate_id=candidate_id,
                ).model_dump(),
            )

        async with lock:
            return await self._promote_to_paper_inner(candidate_id)

    async def _promote_to_paper_inner(self, candidate_id: str) -> PromotePaperResponse:
        from fastapi import HTTPException

        # 1. 取 candidate，不存在 -> 404
        candidate_dict = self._storage.get_strategy_candidate(candidate_id)
        if candidate_dict is None:
            raise HTTPException(status_code=404, detail=f"StrategyCandidate {candidate_id} not found")

        current_status = str(candidate_dict.get("status", "DRAFT"))
        candidate = StrategyCandidate(**candidate_dict)

        # 2. 状态校验：只允许 VALIDATION_PASSED 且非 dev_smoke
        if current_status != "VALIDATION_PASSED":
            raise HTTPException(
                status_code=409,
                detail=PromotePaperError(
                    error_code="INVALID_STATE",
                    current_state=current_status,
                    required_state="VALIDATION_PASSED",
                    detail=f"INVALID_STATE: candidate must be VALIDATION_PASSED (got {current_status})",
                    candidate_id=candidate_id,
                ).model_dump(),
            )

        feature_version = candidate_dict.get("feature_version", "dev_smoke")
        if feature_version == "dev_smoke":
            raise HTTPException(
                status_code=409,
                detail=PromotePaperError(
                    error_code="INVALID_STATE",
                    current_state=current_status,
                    required_state="VALIDATION_PASSED",
                    detail="INVALID_STATE: dev_smoke candidates cannot be promoted to paper",
                    candidate_id=candidate_id,
                ).model_dump(),
            )

        # 3. 写入 PROMOTE_STARTED audit 事件
        self._append_promote_event(candidate_dict, "PROMOTE_STARTED", "promote_started")

        # 4. 构建 deployment_id
        deployment_id = (
            f"{candidate.strategy_id}__promote__{candidate_id[:8]}__paper"
        )

        # 5+6. load -> approve -> audit 在同一补偿作用域内
        # 任何一步失败都触发 _rollback_promote，确保 runtime + deployment 始终干净
        load_succeeded = False
        try:
            await self._load_strategy_runtime(candidate, deployment_id)
            load_succeeded = True

            # approve：更新 candidate 状态
            self._storage.update_strategy_candidate(
                candidate_id, {"status": "APPROVED_FOR_PAPER", "deployment_id": deployment_id}
            )
            updated = self._storage.get_strategy_candidate(candidate_id)
            if updated is None:
                raise RuntimeError("Candidate disappeared after approve")
            self._append_promote_event(updated, "APPROVED_FOR_PAPER", "PROMOTE_COMPLETED")

        except Exception as exc:
            await self._rollback_promote(candidate_dict, deployment_id, str(exc))
            # load 阶段失败 -> PROMOTE_LOAD_FAILED；approve/audit 阶段失败 -> 同样 PROMOTE_LOAD_FAILED
            # 两者对调用方语义相同：运行态没有干净进入 APPROVED_FOR_PAPER
            error_code: str = "PROMOTE_LOAD_FAILED"
            if isinstance(exc, HTTPException):
                # load_strategy 内部已包装成 HTTPException，保留原始错误
                raise HTTPException(
                    status_code=409,
                    detail=PromotePaperError(
                        error_code=error_code,
                        detail=f"PROMOTE_LOAD_FAILED: {exc.detail}",
                        candidate_id=candidate_id,
                    ).model_dump(),
                ) from exc
            raise HTTPException(
                status_code=409,
                detail=PromotePaperError(
                    error_code=error_code,
                    detail=f"PROMOTE_LOAD_FAILED: {exc}",
                    candidate_id=candidate_id,
                ).model_dump(),
            ) from exc

        return PromotePaperResponse(
            candidate_id=candidate_id,
            strategy_id=candidate.strategy_id,
            deployment_id=deployment_id,
            code_version=candidate.code_version,
            status="APPROVED_FOR_PAPER",
            promoted_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _load_strategy_runtime(
        self, candidate: StrategyCandidate, deployment_id: str
    ) -> str:
        """先在持久化层创建 deployment 记录，再动态加载到 StrategyRunner。

        必须先持久化再加载：回滚时需要确认 deployment 是否存在以决定是否清理。
        """
        from trader.api.routes.strategies import LoadStrategyRequest, get_strategy_runner, load_strategy

        symbols = list(
            candidate.config.get("symbols")
            or (candidate.dataset.symbols if candidate.dataset else None)
            or ["BTCUSDT"]
        )
        account_id = str(candidate.config.get("account_id") or "binance_demo")
        venue = str(candidate.config.get("venue") or "BINANCE")

        # 先创建 deployment 持久化记录（回滚时标记 PROMOTE_ROLLED_BACK）
        self._storage.create_deployment(
            {
                "deployment_id": deployment_id,
                "strategy_id": candidate.strategy_id,
                "candidate_id": candidate.candidate_id,
                "code_version": candidate.code_version,
                "symbols": symbols,
                "account_id": account_id,
                "venue": venue,
                "mode": "paper",  # promote-paper 永远是 paper，不能是 live
                "status": "LOADING",
            }
        )

        # 再动态加载到 StrategyRunner runtime
        await load_strategy(
            candidate.strategy_id,
            LoadStrategyRequest(
                deployment_id=deployment_id,
                code_version=candidate.code_version,
                version="v1",
                config=candidate.config,
                symbols=symbols,
                account_id=account_id,
                venue=venue,
                mode="paper",
            ),
        )

        # 加载成功，更新 deployment 状态为 LOADED
        self._storage.update_deployment_status(deployment_id, "LOADED")
        return deployment_id

    async def _rollback_promote(
        self, candidate_dict: dict[str, Any], deployment_id: str, error: str
    ) -> None:
        """补偿事务（严格有序，全程 await）：
        1. unload runtime  2. 标记 deployment  3. 恢复 candidate

        unload 失败不中断后续步骤，但必须记录 warning 和 audit 事件。
        """
        import logging
        from trader.api.routes.strategies import get_strategy_runner

        logger = logging.getLogger(__name__)
        candidate_id = str(candidate_dict["candidate_id"])
        rollback_unload_ok = True

        # 1. await unload runtime（如果已加载进 StrategyRunner）
        try:
            runner = get_strategy_runner()
            info = runner.get_status(deployment_id)
            if info is not None:
                await runner.unload_strategy(deployment_id)
        except Exception as unload_exc:
            rollback_unload_ok = False
            logger.warning(
                "[promote_rollback] unload_strategy failed for %s: %s — "
                "runtime may be in inconsistent state",
                deployment_id,
                unload_exc,
            )
            # 写入 audit，确保 fail-closed 可追溯
            self._storage.append_event(
                {
                    "stream_key": f"strategy_candidate:{candidate_id}",
                    "event_type": "strategy_candidate.promote",
                    "schema_version": 1,
                    "trace_id": f"candidate:{candidate_id}",
                    "ts_ms": _utc_now_ms(),
                    "source": "strategy_candidate_service",
                    "payload": {
                        "candidate_id": candidate_id,
                        "reason": "rollback_runtime_unload_failed",
                        "deployment_id": deployment_id,
                        "unload_error": str(unload_exc),
                    },
                }
            )

        # 2. 标记 deployment 回滚（保留审计）
        dep = self._storage.get_deployment(deployment_id)
        if dep is not None:
            self._storage.update_deployment_status(deployment_id, "PROMOTE_ROLLED_BACK")

        # 3. candidate 回 VALIDATION_PASSED
        self._storage.update_strategy_candidate(
            candidate_id, {"status": "VALIDATION_PASSED", "deployment_id": None}
        )
        updated = self._storage.get_strategy_candidate(candidate_id)
        if updated:
            self._append_promote_event(
                updated,
                "VALIDATION_PASSED",
                "PROMOTE_LOAD_FAILED",
                extra={
                    "error": error,
                    "deployment_id": deployment_id,
                    "rollback_unload_ok": rollback_unload_ok,
                },
            )

    def _append_promote_event(
        self,
        candidate: dict[str, Any],
        to_status: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写入 promote 相关的 audit trail 事件。"""
        candidate_id = str(candidate["candidate_id"])
        payload: dict[str, Any] = {
            "candidate_id": candidate_id,
            "strategy_id": candidate.get("strategy_id"),
            "from_status": candidate.get("status"),
            "to_status": to_status,
            "reason": reason,
            **(extra or {}),
        }
        self._storage.append_event(
            {
                "stream_key": f"strategy_candidate:{candidate_id}",
                "event_type": "strategy_candidate.promote",
                "schema_version": 1,
                "trace_id": f"candidate:{candidate_id}",
                "ts_ms": _utc_now_ms(),
                "source": "strategy_candidate_service",
                "payload": payload,
            }
        )
        events = list(candidate.get("events", []))
        events.append(payload)
        self._storage.update_strategy_candidate(candidate_id, {"events": events})

    def _append_lifecycle_event(
        self,
        candidate: dict[str, Any],
        from_status: str | None,
        to_status: str,
        reason: str,
    ) -> None:
        payload = {
            "candidate_id": candidate["candidate_id"],
            "strategy_id": candidate["strategy_id"],
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
        }
        event = {
            "stream_key": f"strategy_candidate:{candidate['candidate_id']}",
            "event_type": "strategy_candidate.lifecycle",
            "schema_version": 1,
            "trace_id": f"candidate:{candidate['candidate_id']}",
            "ts_ms": _utc_now_ms(),
            "source": "strategy_candidate_service",
            "payload": payload,
        }
        self._storage.append_event(event)
        events = list(candidate.get("events", []))
        events.append(payload)
        self._storage.update_strategy_candidate(str(candidate["candidate_id"]), {"events": events})
