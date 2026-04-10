"""
Artifact Storage - Backtest Report Artifact Storage
=================================================
Provides storage for backtest reports including equity_curve, returns, risk, trades data.

当前实现使用文件存储，未来可迁移到 S3/GCS 等对象存储。
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List


class ArtifactStorage:
    """
    回测报告产物存储。
    
    存储结构：
        artifact_ref = "backtest_report:{run_id}"
        完整报告数据包括: returns, risk, trades, equity_curve
    """
    
    def __init__(self, base_path: Optional[str] = None):
        """
        初始化产物存储。
        
        Args:
            base_path: 存储根路径，默认为 ./artifacts
        """
        self._base_path = Path(base_path) if base_path else Path("artifacts")
        self._base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_report_path(self, run_id: str) -> Path:
        """获取报告文件路径"""
        return self._base_path / "backtest_reports" / f"{run_id}.json"
    
    def save_report(
        self,
        run_id: str,
        returns: Optional[Dict[str, Any]] = None,
        risk: Optional[Dict[str, Any]] = None,
        trades: Optional[List[Dict[str, Any]]] = None,
        equity_curve: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        保存回测报告。
        
        Args:
            run_id: 回测运行 ID
            returns: 收益率指标
            risk: 风险指标
            trades: 交易列表
            equity_curve: 权益曲线
            metadata: 元信息
            
        Returns:
            artifact_ref 字符串
        """
        report_path = self._get_report_path(run_id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        
        report_data = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "returns": returns,
            "risk": risk,
            "trades": trades,
            "equity_curve": equity_curve,
            "metadata": metadata or {},
        }
        
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        return f"backtest_report:{run_id}"
    
    def load_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        加载回测报告。
        
        Args:
            run_id: 回测运行 ID
            
        Returns:
            报告数据字典，如果不存在返回 None
        """
        report_path = self._get_report_path(run_id)
        
        if not report_path.exists():
            return None
        
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def delete_report(self, run_id: str) -> bool:
        """
        删除回测报告。
        
        Args:
            run_id: 回测运行 ID
            
        Returns:
            是否成功删除
        """
        report_path = self._get_report_path(run_id)
        
        if report_path.exists():
            report_path.unlink()
            return True
        return False


# 全局产物存储单例
_artifact_storage: Optional[ArtifactStorage] = None


def get_artifact_storage() -> ArtifactStorage:
    """获取全局产物存储实例"""
    global _artifact_storage
    if _artifact_storage is None:
        # 支持从环境变量配置存储路径
        base_path = os.environ.get("ARTIFACT_STORAGE_PATH")
        _artifact_storage = ArtifactStorage(base_path=base_path)
    return _artifact_storage
