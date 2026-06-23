from __future__ import annotations

from pathlib import Path


def test_portfolio_allocation_defaults_to_long_only() -> None:
    source = Path("Frontend/src/pages/PortfolioAllocation.tsx").read_text(encoding="utf-8")

    assert "const [allowShort, setAllowShort] = useState(false)" in source
