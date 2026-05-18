from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_session_learn_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "session_learn.py"
    spec = importlib.util.spec_from_file_location("session_learn", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_auto_uses_explicit_skill_without_repo_side_effects(tmp_path, monkeypatch):
    session_learn = _load_session_learn_module()
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(session_learn, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(session_learn, "SKILLS_META_DIR", skills_dir / "_meta")
    monkeypatch.setattr(session_learn, "EXPERIENCES_DIR", skills_dir / "_experiences")

    session_file = tmp_path / "session.md"
    session_file.write_text(
        "\n".join(
            [
                "Bug:",
                "effective_qty was used without None check",
                "Fix:",
                "guard effective_qty before position sizing",
            ]
        ),
        encoding="utf-8",
    )

    result = session_learn.cmd_extract(
        SimpleNamespace(file=str(session_file), auto=True, skill="backtesting")
    )

    assert result == 0
    experience_file = skills_dir / "_experiences" / "backtesting.yaml"
    assert experience_file.exists()
    content = experience_file.read_text(encoding="utf-8")
    assert "skill: backtesting" in content
    assert "pattern: effective_qty_none_check" in content
