"""长期记忆对账 —— 校准 SQLite（事实源）与 qdrant（索引）之间的不一致

为什么需要它（P1-5）：两个存储没有共享事务，"先动向量再提交 SQLite" 的写路径一旦中途崩溃，
会留下两类不一致：生效行丢了向量（永久召不回，还不报错）或孤儿向量（白占空间）。
顺序怎么调都堵不住，只能定期拿事实源去校准索引。

用法：
    PYTHONPATH=. .venv/Scripts/python scripts/reconcile_memory.py --dry-run   # 只看不改
    PYTHONPATH=. .venv/Scripts/python scripts/reconcile_memory.py             # 真修
    PYTHONPATH=. .venv/Scripts/python scripts/reconcile_memory.py --user 1    # 只对一个用户
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.core.memory.store import reconcile_memory   # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(description="长期记忆 SQLite ↔ qdrant 对账")
    ap.add_argument("--dry-run", action="store_true", help="只报告不一致，不写任何东西")
    ap.add_argument("--user", type=int, default=None, help="只对账某个用户（默认全部）")
    args = ap.parse_args()

    report = await reconcile_memory(user_id=args.user, dry_run=args.dry_run)
    tag = "（dry-run，未改动）" if report["dry_run"] else ""
    print(f"检查生效记忆 {report['rows']} 条{tag}")
    print(f"  补回向量（行在、索引丢了）: {report['repaired']} 条")
    print(f"  清理孤儿向量（索引在、行没了）: {report['orphans']} 个")
    print(f"  向量化失败（文本仍保留，下次重试）: {report['failed']} 条")


if __name__ == "__main__":
    asyncio.run(main())
