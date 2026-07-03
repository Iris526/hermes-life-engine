"""Canon skin data for optional character/world presets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


# 默认旧 agent 的 living skin 名称：只作为 Canon 引用值使用，不代表 universal default。
DEFAULT_LEGACY_LIVING_SKIN = "guimingguan"


# Canon skin 数据结构：表示一套可选角色内容包，承载 living 节律、资源和供给物资。
# 作用域仅限 Canon 明确引用的角色 skin；生命周期随代码版本发布，读取方不得把它
# 当作 universal default 写入 DEFAULT_CANON_TEMPLATE；业务调用方是 living 层和
# 默认 agent 兼容迁移路径；约束是所有角色文案只能放在这里或用户 Canon 中。
# 字段约定：resources.definitions 是 init_resources 的 RESOURCE_DEFINE 来源；
# living.supplies 是后续 collection bootstrap 可消费的供给物资；living.rhythm_templates
# 用 start_time/end_time 按当天日期物化；living.rhythm_proactive_summary 只有声明时才
# 允许 runtime 创建 rhythm summary，不存在时不得生成通用替代角色文案。
CANON_SKINS: dict[str, dict[str, Any]] = {
    "guimingguan": {
        "resources": {
            "definitions": [
                {"key": "money.lingzhu", "display_name": "灵铢", "resource_class": "currency", "unit": "枚", "min_value": 0, "max_value": None, "initial": 120},
                {"key": "daily_cost.lingzhu", "display_name": "每日基础开销", "resource_class": "currency", "unit": "枚/日", "min_value": 0, "max_value": None, "initial": 8},
                {"key": "commission_income.lingzhu", "display_name": "委托收入累计", "resource_class": "currency", "unit": "枚", "min_value": 0, "max_value": None, "initial": 0},
                {"key": "energy", "display_name": "精力", "resource_class": "vital", "unit": "points", "min_value": 0, "max_value": 100, "initial": 60, "rules": {"heartbeat_recovery": 3, "metabolism": -0.06}},
                {"key": "mood", "display_name": "心情", "resource_class": "vital", "unit": "points", "min_value": -100, "max_value": 100, "initial": 0, "rules": {"heartbeat_recovery": 1, "metabolism": -0.01}},
                {"key": "fatigue", "display_name": "疲劳", "resource_class": "vital", "unit": "points", "min_value": 0, "max_value": 100, "initial": 20, "rules": {"heartbeat_recovery": -2, "metabolism": 0.05}},
            ],
        },
        "living": {
            "supplies": [
                {"name": "符纸", "quantity": 24, "attributes": {"category": "daily_supply", "is_consumable": True, "material": "黄纸朱砂", "purpose": "净符和小委托"}},
                {"name": "朱砂墨", "quantity": 1, "attributes": {"category": "daily_supply", "is_consumable": True, "material": "朱砂", "purpose": "画符"}},
                {"name": "线香", "quantity": 18, "attributes": {"category": "daily_supply", "is_consumable": True, "material": "檀香", "purpose": "供奉、净场"}},
                {"name": "铜铃", "quantity": 1, "attributes": {"category": "tool", "is_consumable": False, "material": "铜", "purpose": "仪式法器"}},
                {"name": "小型结界仪", "quantity": 1, "attributes": {"category": "tool", "is_consumable": False, "material": "金属/灵子回路", "purpose": "结界检测"}},
                {"name": "归明观钥匙", "quantity": 1, "attributes": {"category": "tool", "is_consumable": False, "material": "铜", "purpose": "开门"}},
                {"name": "委托记录册", "quantity": 1, "attributes": {"category": "book", "is_consumable": False, "material": "纸", "purpose": "记录委托"}},
            ],
            "rhythm_templates": [
                {"title": "归明观晨巡与开观", "start_time": "07:30", "end_time": "08:05", "event_type": "routine", "event_category": "maintenance", "activity_domain": "temple_morning", "resource_costs": {"energy": -4, "mood": 2}, "tags": ["晨巡", "开观", "归明观"], "worth_diary": False},
                {"title": "打扫香案并补符纸", "start_time": "08:20", "end_time": "08:55", "event_type": "temple_chores", "event_category": "maintenance", "activity_domain": "altar_upkeep", "resource_costs": {"energy": -5, "mood": 1}, "tags": ["香案", "符纸", "日常"], "worth_diary": False},
                {"title": "检查小型结界工具包", "start_time": "09:40", "end_time": "10:15", "event_type": "inspection", "event_category": "work", "activity_domain": "barrier_tools", "resource_costs": {"energy": -5}, "tags": ["结界仪", "工具包"], "worth_diary": False},
                {"title": "接一个低风险净符委托", "start_time": "13:30", "end_time": "15:00", "event_type": "commission", "event_category": "work", "activity_domain": "low_risk_talisman_commission", "resource_costs": {"energy": -18, "mood": 2}, "tags": ["小委托", "净符", "十二城"], "worth_diary": True, "worth_proactive": True},
                {"title": "傍晚记账与灵铢收支整理", "start_time": "17:40", "end_time": "18:10", "event_type": "bookkeeping", "event_category": "finance", "activity_domain": "temple_accounts", "resource_costs": {"energy": -4}, "tags": ["记账", "灵铢"], "worth_diary": True},
                {"title": "写一张给 Ringo 的小纸条草稿", "start_time": "21:30", "end_time": "21:45", "event_type": "proactive_note", "event_category": "relationship", "activity_domain": "pending_share", "resource_costs": {"mood": 1, "energy": -2}, "tags": ["Ringo", "小纸条", "pending"], "worth_proactive": True},
            ],
            "rhythm_proactive_summary": "我给今天折了几张归明观的小日程纸条：晨巡、香案、工具包、小委托和傍晚记账。",
        },
    },
}


SKIN_ALIASES: dict[str, str] = {
    "mingdeng": DEFAULT_LEGACY_LIVING_SKIN,
    "taoist_temple": DEFAULT_LEGACY_LIVING_SKIN,
}


def get_canon_skin(name: str | None) -> dict[str, Any]:
    """读取 Canon skin 数据。

    输入是 Canon 中声明的 skin 名称或历史别名；输出是可安全修改的深拷贝，
    未命中时返回空 dict。调用方式为 living 层同步读取；调用方包括默认
    agent 兼容路径、life_living 手动动作和 heartbeat 日节律。函数不写库、
    不写全局状态，失败语义是空 skin，保证未知 Canon 不回退到任何角色内容。
    """
    if not isinstance(name, str) or not name.strip():
        return {}
    key = name.strip()
    key = SKIN_ALIASES.get(key, key)
    return deepcopy(CANON_SKINS.get(key) or {})
