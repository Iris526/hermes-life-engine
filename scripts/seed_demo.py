#!/usr/bin/env python3
"""为 LifeEngine WebUI 生成可复现的四域演示库。

本脚本只负责 demo 数据编排：尽量通过 LifeEngineRuntime 的公开写入
入口落库，并只在叙事时间戳这类 API 暂未暴露的字段上做受控 SQL 修正。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any


OWNER_KIND = "agent"
OWNER_ID = "default-agent"
AGENT_B_ID = "agent-qing"
AGENT_B_NAME = "青"
TARGET_USER_ID = "ringo"
DEMO_DATE = "2026-07-03"
DEMO_TZ = "Asia/Shanghai"
SOURCE = "demo_seed"


# 演示设定数据：用于 CanonDraft patch，经 commit_canon 进入 active Canon。
# 作用域仅限本脚本创建的 demo agent；重跑时会先比较 active Canon，避免重复提交。
CANON_PATCH: dict[str, Any] = {
    "identity": {
        "name": "凛",
        "role": "待人·画师",
        "self_description": "住在河边小院的现代画师，待人温和，靠河灯会画摊和封面委托生活。",
    },
    "worldview": {
        "world_type": "国风废土河湾镇",
        "raw_world_description": "第七城外缘的河湾镇，旧城灰尘、河灯会、颜料铺和临水小院并存。凛在这里画现代封面，也替集市画摊接小委托。",
        "social_slots": {
            "entity_kinds": {
                "agent": {"label": "生活主体", "description": "LifeEngine 当前演示主体。"},
                "patron": {"label": "香客/主顾", "description": "会来河边画摊买画或托付小画的人。"},
                "merchant": {"label": "邻摊老板", "description": "在集市或巷口经营的熟人商户。"},
                "neighborhood": {"label": "本地圈层", "description": "河湾镇邻里与河灯会摊主圈。"},
            },
            "relationship_axes": {
                "trust": {"label": "信任"},
                "familiarity": {"label": "熟悉"},
                "gratitude": {"label": "感谢"},
            },
            "reputation_axes": {
                "approachable": {"label": "待人温和"},
                "painting_credit": {"label": "画作交付"},
            },
            "evaluation_axes": {
                "kindness": {"label": "待人温和"},
                "craft_finish": {"label": "画面完成度"},
            },
            "rumor_channels": {
                "river_word_of_mouth": {"label": "河边口碑"},
                "pigment_shop_talk": {"label": "颜料铺闲谈"},
                "lantern_market_gossip": {"label": "河灯会闲话"},
            },
        },
    },
    "truth_sources": {
        "bindings": {
            "time": {
                "domain": "time",
                "authority": "system_clock",
                "timezone": DEMO_TZ,
                "time_flow": "real_time",
            },
            "weather": {
                "domain": "weather",
                "authority": "narrative_simulator",
                "mode": "river_town_local",
                "freshness_ttl_minutes": 180,
            },
            "currency": {
                "domain": "currency",
                "authority": "fixed_setting",
                "value": "灵铢",
            },
        },
    },
    "proactive": {"mode": "pending_only"},
    "living": {"skin": "guimingguan"},
}


# 额外画材资源：核心 money/energy/mood/fatigue 由 init_resources 从 guimingguan skin 写入。
# 这些资源仍走 RESOURCE_DEFINE LifeOps，只在缺失时创建，避免重跑重置账户。
PAINTER_SUPPLIES: list[dict[str, Any]] = [
    {
        "key": "supply.paint.ultramarine",
        "display_name": "巷口群青",
        "resource_class": "material",
        "unit": "盒",
        "min_value": 0,
        "max_value": 12,
        "initial": 2,
        "rules": {"purpose": "封面和河灯会夜色"},
    },
    {
        "key": "supply.paper.cover_stock",
        "display_name": "封面厚纸",
        "resource_class": "material",
        "unit": "张",
        "min_value": 0,
        "max_value": 80,
        "initial": 18,
        "rules": {"purpose": "封面收尾和画摊样张"},
    },
    {
        "key": "supply.varnish.river_lamp",
        "display_name": "河灯清漆",
        "resource_class": "material",
        "unit": "瓶",
        "min_value": 0,
        "max_value": 8,
        "initial": 1,
        "rules": {"purpose": "河灯会画摊成品保护"},
    },
]


# 舞台日程：生活节律之外的画师 demo 块，用公开 event/schedule API 创建。
SCHEDULE_BLOCKS: list[dict[str, Any]] = [
    {
        "title": "河边小院封面收尾",
        "description": "把封面边缘、题字和最后一层冷色收住，准备交第一版成品。",
        "event_type": "painting",
        "event_category": "work",
        "activity_domain": "cover_commission",
        "start": "2026-07-03T10:30:00+08:00",
        "end": "2026-07-03T11:40:00+08:00",
        "location": {"name": "河边小院"},
        "tags": ["封面", "收尾", "河边小院"],
        "resource_costs": {"energy": -10, "mood": 4, "supply.paper.cover_stock": -1},
    },
    {
        "title": "巷口颜料铺取群青",
        "description": "去巷口颜料铺取新到的群青，顺手问河灯会摊位的灯色。",
        "event_type": "errand",
        "event_category": "work",
        "activity_domain": "art_supply",
        "start": "2026-07-03T15:25:00+08:00",
        "end": "2026-07-03T16:05:00+08:00",
        "location": {"name": "巷口颜料铺"},
        "tags": ["颜料", "群青", "巷口"],
        "resource_costs": {"energy": -5, "mood": 2, "money.lingzhu": -16, "supply.paint.ultramarine": 1},
    },
    {
        "title": "河灯会画摊委托清单",
        "description": "整理河灯会画摊的小委托尺寸、价目和试摆顺序。",
        "event_type": "planning",
        "event_category": "work",
        "activity_domain": "lantern_stall_commission",
        "start": "2026-07-03T18:35:00+08:00",
        "end": "2026-07-03T19:15:00+08:00",
        "location": {"name": "河灯会集市"},
        "tags": ["河灯会", "画摊", "委托"],
        "resource_costs": {"energy": -6, "mood": 3},
    },
]


# LifeFeed 叙事点：通过现有 API 创建，再用受控 SQL 写入固定 created_at。
DIARY_ENTRIES: list[dict[str, Any]] = [
    {
        "date": "2026-07-01",
        "diary_type": "daily",
        "content": "新颜料到货。巷口颜料铺把群青留在柜台最里层，说河灯会前这色卖得快。我抱着盒子沿河走回来，觉得封面的夜色终于有地方落下去了。",
        "created_at": "2026-07-01T10:20:00+08:00",
    },
    {
        "date": "2026-07-02",
        "diary_type": "daily",
        "content": "封面改到第四版。人物的眼神还是太硬，我把河面的光压暗一点，忽然就温和了。改稿像沿河捡石头，捡到第四颗才知道第一颗为什么不对。",
        "created_at": "2026-07-02T16:30:00+08:00",
    },
    {
        "date": "2026-07-03",
        "diary_type": "daily",
        "content": "封面收尾。最后一笔落在袖口，纸面安静下来。我把画夹扣好，忽然想先交出去，再慢慢改；人也许也是这样活下去的。",
        "created_at": "2026-07-03T16:45:00+08:00",
    },
]


DREAM_ENTRIES: list[dict[str, Any]] = [
    {
        "content": "梦里颜料流成发光的河，群青从画纸边缘漫出去，河灯一盏盏浮起来。有人在对岸喊我别急，先让水把颜色带到该去的地方。",
        "summary": "颜料流成发光的河。",
        "share_text": "我梦见颜料流成发光的河，好像画自己知道要往哪里走。",
        "symbols": ["群青", "发光的河", "河灯"],
        "created_at": "2026-07-02T23:35:00+08:00",
    },
]


SERENDIPITY_EVENTS: list[dict[str, Any]] = [
    {
        "title": "河边旧铜纽扣",
        "description": "在河边小院门口捡到一枚旧铜纽扣，背面像刻着半朵河灯。凛把它夹进画夹，准备做封面角落的暗纹。",
        "serendipity_type": "minor_discovery",
        "intensity": 34,
        "created_at": "2026-07-01T18:10:00+08:00",
        "trigger_title": "河边小院封面收尾",
    },
    {
        "title": "巷口颜料铺群青",
        "description": "巷口颜料铺老板从柜台下摸出最后一盒群青，说这盒适合画水里有灯的夜色。",
        "serendipity_type": "useful_find",
        "intensity": 42,
        "created_at": "2026-07-03T15:55:00+08:00",
        "trigger_title": "巷口颜料铺取群青",
    },
]


PROACTIVE_INTENTS: list[dict[str, Any]] = [
    {
        "intent_type": "idle_share",
        "summary": "刚收尾一张画，想跟你说：袖口那一笔终于安静下来了。",
        "created_at": "2026-07-03T18:05:00+08:00",
    },
    {
        "intent_type": "ask_about_user",
        "summary": "你上次说的面试，后来怎么样了？我刚把画夹扣好，忽然想起这件事。",
        "created_at": "2026-07-03T18:22:00+08:00",
    },
]


# 第二个 demo agent 的现代设定。作用域仅限 constellation 演示库；它通过
# CanonDraft patch 进入 active Canon，不写角色 skin，也不引用凛的世界专名。
B_CANON_PATCH: dict[str, Any] = {
    "identity": {
        "name": AGENT_B_NAME,
        "role": "独立音乐人 / 声音设计师",
        "self_description": "住在现代城市里的独立音乐人和声音设计师，靠现场采样、短片配乐和小型演出生活。",
    },
    "worldview": {
        "world_type": "现代城市创作生活",
        "raw_world_description": "青住在旧厂房改成的城市工作室，白天整理现场录音，夜里给短片和独立演出做声音设计。",
        "social_slots": {
            "entity_kinds": {
                "agent": {"label": "生活主体", "description": "LifeEngine 当前演示主体。"},
                "collaborator": {"label": "创作合作者", "description": "短片、演出或录音项目里的协作对象。"},
                "venue": {"label": "演出/录音场地", "description": "会承载采样、排练或现场演出的现代空间。"},
            },
            "relationship_axes": {
                "trust": {"label": "信任"},
                "creative_sync": {"label": "创作默契"},
            },
            "reputation_axes": {
                "sound_craft": {"label": "声音质感"},
                "reliable_delivery": {"label": "交付稳定"},
            },
            "evaluation_axes": {
                "listening_depth": {"label": "聆听深度"},
                "texture_finish": {"label": "质感完成度"},
            },
            "rumor_channels": {
                "studio_chat": {"label": "工作室闲聊"},
                "venue_backstage": {"label": "后台口碑"},
            },
        },
    },
    "truth_sources": {
        "bindings": {
            "time": {
                "domain": "time",
                "authority": "system_clock",
                "timezone": DEMO_TZ,
                "time_flow": "real_time",
            },
            "weather": {
                "domain": "weather",
                "authority": "narrative_simulator",
                "mode": "modern_city_local",
                "freshness_ttl_minutes": 180,
            },
            "currency": {
                "domain": "currency",
                "authority": "fixed_setting",
                "value": "JPY",
            },
        },
    },
    "proactive": {"mode": "pending_only"},
    "living": {"skin": None},
}


# B 的现代生活资源。通过 RESOURCE_DEFINE LifeOps 写入，保持 skin-free agent 不继承
# default-agent 的角色货币或物资；按 key guard，重跑不重置已有账户余额。
B_RESOURCES: list[dict[str, Any]] = [
    {
        "key": "energy",
        "display_name": "体力",
        "resource_class": "vital",
        "unit": "points",
        "min_value": 0,
        "max_value": 100,
        "initial": 58,
        "rules": {"heartbeat_recovery": 2, "metabolism": -0.04},
    },
    {
        "key": "mood",
        "display_name": "情绪亮度",
        "resource_class": "vital",
        "unit": "points",
        "min_value": -100,
        "max_value": 100,
        "initial": 12,
        "rules": {"heartbeat_recovery": 1},
    },
    {
        "key": "money.jpy",
        "display_name": "日元账户",
        "resource_class": "fungible",
        "unit": "JPY",
        "min_value": 0,
        "max_value": 200000,
        "initial": 42000,
        "rules": {"purpose": "录音、交通和小型演出收入"},
    },
    {
        "key": "focus.deep_listening",
        "display_name": "深听专注",
        "resource_class": "capacity",
        "unit": "points",
        "min_value": 0,
        "max_value": 100,
        "initial": 64,
        "rules": {"purpose": "声音设计和混音判断"},
    },
    {
        "key": "sample.library.roomtone",
        "display_name": "房间底噪采样",
        "resource_class": "material",
        "unit": "段",
        "min_value": 0,
        "max_value": 120,
        "initial": 9,
        "rules": {"purpose": "短片配乐与环境声拼贴"},
    },
]


# B 的 LifeFeed 叙事点。由 diary/proactive 公开 API 写入，再补固定时间戳，
# 让 inter-agent sharing 能从现有 shareable surface 读取，不手写跨 agent 结果。
B_DIARY_ENTRIES: list[dict[str, Any]] = [
    {
        "date": "2026-07-02",
        "diary_type": "daily",
        "content": "下午在旧厂房楼梯间录了三段脚步声。混响很短，像有人把一句话说到一半就停住。我把它标成 roomtone-0702，准备放进短片开场。",
        "created_at": "2026-07-02T21:10:00+08:00",
    },
    {
        "date": "2026-07-03",
        "diary_type": "daily",
        "content": "夜里把一段雨声切成很细的颗粒，铺在合成器下面。它不像雨，更像城市在很低的地方呼吸。",
        "created_at": "2026-07-03T22:05:00+08:00",
    },
]


B_PROACTIVE_INTENTS: list[dict[str, Any]] = [
    {
        "intent_type": "idle_share",
        "summary": "我刚把雨声切成很细的颗粒，忽然觉得城市也会在低频里呼吸。",
        "created_at": "2026-07-03T22:18:00+08:00",
    },
]


SELF_NARRATIVE = {
    "memory_type": "self_narrative",
    "content": "我好像慢慢明白了：先交出去，再慢慢改。画是这样，人也是这样。",
    "created_at": "2026-07-03T17:20:00+08:00",
}


WORLD_PROFILE = {
    "key": "demo.guofeng_wasteland.river_town",
    "title": "第七城外缘的河湾镇",
    "summary": "国风废土里的河边小镇，旧城灰尘和河灯会人声混在一起。",
    "background_text": "第七城外缘没有真正的城墙，只有旧高架、护河符线和被风磨亮的摊棚。凛住在河边小院，白天画现代封面，傍晚为河灯会画摊接小委托。",
    "rules": {
        "map": {
            "canvas": {"width": 1200, "height": 720},
            "tone": "river-town",
        },
        "currency": "灵铢",
    },
}


WORLD_REGIONS: list[dict[str, Any]] = [
    {
        "key": "region.seventh_city_outskirts",
        "name": "第七城外缘",
        "region_type": "outskirts",
        "summary": "旧城与荒原交界，集市、河道和巡灯线都在这里变得松散。",
        "content": "第七城外缘是城规管不到底的地方。白日有灰尘，夜里靠河灯会和旧符线照明。",
        "traits": {"map": {"x": 40, "y": 80, "w": 1080, "h": 560}},
    },
    {
        "key": "region.river_waste_town",
        "name": "河湾镇",
        "region_type": "town",
        "summary": "围着一段慢河长出来的小镇，颜料铺、画摊和河边小院都在这里。",
        "content": "河湾镇的人熟悉彼此的脚步声，也熟悉河灯会前那几天忽然涨起来的买卖。",
        "traits": {"map": {"x": 120, "y": 160, "w": 720, "h": 380}},
    },
]


WORLD_PLACES: list[dict[str, Any]] = [
    {
        "key": "place.river_courtyard",
        "name": "河边小院",
        "place_type": "home_studio",
        "region_key": "region.river_waste_town",
        "summary": "凛临河住着的小院，也是画稿、颜料和旧铜纽扣聚在一起的工作间。",
        "content": "窗外能看见慢河，风里有旧城灰。画桌靠窗，旁边放着封面厚纸和刚开的群青。",
        "coordinates": {"x": 265, "y": 325, "map": {"icon": "home"}},
        "traits": {"quiet": 0.72, "river_view": True},
    },
    {
        "key": "place.pigment_shop",
        "name": "巷口颜料铺",
        "place_type": "shop",
        "region_key": "region.river_waste_town",
        "summary": "窄巷口的小颜料铺，老板会把难买的色留给熟客。",
        "content": "柜台下层常压着几盒好颜色，店里闻得到胶和矿物粉。",
        "coordinates": {"x": 445, "y": 300, "map": {"icon": "shop"}},
        "traits": {"merchant_gossip": True, "supply": ["群青", "清漆", "朱砂墨"]},
    },
    {
        "key": "place.river_lantern_market",
        "name": "河灯会集市",
        "place_type": "market",
        "region_key": "region.river_waste_town",
        "summary": "河灯会前后最热闹的临水集市，画摊、灯摊和小吃摊挤在一起。",
        "content": "入夜后河面反光，摊棚的影子会落到每张画纸上。",
        "coordinates": {"x": 620, "y": 390, "map": {"icon": "market"}},
        "traits": {"crowd": "seasonal", "commission_chance": 0.8},
    },
]


WORLD_CONDITIONS: list[dict[str, Any]] = [
    {
        "key": "condition.river_lantern_crowd_rising",
        "title": "河灯会前人流升温",
        "condition_type": "opportunity",
        "scope_kind": "place",
        "place_key": "place.river_lantern_market",
        "severity": 22,
        "intensity": 68,
        "summary": "河灯会临近，画摊小委托和临时买画的人明显变多。",
        "content": "这会提高画摊收入，也让凛更容易被邻摊和主顾记住。",
        "starts_at": "2026-07-01T00:00:00+08:00",
        "ends_at": "2026-07-05T23:59:00+08:00",
        "payload": {"affects": ["venture", "reputation", "rumors"]},
    },
    {
        "key": "condition.river_dust_wind",
        "title": "河边细灰风",
        "condition_type": "hazard",
        "scope_kind": "place",
        "place_key": "place.river_courtyard",
        "severity": 18,
        "intensity": 34,
        "summary": "河边风里夹着旧城细灰，画纸收尾时要压好边角。",
        "content": "轻微影响画稿保护，但也给夜色和旧铜纽扣带来更贴近本地的质感。",
        "starts_at": "2026-07-02T08:00:00+08:00",
        "ends_at": "2026-07-03T22:00:00+08:00",
        "payload": {"affects": ["cover_commission"]},
    },
]


WORLD_CHRONICLES: list[dict[str, Any]] = [
    {
        "key": "chronicle.demo.lantern_stall_corner",
        "title": "接下河灯会画摊一角",
        "event_type": "local_milestone",
        "era_key": "new_river_town",
        "expansion_key": "demo.week.rin",
        "scope_kind": "place",
        "place_key": "place.river_lantern_market",
        "occurred_at": "2026-07-01",
        "sort_order": 20260701,
        "summary": "凛答应在河灯会集市占半张摊桌，接小幅灯影画和封面样张。",
        "content": "这件事把她的封面委托、颜料补给和邻摊关系连到同一周里。",
        "tags": ["河灯会", "画摊", "营生"],
    },
    {
        "key": "chronicle.demo.old_city_pigment_route",
        "title": "旧城颜料路重新热起来",
        "event_type": "local_history",
        "era_key": "new_river_town",
        "expansion_key": "demo.week.rin",
        "scope_kind": "region",
        "region_key": "region.river_waste_town",
        "occurred_at": "2026-07-02",
        "sort_order": 20260702,
        "summary": "河灯会前，巷口颜料铺重新补了几批矿物色。",
        "content": "群青、朱砂墨和清漆短暂变成镇上画师与摊主的共同话题。",
        "tags": ["颜料铺", "本地史"],
    },
]


SOCIAL_SLOTS: list[dict[str, Any]] = [
    {"slot_type": "entity_kind", "key": "agent", "label": "生活主体"},
    {"slot_type": "entity_kind", "key": "patron", "label": "香客/主顾"},
    {"slot_type": "entity_kind", "key": "merchant", "label": "邻摊老板"},
    {"slot_type": "entity_kind", "key": "neighborhood", "label": "本地圈层"},
    {"slot_type": "relationship_axis", "key": "trust", "label": "信任"},
    {"slot_type": "relationship_axis", "key": "familiarity", "label": "熟悉"},
    {"slot_type": "reputation_axis", "key": "approachable", "label": "待人温和"},
    {"slot_type": "reputation_axis", "key": "painting_credit", "label": "画作交付"},
    {"slot_type": "evaluation_axis", "key": "kindness", "label": "待人温和"},
    {"slot_type": "evaluation_axis", "key": "craft_finish", "label": "画面完成度"},
    {"slot_type": "rumor_channel", "key": "river_word_of_mouth", "label": "河边口碑"},
    {"slot_type": "rumor_channel", "key": "pigment_shop_talk", "label": "颜料铺闲谈"},
    {"slot_type": "rumor_channel", "key": "lantern_market_gossip", "label": "河灯会闲话"},
]


SOCIAL_ENTITIES: list[dict[str, Any]] = [
    {"entity_kind": "agent", "display_name": "凛", "summary": "住在河边小院的待人·画师。"},
    {"entity_kind": "patron", "display_name": "阿照", "summary": "常从河边路过的主顾，喜欢问画纸和灯色。"},
    {"entity_kind": "merchant", "display_name": "叶老板", "summary": "巷口颜料铺老板，熟悉本地画师和摊主。"},
    {"entity_kind": "neighborhood", "display_name": "河灯会摊主圈", "summary": "河灯会临水摊位的临时熟人圈。"},
]


SOCIAL_RUMORS: list[dict[str, Any]] = [
    {
        "content": "河边那位画画的待人温和，讲纸色和灯色都不急。",
        "channel": "river_word_of_mouth",
        "heat": 0.68,
        "credibility": 0.52,
        "sentiment": "positive",
        "effective_at": "2026-07-03T12:25:00+08:00",
    },
    {
        "content": "巷口颜料铺说凛为了河灯会又补了一盒群青。",
        "channel": "pigment_shop_talk",
        "heat": 0.47,
        "credibility": 0.46,
        "sentiment": "neutral",
        "effective_at": "2026-07-03T15:58:00+08:00",
    },
    {
        "content": "河灯会集市已经给凛留了半张桌面，说她的封面样张能压住夜色。",
        "channel": "lantern_market_gossip",
        "heat": 0.54,
        "credibility": 0.41,
        "sentiment": "positive",
        "effective_at": "2026-07-03T19:30:00+08:00",
    },
]


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    输入来自人类或 CI 的脚本参数；输出 argparse Namespace。调用方式为脚本入口
    同步调用；副作用仅为读取 argv。失败时 argparse 打印用法并退出，避免把
    home 误解析成默认目录。
    """
    parser = argparse.ArgumentParser(description="Seed a reproducible LifeEngine WebUI demo database.")
    parser.add_argument("--home", default="/tmp/le_demo", help="Hermes home directory to seed. Default: /tmp/le_demo")
    parser.add_argument("--reset", action="store_true", help="Wipe and recreate --home before seeding.")
    return parser.parse_args()


def configure_home(home: Path, reset: bool) -> Path:
    """准备 HERMES_HOME。

    输入是用户指定目录和 reset 标记；输出解析后的绝对 home。调用方是 main；
    副作用是在 reset 时删除 home，并设置进程级 HERMES_HOME。函数拒绝明显危险的
    根目录，避免误删系统路径。失败会抛出 ValueError 或 OSError，由脚本入口显示。
    """
    resolved = home.expanduser().resolve()
    if reset:
        if resolved == Path("/") or len(resolved.parts) < 3:
            raise ValueError(f"refuse to reset unsafe home: {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)
    resolved.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(resolved)
    return resolved


def dumps_json(value: Any) -> str:
    """生成数据库 JSON 文本。

    输入为脚本内的小型结构化字段；输出 UTF-8 可读 JSON 字符串。调用方是受控 SQL
    时间戳/状态补写路径；无外部副作用。排序键保证重跑时内容稳定，便于人工 diff。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def first(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """读取单行 SQLite 结果。

    输入为只读 SQL 和参数；输出 dict 或 None。调用方是幂等 guard 与摘要计数；
    副作用只读数据库。SQL 均由脚本常量提供，不接受用户拼接片段。
    """
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """读取单个 SQLite 标量。

    输入为只读 SQL 和参数；输出第一列值。调用方是 row-count 摘要和存在性检查；
    副作用只读数据库。无结果时返回 None，便于幂等分支判断。
    """
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def result_from_commit(commit: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """从 LifeOps commit 返回值中取领域结果。

    输入是 Runtime 公开 API 返回的 commit dict 和结果序号；输出单个 result。
    调用方是脚本的 world/social/event 编排。函数不写库；当 API 返回异常形状时
    返回空 dict，让后续校验在引用缺失处显式失败。
    """
    return ((commit.get("results") or [{}])[index].get("result") or {})


def active_canon(conn: Any, owner_id: str = OWNER_ID) -> dict[str, Any]:
    """读取当前 active Canon。

    输入是 Runtime 连接和 agent owner_id；输出 active Canon 数据。调用方是
    Canon 幂等比较；副作用只读数据库。没有 active Canon 时返回空对象，让首次
    setup 路径接管。
    """
    row = first(
        conn,
        "SELECT data_json FROM canon_versions WHERE owner_kind=? AND owner_id=? AND status='active' ORDER BY version DESC LIMIT 1",
        (OWNER_KIND, owner_id),
    )
    if not row:
        return {}
    return json.loads(row.get("data_json") or "{}")


def ensure_canon(rt: Any) -> None:
    """确保默认 agent 的 Canon 已写入凛的人设。

    输入是 LifeEngineRuntime；输出为空。调用方式为 seed 开始阶段同步执行；
    副作用通过 setup/required_settings/commit_canon 写 CanonDraft 和 CanonVersion，
    然后 resume 引擎。若 active Canon 已包含目标 name/role/skin，则跳过提交，
    避免重跑产生重复 Canon 版本。
    """
    canon = active_canon(rt.conn)
    identity = canon.get("identity") or {}
    living = canon.get("living") or {}
    needs_patch = (
        identity.get("name") != "凛"
        or identity.get("role") != "待人·画师"
        or living.get("skin") != "guimingguan"
    )
    if not canon:
        rt.setup("名字是 凛。她是待人·画师，住在河边小院，生活在第七城外缘的国风废土河湾镇。")
        needs_patch = True
    if needs_patch:
        rt.required_settings("patch", patch=CANON_PATCH, source=SOURCE)
        rt.commit_canon()
    rt.control("resume")


def ensure_resources(rt: Any) -> None:
    """初始化核心资源和画材补给资源。

    输入是 Runtime；输出为空。副作用先在缺少 guimingguan 核心资源时调用
    living("init_resources")，再用 RESOURCE_DEFINE LifeOps 写入画师补给。所有资源
    都按 key guard，重跑不重置已有账户余额。
    """
    required = {"money.lingzhu", "energy", "mood", "fatigue"}
    existing = {
        row["resource_key"]
        for row in rt.conn.execute(
            "SELECT resource_key FROM resource_accounts WHERE owner_kind=? AND owner_id=?",
            (OWNER_KIND, OWNER_ID),
        ).fetchall()
    }
    if not required.issubset(existing):
        rt.living("init_resources")
    for supply in PAINTER_SUPPLIES:
        if not scalar(
            rt.conn,
            "SELECT 1 FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (OWNER_KIND, OWNER_ID, supply["key"]),
        ):
            rt.commit_ops([{"type": "RESOURCE_DEFINE", "payload": supply}], source=SOURCE)


def ensure_day_rhythm(rt: Any) -> None:
    """生成 2026-07-03 的生活节律。

    输入是 Runtime；输出为空。副作用通过 living("day_rhythm") 创建事件、
    schedule_blocks、life_rhythm_items 和可能的 proactive intent。函数先按
    owner/date 查 life_rhythm_runs，避免同日重跑触发 schedule overlap。
    """
    exists = scalar(
        rt.conn,
        "SELECT 1 FROM life_rhythm_runs WHERE owner_kind=? AND owner_id=? AND date_key=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, DEMO_DATE),
    )
    if not exists:
        rt.living("day_rhythm", date=DEMO_DATE, timezone=DEMO_TZ)


def event_by_title(rt: Any, title: str) -> dict[str, Any] | None:
    """按标题读取 demo 事件。

    输入是 Runtime 和事件标题；输出最新 matching event 或 None。调用方是日程、
    叙事触发和目标链接的幂等 guard；副作用只读数据库。
    """
    return first(
        rt.conn,
        "SELECT * FROM events WHERE owner_kind=? AND owner_id=? AND title=? ORDER BY created_at DESC LIMIT 1",
        (OWNER_KIND, OWNER_ID, title),
    )


def ensure_schedule_blocks(rt: Any) -> dict[str, str]:
    """创建画师专属日程块。

    输入是 Runtime；输出 title 到 event_id 的映射。副作用通过 event_tool 创建
    events 和 schedule_blocks；重跑会复用已有 title 和同事件排期，不重复插入。
    如果外部已有重叠 schedule，Runtime 会拒绝写入，避免 seed 悄悄破坏真实日程。
    """
    ids: dict[str, str] = {}
    for item in SCHEDULE_BLOCKS:
        event = event_by_title(rt, item["title"])
        if not event:
            event = result_from_commit(
                rt.event_tool(
                    "create",
                    title=item["title"],
                    description=item["description"],
                    event_type=item["event_type"],
                    event_category=item["event_category"],
                    activity_domain=item["activity_domain"],
                    status="planned",
                    priority=72,
                    importance=76,
                    tags=item["tags"],
                    attributes={"demo_seed": True, "date": DEMO_DATE},
                    location=item["location"],
                    resource_costs=item["resource_costs"],
                    source=SOURCE,
                )
            )
        event_id = event["id"]
        ids[item["title"]] = event_id
        block_exists = scalar(
            rt.conn,
            "SELECT 1 FROM schedule_blocks WHERE owner_kind=? AND owner_id=? AND event_id=? AND start=? AND end=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, event_id, item["start"], item["end"]),
        )
        if not block_exists:
            rt.event_tool(
                "schedule",
                event_id=event_id,
                start=item["start"],
                end=item["end"],
                block_type="planned_event",
                timezone_name=DEMO_TZ,
                interruptibility={"level": "soft_interruptible", "max_delay_minutes": 20},
            )
    return ids


def ensure_world(rt: Any) -> dict[str, str]:
    """写入世界档案、区域、地点、状态和编年史。

    输入是 Runtime；输出关键 region/place key 到 id 的映射。副作用全部走
    rt.world(...)，底层转换为 WORLD_* LifeOps。world 表按 key upsert，重跑只更新
    同一记录，不制造重复世界对象。
    """
    if not scalar(
        rt.conn,
        "SELECT 1 FROM world_profiles WHERE owner_kind=? AND owner_id=? AND key=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, WORLD_PROFILE["key"]),
    ):
        rt.world("profile", **WORLD_PROFILE)
    region_ids: dict[str, str] = {}
    for region in WORLD_REGIONS:
        existing = first(
            rt.conn,
            "SELECT * FROM world_regions WHERE owner_kind=? AND owner_id=? AND key=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, region["key"]),
        )
        if existing:
            region_ids[region["key"]] = existing["id"]
        else:
            out = result_from_commit(rt.world("region", **region))
            region_ids[region["key"]] = out["id"]
    place_ids: dict[str, str] = {}
    for place in WORLD_PLACES:
        payload = dict(place)
        region_key = payload.pop("region_key")
        payload["region_id"] = region_ids[region_key]
        existing = first(
            rt.conn,
            "SELECT * FROM world_places WHERE owner_kind=? AND owner_id=? AND key=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, place["key"]),
        )
        if existing:
            place_ids[place["key"]] = existing["id"]
        else:
            out = result_from_commit(rt.world("place", **payload))
            place_ids[place["key"]] = out["id"]
    for condition in WORLD_CONDITIONS:
        if scalar(
            rt.conn,
            "SELECT 1 FROM world_conditions WHERE owner_kind=? AND owner_id=? AND key=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, condition["key"]),
        ):
            continue
        payload = dict(condition)
        place_key = payload.pop("place_key", None)
        region_key = payload.pop("region_key", None)
        if place_key:
            payload["scope_id"] = place_ids[place_key]
        if region_key:
            payload["scope_id"] = region_ids[region_key]
        rt.world("condition", **payload)
    for chronicle in WORLD_CHRONICLES:
        if scalar(
            rt.conn,
            "SELECT 1 FROM world_chronicle_events WHERE owner_kind=? AND owner_id=? AND key=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, chronicle["key"]),
        ):
            continue
        payload = dict(chronicle)
        place_key = payload.pop("place_key", None)
        region_key = payload.pop("region_key", None)
        if place_key:
            payload["scope_id"] = place_ids[place_key]
        if region_key:
            payload["scope_id"] = region_ids[region_key]
        payload["related"] = {"places": list(place_ids.values()), "regions": list(region_ids.values())}
        rt.world("chronicle_event", **payload)
    return {**region_ids, **place_ids}


def ensure_social_entity(rt: Any, entity_kind: str, display_name: str, summary: str) -> dict[str, Any]:
    """按 kind/name 幂等创建社会实体。

    输入是实体 kind、显示名和摘要；输出 world_entities 行。副作用在缺失时通过
    rt.social("create_entity") 写入 LifeOps；已有实体直接复用，避免 create_entity
    无唯一约束导致重跑重复。
    """
    existing = first(
        rt.conn,
        """SELECT * FROM world_entities
           WHERE owner_kind=? AND owner_id=? AND entity_kind=? AND display_name=? AND status='active'
           ORDER BY created_at DESC LIMIT 1""",
        (OWNER_KIND, OWNER_ID, entity_kind, display_name),
    )
    if existing:
        return existing
    return result_from_commit(
        rt.social("create_entity", entity_kind=entity_kind, display_name=display_name, summary=summary, source=SOURCE)
    )


def ensure_social(rt: Any) -> dict[str, str]:
    """写入社会关系、声望、评价和流言。

    输入是 Runtime；输出实体显示名到 id 的映射。副作用使用 rt.social(...) 对槽、
    实体、关系边、声望事件、评价和流言进行公开 API 写入。实体、声望事件、评价、
    流言都有存在性 guard，重跑不会重复堆叠。
    """
    for slot in SOCIAL_SLOTS:
        if not scalar(
            rt.conn,
            "SELECT 1 FROM worldview_slot_definitions WHERE owner_kind=? AND owner_id=? AND slot_type=? AND key=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, slot["slot_type"], slot["key"]),
        ):
            rt.social("define_slot", **slot, source=SOURCE)
    entities = {
        item["display_name"]: ensure_social_entity(rt, item["entity_kind"], item["display_name"], item["summary"])
        for item in SOCIAL_ENTITIES
    }
    rin_id = entities["凛"]["id"]
    patron_id = entities["阿照"]["id"]
    merchant_id = entities["叶老板"]["id"]
    circle_id = entities["河灯会摊主圈"]["id"]

    if not scalar(
        rt.conn,
        """SELECT 1 FROM world_affiliations
           WHERE owner_kind=? AND owner_id=? AND subject_entity_id=? AND faction_entity_id=? AND role=? LIMIT 1""",
        (OWNER_KIND, OWNER_ID, rin_id, circle_id, "painter_vendor"),
    ):
        rt.social("link_affiliation", subject_entity_id=rin_id, faction_entity_id=circle_id, role="painter_vendor", strength=0.72)
    if not scalar(
        rt.conn,
        """SELECT 1 FROM social_edges
           WHERE owner_kind=? AND owner_id=? AND source_entity_id=? AND target_entity_id=? AND axis=? LIMIT 1""",
        (OWNER_KIND, OWNER_ID, patron_id, rin_id, "trust"),
    ):
        rt.social("set_edge", source_entity_id=patron_id, target_entity_id=rin_id, axis="trust", value=38, confidence=0.7)
    if not scalar(
        rt.conn,
        """SELECT 1 FROM social_edges
           WHERE owner_kind=? AND owner_id=? AND source_entity_id=? AND target_entity_id=? AND axis=? LIMIT 1""",
        (OWNER_KIND, OWNER_ID, merchant_id, rin_id, "familiarity"),
    ):
        rt.social("set_edge", source_entity_id=merchant_id, target_entity_id=rin_id, axis="familiarity", value=52, confidence=0.82)

    reputation_specs = [
        {
            "axis": "approachable",
            "delta": 18,
            "reason": "给主顾解释画纸和灯色时很耐心",
            "evidence_id": "demo.rep.approachable.20260703",
            "effective_at": "2026-07-03T12:35:00+08:00",
        },
        {
            "axis": "painting_credit",
            "delta": 21,
            "reason": "封面收尾按时交出，河灯会样张也准备妥当",
            "evidence_id": "demo.rep.painting_credit.20260703",
            "effective_at": "2026-07-03T17:10:00+08:00",
        },
    ]
    for spec in reputation_specs:
        if not scalar(
            rt.conn,
            "SELECT 1 FROM reputation_events WHERE owner_kind=? AND owner_id=? AND evidence_id=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, spec["evidence_id"]),
        ):
            rt.social(
                "reputation_event",
                subject_entity_id=rin_id,
                audience_entity_id=circle_id,
                evidence_kind="demo_seed",
                evidence={"date": DEMO_DATE},
                **spec,
            )

    if not scalar(
        rt.conn,
        """SELECT 1 FROM social_evaluations
           WHERE owner_kind=? AND owner_id=? AND evaluator_entity_id=? AND subject_entity_id=? AND axis=? AND reason=? LIMIT 1""",
        (OWNER_KIND, OWNER_ID, patron_id, rin_id, "kindness", "问价时解释得清楚，也没有催人。"),
    ):
        rt.social(
            "evaluate",
            evaluator_entity_id=patron_id,
            subject_entity_id=rin_id,
            axis="kindness",
            score=41,
            reason="问价时解释得清楚，也没有催人。",
            evidence={"source": "demo_seed", "date": DEMO_DATE},
        )
    if not scalar(
        rt.conn,
        """SELECT 1 FROM social_evaluations
           WHERE owner_kind=? AND owner_id=? AND evaluator_entity_id=? AND subject_entity_id=? AND axis=? AND reason=? LIMIT 1""",
        (OWNER_KIND, OWNER_ID, merchant_id, rin_id, "craft_finish", "封面第四色压得稳，像能上摊的样张。"),
    ):
        rt.social(
            "evaluate",
            evaluator_entity_id=merchant_id,
            subject_entity_id=rin_id,
            axis="craft_finish",
            score=36,
            reason="封面第四色压得稳，像能上摊的样张。",
            evidence={"source": "demo_seed", "date": DEMO_DATE},
        )

    for rumor in SOCIAL_RUMORS:
        if not scalar(
            rt.conn,
            "SELECT 1 FROM rumors WHERE owner_kind=? AND owner_id=? AND channel=? AND content=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, rumor["channel"], rumor["content"]),
        ):
            rt.social("rumor", subject_entity_id=rin_id, target_kind="entity", target_id=rin_id, visibility="local", **rumor)
    return {name: entity["id"] for name, entity in entities.items()}


def ensure_goal_and_venture(rt: Any) -> None:
    """写入可选目标和营生，点亮后续 backlog/read-side。

    输入是 Runtime；输出为空。副作用使用 rt.goals、CREATE_GOAL_MILESTONE
    LifeOp、UPDATE_GOAL_PROGRESS 和 rt.activity 创建目标、里程碑、进度与营生。
    所有非 upsert 表按标题或 reason guard，重跑不重复创建。
    """
    goal = first(
        rt.conn,
        "SELECT * FROM goals WHERE owner_kind=? AND owner_id=? AND title=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, "完成河灯会的画摊委托"),
    )
    if not goal:
        goal = result_from_commit(
            rt.goals(
                "create",
                title="完成河灯会的画摊委托",
                description="把封面样张、现场小幅画和半张摊桌准备好，河灯会当晚能稳定接待主顾。",
                goal_type="creative_work",
                priority=86,
                progress=35,
                target_date="2026-07-05T23:00:00+08:00",
                metrics={"deliverables": ["封面样张", "小幅灯影画", "摊位价目"]},
            )
        )
    goal_id = goal["id"]
    if not scalar(
        rt.conn,
        "SELECT 1 FROM goal_milestones WHERE owner_kind=? AND owner_id=? AND goal_id=? AND title=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, goal_id, "完成封面样张和摊位价目"),
    ):
        rt.commit_ops(
            [
                {
                    "type": "CREATE_GOAL_MILESTONE",
                    "payload": {
                        "goal_id": goal_id,
                        "title": "完成封面样张和摊位价目",
                        "description": "先把能给主顾看的样张和价目写清楚。",
                        "target_progress": 65,
                        "due_at": "2026-07-04T18:00:00+08:00",
                    },
                }
            ],
            source=SOURCE,
        )
    if not scalar(
        rt.conn,
        "SELECT 1 FROM goal_progress_entries WHERE owner_kind=? AND owner_id=? AND goal_id=? AND reason=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, goal_id, "封面收尾完成，河灯会画摊清单已列出。"),
    ):
        rt.goals("progress", goal_id=goal_id, progress=48, reason="封面收尾完成，河灯会画摊清单已列出。", source=SOURCE)

    if not scalar(
        rt.conn,
        "SELECT 1 FROM ventures WHERE owner_kind=? AND owner_id=? AND title=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, "净符/画摊营生"),
    ):
        rt.activity(
            "register",
            title="净符/画摊营生",
            description="河灯会前后在临水集市接小幅灯影画、净符边饰和封面样张相关委托。",
            activity_type="creative_stall",
            event_category="work",
            activity_domain="venture",
            cadence_kind="weekly",
            weekdays=[4],
            start_time="19:30",
            end_time="22:00",
            timezone=DEMO_TZ,
            resource_costs={"energy": -16, "mood": 4, "money.lingzhu": 24},
            importance=68,
            priority=70,
            start_date="2026-07-03",
            location="河灯会集市",
            tags=["净符", "画摊", "河灯会"],
        )


def update_created_at(conn: Any, table: str, row_id: str, created_at: str) -> None:
    """给 API 创建的叙事行补固定时间戳。

    输入为表名、主键和目标 created_at；输出为空。调用方是 LifeFeed 叙事 seed；
    副作用只更新白名单叙事表的 created_at/updated_at。这样保留公开 API 的写入
    账本，又让 demo 时间线可复现。未知表会直接失败，避免任意 SQL 更新。
    """
    if table not in {"diary_entries", "dream_entries", "serendipity_events", "proactive_intents", "memories"}:
        raise ValueError(f"timestamp update table is not allowed: {table}")
    conn.execute(f"UPDATE {table} SET created_at=? WHERE id=?", (created_at, row_id))
    if table == "proactive_intents":
        conn.execute("UPDATE proactive_intents SET updated_at=?, queued_at=COALESCE(queued_at, ?) WHERE id=?", (created_at, created_at, row_id))


def ensure_diary(rt: Any) -> None:
    """写入三条日记叙事。

    输入是 Runtime；输出为空。副作用用 rt.diary("write") 创建 diary_entries，
    再更新固定 created_at。按 date/type/content guard，重跑不重复。
    """
    for item in DIARY_ENTRIES:
        row = first(
            rt.conn,
            """SELECT * FROM diary_entries
               WHERE owner_kind=? AND owner_id=? AND diary_type=? AND date=? AND content=? LIMIT 1""",
            (OWNER_KIND, OWNER_ID, item["diary_type"], item["date"], item["content"]),
        )
        if not row:
            rt.diary(
                "write",
                diary_type=item["diary_type"],
                date=item["date"],
                content=item["content"],
                privacy="safe_to_share",
            )
            row = first(
                rt.conn,
                """SELECT * FROM diary_entries
                   WHERE owner_kind=? AND owner_id=? AND diary_type=? AND date=? AND content=? LIMIT 1""",
                (OWNER_KIND, OWNER_ID, item["diary_type"], item["date"], item["content"]),
            )
        if row:
            update_created_at(rt.conn, "diary_entries", row["id"], item["created_at"])


def ensure_dreams(rt: Any) -> None:
    """写入固定梦境叙事。

    输入是 Runtime；输出为空。副作用通过 CREATE_DREAM_ENTRY LifeOp 写梦境和梦境
    memory，再修正 dream_entries.created_at。按 content guard，重跑不重复。
    """
    for item in DREAM_ENTRIES:
        row = first(
            rt.conn,
            "SELECT * FROM dream_entries WHERE owner_kind=? AND owner_id=? AND content=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, item["content"]),
        )
        if not row:
            out = result_from_commit(
                rt.commit_ops(
                    [
                        {
                            "type": "CREATE_DREAM_ENTRY",
                            "payload": {
                                "content": item["content"],
                                "summary": item["summary"],
                                "share_text": item["share_text"],
                                "symbols": item["symbols"],
                                "truth_layer": "dream_symbolic",
                                "privacy": "safe_to_share",
                                "status": "created",
                                "source": SOURCE,
                            },
                        }
                    ],
                    source=SOURCE,
                )
            )
            row = first(rt.conn, "SELECT * FROM dream_entries WHERE id=?", (out.get("id"),))
        if row:
            update_created_at(rt.conn, "dream_entries", row["id"], item["created_at"])
            if row.get("memory_id"):
                update_created_at(rt.conn, "memories", row["memory_id"], item["created_at"])


def ensure_serendipity(rt: Any, event_ids: dict[str, str]) -> None:
    """写入两条偶遇叙事。

    输入是 Runtime 和可引用的 demo event ids；输出为空。副作用通过
    CREATE_SERENDIPITY_EVENT LifeOp 创建 serendipity event，再修正 created_at。按
    title/description guard，重跑不重复。
    """
    for item in SERENDIPITY_EVENTS:
        row = first(
            rt.conn,
            "SELECT * FROM serendipity_events WHERE owner_kind=? AND owner_id=? AND title=? AND description=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, item["title"], item["description"]),
        )
        if not row:
            trigger_event_id = event_ids.get(item["trigger_title"])
            out = result_from_commit(
                rt.commit_ops(
                    [
                        {
                            "type": "CREATE_SERENDIPITY_EVENT",
                            "payload": {
                                "title": item["title"],
                                "description": item["description"],
                                "serendipity_type": item["serendipity_type"],
                                "intensity": item["intensity"],
                                "trigger_event_id": trigger_event_id,
                                "emotional_impact": {"mood": 3, "inspiration": 8},
                                "proposed_ops": [],
                                "source": SOURCE,
                            },
                        }
                    ],
                    source=SOURCE,
                )
            )
            row = first(rt.conn, "SELECT * FROM serendipity_events WHERE id=?", (out.get("id"),))
        if row:
            update_created_at(rt.conn, "serendipity_events", row["id"], item["created_at"])


def ensure_proactive_intents(rt: Any) -> None:
    """写入两条待说 proactive intent。

    输入是 Runtime；输出为空。副作用通过 rt.proactive("create") 写入 LifeOps，
    然后固定 created_at/queued_at。按 intent_type/summary/target guard，重跑不重复。
    """
    for item in PROACTIVE_INTENTS:
        row = first(
            rt.conn,
            """SELECT * FROM proactive_intents
               WHERE agent_id=? AND target_type='user' AND target_id=? AND intent_type=? AND summary=? LIMIT 1""",
            (OWNER_ID, TARGET_USER_ID, item["intent_type"], item["summary"]),
        )
        if not row:
            out = result_from_commit(
                rt.proactive(
                    "create",
                    target_type="user",
                    target_id=TARGET_USER_ID,
                    intent_type=item["intent_type"],
                    summary=item["summary"],
                    emotional_tone="warm",
                    importance=66,
                    urgency=24,
                    novelty=52,
                    relationship_relevance=70,
                    privacy_level="safe_to_share",
                    status="queued",
                    delivery_policy={"mode": "pending_only", "demo_seed": True},
                )
            )
            row = first(rt.conn, "SELECT * FROM proactive_intents WHERE id=?", (out.get("id"),))
        if row:
            update_created_at(rt.conn, "proactive_intents", row["id"], item["created_at"])


def ensure_self_narrative(rt: Any) -> None:
    """写入反思型 self_narrative memory。

    输入是 Runtime；输出为空。副作用通过 rt.memory("remember") 创建 memory 和索引，
    再固定 created_at。按 memory_type/content guard，重跑不重复。
    """
    row = first(
        rt.conn,
        "SELECT * FROM memories WHERE owner_kind=? AND owner_id=? AND memory_type=? AND content=? LIMIT 1",
        (OWNER_KIND, OWNER_ID, SELF_NARRATIVE["memory_type"], SELF_NARRATIVE["content"]),
    )
    if not row:
        rt.memory(
            "remember",
            content=SELF_NARRATIVE["content"],
            memory_type=SELF_NARRATIVE["memory_type"],
            source=SOURCE,
            importance=68,
            emotional_weight=14,
            confidence=0.95,
        )
        row = first(
            rt.conn,
            "SELECT * FROM memories WHERE owner_kind=? AND owner_id=? AND memory_type=? AND content=? LIMIT 1",
            (OWNER_KIND, OWNER_ID, SELF_NARRATIVE["memory_type"], SELF_NARRATIVE["content"]),
        )
    if row:
        update_created_at(rt.conn, "memories", row["id"], SELF_NARRATIVE["created_at"])


def ensure_life_feed(rt: Any, event_ids: dict[str, str]) -> None:
    """写入生活日志域的全部叙事来源。

    输入是 Runtime 和日程事件 id 映射；输出为空。副作用优先使用 Runtime/ LifeOps
    写入 diary、dream、serendipity、proactive、memory 和 social rumor，再用时间戳
    UPDATE 保证三天内的可复现叙事顺序。
    """
    ensure_diary(rt)
    ensure_dreams(rt)
    ensure_serendipity(rt, event_ids)
    ensure_proactive_intents(rt)
    ensure_self_narrative(rt)


def ensure_second_agent_canon(rt: Any) -> None:
    """确保第二个现代 agent 的 Canon 已提交且没有角色 skin。

    输入是 LifeEngineRuntime；输出为空。调用方式为 seed 编排同步调用；副作用通过
    setup/required_settings/commit_canon/control 写入 `agent-qing` 的 active Canon
    和运行态。重跑时按 identity/role/living.skin 做幂等比较，只在设定不匹配时
    提交新 Canon 版本。
    """
    canon = active_canon(rt.conn, AGENT_B_ID)
    identity = canon.get("identity") or {}
    living = canon.get("living") or {}
    needs_patch = (
        identity.get("name") != AGENT_B_NAME
        or identity.get("role") != "独立音乐人 / 声音设计师"
        or bool(living.get("skin"))
    )
    if not canon:
        rt.setup(
            "名字是 青。她是现代城市里的独立音乐人和声音设计师，货币用日元。",
            OWNER_KIND,
            AGENT_B_ID,
        )
        needs_patch = True
    if needs_patch:
        rt.required_settings("patch", OWNER_KIND, AGENT_B_ID, patch=B_CANON_PATCH, source=SOURCE)
        rt.commit_canon(OWNER_KIND, AGENT_B_ID)
    rt.control("resume", OWNER_KIND, AGENT_B_ID, reason="demo seed constellation")


def ensure_second_agent_resources(rt: Any) -> None:
    """写入第二个 agent 的现代资源账户。

    输入是 Runtime；输出为空。副作用通过 RESOURCE_DEFINE LifeOps 为 `agent-qing`
    建立体力、情绪、日元、深听专注和采样库资源。函数按 resource_key 检查账户，
    已存在时跳过，避免 reset 之外的重跑覆盖人类浏览后的余额变化。
    """
    for resource in B_RESOURCES:
        if not scalar(
            rt.conn,
            "SELECT 1 FROM resource_accounts WHERE owner_kind=? AND owner_id=? AND resource_key=?",
            (OWNER_KIND, AGENT_B_ID, resource["key"]),
        ):
            rt.commit_ops([{"type": "RESOURCE_DEFINE", "payload": resource}], OWNER_KIND, AGENT_B_ID, source=SOURCE)


def ensure_second_agent_diary(rt: Any) -> None:
    """写入第二个 agent 的可分享日记。

    输入是 Runtime；输出为空。副作用通过 rt.diary("write") 创建 diary_entries，
    再固定 created_at。按 owner/date/type/content guard，确保重跑不会重复制造
    sharing 来源。
    """
    for item in B_DIARY_ENTRIES:
        row = first(
            rt.conn,
            """SELECT * FROM diary_entries
               WHERE owner_kind=? AND owner_id=? AND diary_type=? AND date=? AND content=? LIMIT 1""",
            (OWNER_KIND, AGENT_B_ID, item["diary_type"], item["date"], item["content"]),
        )
        if not row:
            rt.diary(
                "write",
                owner_id=AGENT_B_ID,
                diary_type=item["diary_type"],
                date=item["date"],
                content=item["content"],
                privacy="safe_to_share",
            )
            row = first(
                rt.conn,
                """SELECT * FROM diary_entries
                   WHERE owner_kind=? AND owner_id=? AND diary_type=? AND date=? AND content=? LIMIT 1""",
                (OWNER_KIND, AGENT_B_ID, item["diary_type"], item["date"], item["content"]),
            )
        if row:
            update_created_at(rt.conn, "diary_entries", row["id"], item["created_at"])


def ensure_second_agent_proactive(rt: Any) -> None:
    """写入第二个 agent 的 idle_share 讲述。

    输入是 Runtime；输出为空。副作用通过 rt.proactive("create") 建立 safe_to_share
    idle_share intent，再固定 created_at/queued_at。调用方是 constellation seed；
    这些 intent 只作为现有 inter-agent sharing 的读取来源，不直接写入其它 agent。
    """
    for item in B_PROACTIVE_INTENTS:
        row = first(
            rt.conn,
            """SELECT * FROM proactive_intents
               WHERE agent_id=? AND target_type='user' AND target_id=? AND intent_type=? AND summary=? LIMIT 1""",
            (AGENT_B_ID, TARGET_USER_ID, item["intent_type"], item["summary"]),
        )
        if not row:
            out = result_from_commit(
                rt.proactive(
                    "create",
                    owner_id=AGENT_B_ID,
                    target_type="user",
                    target_id=TARGET_USER_ID,
                    intent_type=item["intent_type"],
                    summary=item["summary"],
                    emotional_tone="focused",
                    importance=62,
                    urgency=18,
                    novelty=58,
                    relationship_relevance=52,
                    privacy_level="safe_to_share",
                    status="queued",
                    delivery_policy={"mode": "pending_only", "demo_seed": True},
                )
            )
            row = first(rt.conn, "SELECT * FROM proactive_intents WHERE id=?", (out.get("id"),))
        if row:
            update_created_at(rt.conn, "proactive_intents", row["id"], item["created_at"])


def ensure_second_agent_life(rt: Any) -> None:
    """编排第二个 agent 的最小可见生活。

    输入是 Runtime；输出为空。副作用依次写入 skin-free Canon、现代资源、可分享
    diary 和 idle_share。调用方是 seed_demo；函数不写世界 peer/rumor，跨 agent
    可见性只由后续 `ensure_constellation_sharing` 通过现有 sharing path 完成。
    """
    ensure_second_agent_canon(rt)
    ensure_second_agent_resources(rt)
    ensure_second_agent_diary(rt)
    ensure_second_agent_proactive(rt)


def ensure_inter_agent_gates(rt: Any) -> None:
    """开启两个 demo agent 的 inter_agent gate。

    输入是 Runtime；输出为空。副作用通过 control module API 写入 controls 的
    module_gates_json。调用方是 seed_demo，在 sharing 前执行；单 agent 安装默认
    仍由 DEFAULT_MODULE_GATES 保持 off。
    """
    for owner_id in (OWNER_ID, AGENT_B_ID):
        rt.control("module", OWNER_KIND, owner_id, key="inter_agent", value="on")


def ensure_constellation_sharing(rt: Any) -> dict[str, Any]:
    """运行现有跨 Agent 分享路径，让双方世界出现 peer rumor。

    输入是 Runtime；输出 sharing/delivery 摘要。副作用只调用
    `run_inter_agent_sharing_for_tick` 与 `deliver_inter_agent`：先从双方公开的
    idle_share/diary surface 读取讲述并入队，再由 delivery 写入接收方社会世界的
    `peer_agent` 与 `rumor_unverified`。函数不直接 INSERT/UPDATE 任何跨 agent
    目标表，保持 truth-layering 合同。
    """
    from lifeengine.canon import ensure_control
    from lifeengine.inter_agent import deliver_inter_agent, run_inter_agent_sharing_for_tick

    results: dict[str, Any] = {}
    for owner_id in (OWNER_ID, AGENT_B_ID):
        control = ensure_control(rt.conn, OWNER_KIND, owner_id)
        results[owner_id] = run_inter_agent_sharing_for_tick(
            rt.conn,
            (OWNER_KIND, owner_id),
            control=control,
            limit=2,
            recent_hours=24 * 365 * 20,
            deliver_limit=10,
        )
    results["final_delivery"] = deliver_inter_agent(rt.conn, limit=20)
    return results


def table_count(conn: Any, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    """统计单表行数。

    输入是表名、可选 WHERE 片段和参数；输出整数行数。调用方是最终摘要与自测断言；
    副作用只读数据库。表名来自脚本常量，不接受用户输入。
    """
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(scalar(conn, sql, params) or 0)


def build_summary(rt: Any, owner_id: str = OWNER_ID) -> dict[str, dict[str, int]]:
    """生成四域 row-count 摘要。

    输入是 Runtime 和 agent owner_id；输出按 WebUI 四域组织的计数字典。调用方是
    脚本 stdout 和自测；副作用只读数据库。计数覆盖本次 demo 需要点亮的主要
    read-side 表，便于人类确认切换 agent 后每个观测面都有数据。
    """
    conn = rt.conn
    return {
        "舞台": {
            "resource_accounts": table_count(conn, "resource_accounts", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "life_rhythm_items": table_count(conn, "life_rhythm_items", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "schedule_blocks_today": table_count(conn, "schedule_blocks", "owner_kind=? AND owner_id=? AND start LIKE ?", (OWNER_KIND, owner_id, f"{DEMO_DATE}%")),
            "pending_proactive": table_count(conn, "proactive_intents", "agent_id=? AND status IN ('generated','queued')", (owner_id,)),
        },
        "生活日志": {
            "diary_entries": table_count(conn, "diary_entries", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "dream_entries": table_count(conn, "dream_entries", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "serendipity_events": table_count(conn, "serendipity_events", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "self_narrative_memories": table_count(conn, "memories", "owner_kind=? AND owner_id=? AND memory_type='self_narrative'", (OWNER_KIND, owner_id)),
            "rumors": table_count(conn, "rumors", "owner_kind=? AND owner_id=? AND status='active'", (OWNER_KIND, owner_id)),
        },
        "世界": {
            "world_profiles": table_count(conn, "world_profiles", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "world_regions": table_count(conn, "world_regions", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "world_places": table_count(conn, "world_places", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "world_conditions": table_count(conn, "world_conditions", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "world_chronicle_events": table_count(conn, "world_chronicle_events", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "world_entities": table_count(conn, "world_entities", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "social_edges": table_count(conn, "social_edges", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "reputation_accounts": table_count(conn, "reputation_accounts", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "rumors": table_count(conn, "rumors", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
        },
        "系统": {
            "canon_versions": table_count(conn, "canon_versions", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "life_transactions": table_count(conn, "life_transactions", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "life_ops": table_count(conn, "life_ops", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "goals": table_count(conn, "goals", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
            "ventures": table_count(conn, "ventures", "owner_kind=? AND owner_id=?", (OWNER_KIND, owner_id)),
        },
    }


def build_cross_agent_counts(rt: Any) -> dict[str, Any]:
    """统计 constellation 交叉可见性。

    输入是 Runtime；输出每个 agent 接收到的 peer_agent 与 rumor_unverified 数量，
    以及 outbox 状态计数。调用方是 seed stdout、自测和健康断言；副作用只读数据库。
    计数只看 inter_agent delivery 写入的目标形状，不参与任何跨 agent 写入。
    """
    conn = rt.conn
    per_agent = {}
    for owner_id in (OWNER_ID, AGENT_B_ID):
        per_agent[owner_id] = {
            "peer_agent": table_count(
                conn,
                "world_entities",
                "owner_kind=? AND owner_id=? AND entity_kind='peer_agent' AND status='active'",
                (OWNER_KIND, owner_id),
            ),
            "rumor_unverified": table_count(
                conn,
                "rumors",
                "owner_kind=? AND owner_id=? AND target_kind='inter_agent_outbox' AND truth_layer='rumor_unverified' AND status='active'",
                (OWNER_KIND, owner_id),
            ),
        }
    return {
        "per_agent": per_agent,
        "outbox_delivered": table_count(conn, "inter_agent_outbox", "status='delivered'"),
        "outbox_queued": table_count(conn, "inter_agent_outbox", "status='queued'"),
        "outbox_failed": table_count(conn, "inter_agent_outbox", "status='failed'"),
    }


def assert_demo_health(summary: dict[str, dict[str, int]]) -> None:
    """对 demo seed 做轻量健康断言。

    输入是 build_summary 的结果；输出为空。调用方是 seed 末尾的自检；副作用无。
    如果任一 WebUI 主要域仍是空墙，会抛 AssertionError，让脚本在自测或人工运行时
    直接失败。
    """
    checks = {
        "舞台.resource_accounts": summary["舞台"]["resource_accounts"] >= 7,
        "舞台.life_rhythm_items": summary["舞台"]["life_rhythm_items"] >= 1,
        "舞台.schedule_blocks_today": summary["舞台"]["schedule_blocks_today"] >= 3,
        "生活日志.diary_entries": summary["生活日志"]["diary_entries"] >= 3,
        "生活日志.dream_entries": summary["生活日志"]["dream_entries"] >= 1,
        "生活日志.serendipity_events": summary["生活日志"]["serendipity_events"] >= 2,
        "生活日志.rumors": summary["生活日志"]["rumors"] >= 3,
        "世界.world_places": summary["世界"]["world_places"] >= 3,
        "世界.world_chronicle_events": summary["世界"]["world_chronicle_events"] >= 2,
        "世界.reputation_accounts": summary["世界"]["reputation_accounts"] >= 1,
        "系统.canon_versions": summary["系统"]["canon_versions"] >= 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise AssertionError("demo seed incomplete: " + ", ".join(failed))


def assert_constellation_health(summaries: dict[str, dict[str, dict[str, int]]], cross: dict[str, Any]) -> None:
    """检查第二 agent 与双向 constellation 可见性。

    输入是每个 agent 的四域摘要和 cross-agent 计数；输出为空。调用方是 seed 末尾
    自检；副作用无。失败时抛 AssertionError，避免演示库看似 seeded 但 roster 或
    双向 rumor 实际缺席。
    """
    b = summaries.get(AGENT_B_ID) or {}
    cross_per_agent = (cross.get("per_agent") or {})
    checks = {
        f"{AGENT_B_ID}.canon_versions": (b.get("系统") or {}).get("canon_versions", 0) >= 1,
        f"{AGENT_B_ID}.resource_accounts": (b.get("舞台") or {}).get("resource_accounts", 0) >= 3,
        f"{AGENT_B_ID}.diary_entries": (b.get("生活日志") or {}).get("diary_entries", 0) >= 2,
        f"{AGENT_B_ID}.pending_proactive": (b.get("舞台") or {}).get("pending_proactive", 0) >= 1,
        f"{OWNER_ID}.peer_agent": (cross_per_agent.get(OWNER_ID) or {}).get("peer_agent", 0) >= 1,
        f"{OWNER_ID}.rumor_unverified": (cross_per_agent.get(OWNER_ID) or {}).get("rumor_unverified", 0) >= 1,
        f"{AGENT_B_ID}.peer_agent": (cross_per_agent.get(AGENT_B_ID) or {}).get("peer_agent", 0) >= 1,
        f"{AGENT_B_ID}.rumor_unverified": (cross_per_agent.get(AGENT_B_ID) or {}).get("rumor_unverified", 0) >= 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise AssertionError("constellation seed incomplete: " + ", ".join(failed))


def print_summary(home: Path, db: Path, summaries: dict[str, dict[str, dict[str, int]]], cross: dict[str, Any]) -> None:
    """打印人类可读的 seed 摘要。

    输入是 home、db 路径、每个 agent 的四域计数和跨 agent 计数；输出到 stdout。
    调用方是 main；副作用仅为打印。文案保持短小，方便用户复制命令后快速确认
    WebUI roster、双方生活和 peer rumor 都已落地。
    """
    print(f"Seeded LifeEngine demo home: {home}")
    print(f"DB: {db}")
    for owner_id, summary in summaries.items():
        name = AGENT_B_NAME if owner_id == AGENT_B_ID else "凛"
        print(f"Agent {owner_id} ({name}):")
        for domain, counts in summary.items():
            joined = ", ".join(f"{key}={value}" for key, value in counts.items())
            print(f"  {domain}: {joined}")
    cross_parts = []
    for owner_id, counts in (cross.get("per_agent") or {}).items():
        cross_parts.append(
            f"{owner_id}: peer_agent={counts.get('peer_agent', 0)}, rumor_unverified={counts.get('rumor_unverified', 0)}"
        )
    cross_parts.append(
        "outbox="
        f"delivered:{cross.get('outbox_delivered', 0)}, queued:{cross.get('outbox_queued', 0)}, failed:{cross.get('outbox_failed', 0)}"
    )
    print("跨 Agent: " + "; ".join(cross_parts))


def seed_demo(home: Path, reset: bool) -> dict[str, Any]:
    """执行完整 demo seed。

    输入是 home 路径和 reset 标记；输出包含 per-agent 四域 row-count 与跨 agent
    计数的摘要。副作用包括设置 HERMES_HOME、可选删除 home、创建/迁移 SQLite DB，
    并通过 Runtime 公开 API 写入 demo 数据。函数不修改生产代码，也不访问
    /tmp/le_demo 以外的默认目录，除非调用方显式传入其它 home。
    """
    configured_home = configure_home(home, reset)
    from lifeengine.paths import db_path
    from lifeengine.runtime import LifeEngineRuntime

    rt = LifeEngineRuntime()
    try:
        ensure_canon(rt)
        ensure_resources(rt)
        world_ids = ensure_world(rt)
        if not world_ids:
            raise AssertionError("world ids were not seeded")
        event_ids = ensure_schedule_blocks(rt)
        ensure_day_rhythm(rt)
        ensure_social(rt)
        ensure_goal_and_venture(rt)
        ensure_life_feed(rt, event_ids)
        ensure_second_agent_life(rt)
        ensure_inter_agent_gates(rt)
        sharing = ensure_constellation_sharing(rt)
        summaries = {
            OWNER_ID: build_summary(rt, OWNER_ID),
            AGENT_B_ID: build_summary(rt, AGENT_B_ID),
        }
        cross = build_cross_agent_counts(rt)
        assert_demo_health(summaries[OWNER_ID])
        assert_constellation_health(summaries, cross)
        print_summary(configured_home, db_path(), summaries, cross)
        return {"agents": summaries, "cross_agent": cross, "sharing": sharing}
    finally:
        rt.close()


def main() -> None:
    """脚本入口。

    输入来自命令行；输出为进程退出码和 stdout 摘要。调用方式是直接执行本文件；
    副作用委托给 seed_demo。异常不吞掉，让 CI/人工自测能看到真实失败栈。
    """
    args = parse_args()
    seed_demo(Path(args.home), args.reset)


if __name__ == "__main__":
    main()
