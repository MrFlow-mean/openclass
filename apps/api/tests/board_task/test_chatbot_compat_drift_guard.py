from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = ROOT / "scripts" / "check_chatbot_compat_drift.py"


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("check_chatbot_compat_drift", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chatbot_compatibility_symbols_do_not_drift() -> None:
    guard = _load_guard_module()

    errors = guard.check_repo(ROOT)

    assert errors == []
