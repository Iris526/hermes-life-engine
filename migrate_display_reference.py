#!/usr/bin/env python3
"""一次性手工迁移脚本（未挂载到 CLI/upgrade 入口）。

运行方式（需 lifeengine 在 sys.path 上）：
    PYTHONPATH=/path/to/parent python -m lifeengine.migrate_display_reference

把旧 asset_bundle 结构转换到新的 display_image + reference_image 模型。

旧结构：
  {
    "primary_image": <path>,        # 通常是穿着图/立绘
    "reference_assets": [{asset_type, asset_uri, ...}],
    "requirements": [...],
    "status": "...",
    "generated_from_rule": {...}
  }

新结构：
  {
    "display_image": <path>,        # 封面/展示图（穿着立绘）
    "reference_image": <path>,      # 参考图（complex asset sheet）
    "status": "...",
    "generated_from_rule": {...}
  }

转换逻辑：
  - 旧 primary_image → display_image（它通常是穿着立绘）
  - 旧 reference_assets 中 asset_type 为 material_sheet/complex_sheet 的 → reference_image
  - 如果 reference_image 为空但 primary_image 是穿着图，display_image = primary_image
  - 如果 display_image 为空但找到了任何 asset_uri → display_image = 那个 uri
  - 旧 requirements 被丢弃
  - status: 如果 display_image 或 reference_image 有值 → "available"，否则 "needs_generation"
"""

import json
import sqlite3
import sys
from pathlib import Path


def find_db() -> str:
    """找到 LifeEngine DB 路径。"""
    candidates = [
        Path.home() / ".hermes" / "lifeengine.db",
        Path.home() / ".hermes" / "life" / "lifeengine.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # 让用户指定
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists():
            return str(p)
    print("ERROR: 找不到 lifeengine.db，请指定路径：python migrate_display_reference.py <path>")
    sys.exit(1)


def migrate_bundle(old: dict) -> dict:
    """转换单个 asset_bundle。"""
    if not old or not isinstance(old, dict):
        return {"display_image": None, "reference_image": None, "status": "needs_generation"}

    # 如果已经是新格式，跳过
    if "display_image" in old and "reference_image" in old and "primary_image" not in old:
        return old

    primary = old.get("primary_image")
    ref_assets = old.get("reference_assets") or []
    rule = old.get("generated_from_rule")

    # 从 reference_assets 中找 sheet 图
    ref_image = None
    sheet_types = {"material_sheet", "complex_asset_sheet", "complex_sheet", "single_complex_sheet"}
    for a in ref_assets:
        if isinstance(a, dict):
            atype = a.get("asset_type", "")
            uri = a.get("asset_uri")
            if uri and atype in sheet_types:
                ref_image = uri
                break

    # 如果没有专门的 sheet，从 reference_assets 找任何非 primary 的 uri
    if not ref_image:
        for a in ref_assets:
            if isinstance(a, dict):
                uri = a.get("asset_uri")
                if uri and uri != primary:
                    ref_image = uri
                    break

    display_image = primary  # 旧 primary_image 通常是穿着立绘

    has_any = bool(display_image or ref_image)
    new_bundle = {
        "display_image": display_image,
        "reference_image": ref_image,
        "status": "available" if has_any else "needs_generation",
    }
    if rule:
        new_bundle["generated_from_rule"] = rule
    return new_bundle


def main():
    db_path = find_db()
    print(f"DB: {db_path}")

    # 先备份
    backup_path = db_path + ".bak.pre_display_reference"
    import shutil
    shutil.copy2(db_path, backup_path)
    print(f"备份: {backup_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, owner_kind, owner_id, name, asset_bundle_json FROM collection_items"
    ).fetchall()

    migrated = 0
    skipped = 0
    for row in rows:
        old_json = row["asset_bundle_json"]
        try:
            old = json.loads(old_json) if old_json else {}
        except json.JSONDecodeError:
            old = {}

        # 检查是否已经是新格式
        if "display_image" in old and "primary_image" not in old:
            skipped += 1
            continue

        new = migrate_bundle(old)
        new_json = json.dumps(new, ensure_ascii=False)

        conn.execute(
            "UPDATE collection_items SET asset_bundle_json=?, updated_at=datetime('now') WHERE id=?",
            (new_json, row["id"]),
        )
        migrated += 1
        print(f"  ✓ {row['name']}: display={new.get('display_image')}, reference={new.get('reference_image')}")

    conn.commit()
    conn.close()
    print(f"\n完成：迁移 {migrated} 条，跳过 {skipped} 条（已是新格式）")


if __name__ == "__main__":
    main()
