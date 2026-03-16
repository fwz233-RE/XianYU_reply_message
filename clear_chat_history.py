import argparse
import sqlite3
from pathlib import Path


TARGET_TABLES = ("messages", "chat_bargain_counts", "chat_trigger_rules")


def table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def clear_history_for_db(db_path: Path) -> bool:
    if not db_path.exists():
        print(f"[SKIP] 未找到数据库: {db_path}")
        return False

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()
            deleted_any = False

            for table_name in TARGET_TABLES:
                if table_exists(cursor, table_name):
                    cursor.execute(f"DELETE FROM {table_name}")
                    print(f"[OK] 已清空表 {table_name}: {db_path}")
                    deleted_any = True
                else:
                    print(f"[SKIP] 表不存在 {table_name}: {db_path}")

            if table_exists(cursor, "sqlite_sequence"):
                cursor.execute(
                    "DELETE FROM sqlite_sequence WHERE name IN (?, ?, ?)",
                    TARGET_TABLES,
                )

            conn.commit()
            return deleted_any
    except Exception as exc:
        print(f"[ERROR] 清空失败 {db_path}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="清空闲鱼助手历史对话")
    parser.add_argument(
        "--targets",
        nargs="*",
        default=["chrome", "edge", "legacy"],
        help="要清理的目标: chrome edge legacy",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    target_paths: list[Path] = []

    normalized_targets = {item.strip().lower() for item in args.targets if item.strip()}

    if "chrome" in normalized_targets:
        target_paths.append(project_dir / "data" / "chrome" / "chat_history.db")
    if "edge" in normalized_targets:
        target_paths.append(project_dir / "data" / "edge" / "chat_history.db")
    if "legacy" in normalized_targets:
        target_paths.append(project_dir / "data" / "chat_history.db")

    if not target_paths:
        print("[ERROR] 未指定有效 targets，可用值: chrome edge legacy")
        return 1

    print("将清空以下数据库中的历史对话：")
    for path in target_paths:
        print(f" - {path}")

    confirm = input("确认继续吗？输入 YES 继续：").strip()
    if confirm != "YES":
        print("已取消。")
        return 0

    success_count = 0
    for db_path in target_paths:
        if clear_history_for_db(db_path):
            success_count += 1

    print(f"完成。成功处理 {success_count}/{len(target_paths)} 个数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
