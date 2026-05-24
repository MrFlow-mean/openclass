from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


API_BASE_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = API_BASE_DIR.parents[1]
DATA_DIR = API_BASE_DIR / "data"


def load_root_dotenv() -> None:
    root_env = ROOT_DIR / ".env"
    if root_env.exists():
        load_dotenv(root_env)
        return
    load_dotenv()
