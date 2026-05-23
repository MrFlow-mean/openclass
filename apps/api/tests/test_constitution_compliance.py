from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CORE_PATHS = [ROOT / "apps" / "api" / "app", ROOT / "apps" / "web" / "src"]
FORBIDDEN_CORE_SNIPPETS = (
    "法语",
    "数学",
    "文科",
    "计算机",
    "高考",
    "CSAPP",
    "统计学习理论",
    "勾股定理",
    "直角三角形",
    "欧几里得",
    "【学习精要】",
    "【习题解析】",
    "【补充训练】",
)


def test_core_paths_do_not_contain_subject_or_demo_hardcoding() -> None:
    offenders: list[str] = []
    for base in CORE_PATHS:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".swift"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for snippet in FORBIDDEN_CORE_SNIPPETS:
                if snippet in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {snippet}")

    assert offenders == []
