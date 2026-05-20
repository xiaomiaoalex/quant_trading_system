"""
QuantStats Tearsheet Generator
================================
Post-processes equity_curve into a full HTML performance tearsheet.
Not on the critical backtest path — called async after completion.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_tearsheet(
    equity_curve: list[dict[str, Any]],
    run_id: str,
    strategy_name: str = "Strategy",
    output_path: str | None = None,
) -> str | None:
    """
    Generate a QuantStats HTML tearsheet from an equity_curve list.

    Args:
        equity_curve: list of {timestamp: int_ms, equity: float}
        run_id: used to name the output file if output_path not given
        strategy_name: display name in the report title
        output_path: override file path; defaults to tempfile.gettempdir()/tearsheet_{run_id}.html

    Returns:
        Absolute path to the generated HTML file, or None if generation failed.
    """
    if not equity_curve or len(equity_curve) < 10:
        logger.info("Skipping tearsheet for run %s: equity_curve too short (%d points)", run_id, len(equity_curve or []))
        return None

    try:
        import pandas as pd
        import quantstats as qs

        df = pd.DataFrame(equity_curve)
        df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("ts").sort_index()
        returns = df["equity"].pct_change().dropna()

        if returns.empty:
            return None

        path = output_path or str(Path(tempfile.gettempdir()) / f"tearsheet_{run_id}.html")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        qs.reports.html(
            returns,
            output=path,
            title=f"{strategy_name} — {run_id[:8]}",
            download_filename=f"tearsheet_{run_id[:8]}.html",
        )
        logger.info("Tearsheet generated: %s", path)
        return path

    except ImportError:
        logger.warning("quantstats not installed — tearsheet skipped for run %s", run_id)
        return None
    except Exception as exc:
        logger.warning("Tearsheet generation failed for run %s: %s", run_id, exc)
        return None
