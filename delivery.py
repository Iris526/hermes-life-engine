"""主动消息外部投递桥。

LifeEngine 核心只负责产生 ``proactive_outbox``，真正把消息送到 QQ、
飞书或其它 IM 属于宿主/服务器边界。本模块把这条边界显式化：读取
queued outbox，调用可配置的 command / webhook / stdout 适配器，成功
后再把 outbox 标记为 sent。默认关闭，避免开发机或未配置服务器误发。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import urllib.request
from typing import Any

from .db import transaction
from .jsonutil import dumps
from .proactive import _gate_policy, _get_canon_policy, mark_outbox_sent, quiet_hours_status
from .trace import append_journal, new_id

_DELIVERY_MODES = {"off", "command", "webhook", "stdout"}


def _json_dict(value: str | None) -> dict[str, Any]:
    """把外部适配器返回文本解析为 JSON 对象。

    输入来自 command stdout 或 webhook body；解析失败、空值或非对象 JSON 都按
    空对象处理。调用方只把它作为审计补充，不依赖它决定是否已送达。
    """
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _mode_from(payload: dict[str, Any] | None = None) -> str:
    """解析投递模式并收敛到受支持枚举。

    输入优先级为调用参数高于环境变量；输出只允许 off、command、webhook、
    stdout。未知值降级为 off，避免服务器误配置时意外外推。
    """
    mode = str((payload or {}).get("delivery_mode") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_MODE") or "off").strip().lower()
    return mode if mode in _DELIVERY_MODES else "off"


def _timeout_from(payload: dict[str, Any] | None = None) -> float:
    """解析外部投递超时。

    输入来自调用参数或环境变量，单位为秒；输出限制在 1 到 120 秒之间。
    command/webhook 调用方使用该值中断卡住的外部发送，失败会记录 attempt。
    """
    raw = (payload or {}).get("delivery_timeout_seconds") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_TIMEOUT") or 15
    try:
        return max(1.0, min(float(raw), 120.0))
    except Exception:
        return 15.0


def delivery_config_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回主动消息投递配置的可审计摘要。

    输入来自 ``life_proactive(action="deliver")`` 的显式参数和服务器环境变量；
    输出只暴露模式、是否可用、是否配置了 command/webhook 等非敏感摘要，供
    doctor、CLI 和运维面板展示。不会读取数据库、不会发网络请求，也不会泄漏
    webhook 完整 URL。默认 ``off`` 表示核心仍只排队，不对外推送。
    """
    payload = payload or {}
    mode = _mode_from(payload)
    command = str(payload.get("delivery_command") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_COMMAND") or "").strip()
    webhook = str(payload.get("webhook_url") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_WEBHOOK_URL") or "").strip()
    enabled = mode in {"command", "webhook", "stdout"}
    ready = (mode == "stdout") or (mode == "command" and bool(command)) or (mode == "webhook" and bool(webhook))
    return {
        "mode": mode,
        "enabled": enabled and ready,
        "command_configured": bool(command),
        "webhook_configured": bool(webhook),
        "timeout_seconds": _timeout_from(payload),
    }


def _due_outbox(conn, agent_id: str, limit: int) -> list[dict[str, Any]]:
    """读取当前可投递的 queued outbox。

    输入是 Agent id 和批量上限；只返回 ``send_after`` 已到期或未设置的 queued
    消息。函数只读数据库，主要服务 dry-run 预览；真实投递必须走
    ``_claim_due_outbox`` 的原子占用，避免多个 worker 同时外发同一条消息。
    """
    rows = conn.execute(
        """SELECT o.* FROM proactive_outbox o
              LEFT JOIN proactive_intents i ON i.id=o.intent_id
              WHERE o.agent_id=? AND o.status='queued'
                AND (o.send_after IS NULL OR o.send_after <= datetime('now'))
                AND (o.intent_id IS NULL OR i.status IN ('generated','queued'))
              ORDER BY o.created_at ASC
              LIMIT ?""",
        (agent_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _defer_due_outbox_for_quiet_hours(conn, agent_id: str, limit: int) -> dict[str, Any]:
    """Push due queued outbox past the user's quiet-hours window.

    This is a delivery-boundary guard: evaluation may have queued a message
    before bedtime, but an external QQ/webhook worker can run later. If quiet
    hours are currently active, due rows get ``send_after`` set to the window
    end and no adapter is invoked.
    """
    policy = _gate_policy(None, _get_canon_policy(conn, agent_id))
    quiet = quiet_hours_status(policy)
    if not quiet.get("active") or not quiet.get("next_allowed_at"):
        return {"active": False, "deferred_count": 0, "outbox_ids": [], "next_allowed_at": None, "timezone": quiet.get("timezone")}
    rows = _due_outbox(conn, agent_id, max(1, int(limit)))
    outbox_ids = [r["id"] for r in rows]
    with transaction(conn):
        for outbox_id in outbox_ids:
            conn.execute(
                """UPDATE proactive_outbox
                      SET send_after=?, error=NULL
                    WHERE id=? AND agent_id=? AND status='queued'""",
                (quiet["next_allowed_at"], outbox_id, agent_id),
            )
    if outbox_ids:
        append_journal(
            conn,
            "agent",
            agent_id,
            "proactive_outbox_deferred_for_quiet_hours",
            {"outbox_ids": outbox_ids, "next_allowed_at": quiet.get("next_allowed_at"), "timezone": quiet.get("timezone")},
            "proactive_delivery",
        )
    return {
        "active": True,
        "deferred_count": len(outbox_ids),
        "outbox_ids": outbox_ids,
        "next_allowed_at": quiet.get("next_allowed_at"),
        "timezone": quiet.get("timezone"),
    }


def _reap_stale_delivery_claims(conn, agent_id: str, stale_minutes: float = 30.0) -> dict[str, Any]:
    """回收卡在 delivering 的旧投递占用。

    输入是 Agent id 与过期分钟数，调用方是 delivery worker。它只回收超过
    command/webhook 超时上限许多倍的 running attempt：把 attempt 标 failed，
    并把 outbox 从 delivering 放回 queued 以便后续重试。这个补偿路径处理进程
    崩溃或服务器重启后的半占用；活跃发送不应超过默认 30 分钟。
    """
    stale = f"-{max(1.0, float(stale_minutes)):g} minutes"
    error = "delivery claim timed out before completion"
    with transaction(conn):
        rows = conn.execute(
            """SELECT o.id AS outbox_id, d.id AS attempt_id
                 FROM proactive_outbox o
                 JOIN proactive_deliveries d ON d.outbox_id=o.id
                WHERE o.agent_id=? AND o.status='delivering'
                  AND d.status='running' AND d.created_at <= datetime('now', ?)
                ORDER BY d.created_at ASC""",
            (agent_id, stale),
        ).fetchall()
        outbox_ids = [r["outbox_id"] for r in rows]
        attempt_ids = [r["attempt_id"] for r in rows]
        for attempt_id in attempt_ids:
            conn.execute(
                """UPDATE proactive_deliveries
                      SET status='failed', error=?, completed_at=datetime('now')
                    WHERE id=? AND status='running'""",
                (error, attempt_id),
            )
        for outbox_id in outbox_ids:
            conn.execute(
                "UPDATE proactive_outbox SET status='queued', error=? WHERE id=? AND status='delivering'",
                (error, outbox_id),
            )
    return {"requeued_count": len(outbox_ids), "outbox_ids": outbox_ids, "attempt_ids": attempt_ids}


def _intent_summary(conn, intent_id: str | None) -> str | None:
    """读取 intent 摘要作为消息文本兜底。

    输入是可空 intent_id；当 outbox 缺少 draft_text/message_text 时使用。
    只读数据库，不改变 intent/outbox 状态。
    """
    if not intent_id:
        return None
    row = conn.execute("SELECT summary FROM proactive_intents WHERE id=?", (intent_id,)).fetchone()
    return row["summary"] if row else None


def _payload_for(conn, row: dict[str, Any], channel: str) -> dict[str, Any]:
    """构造交给外部投递器的稳定 JSON 载荷。

    载荷是服务器 command/webhook 的公共合同：只包含 outbox、目标用户、
    文本和渠道等必要字段，不包含私密 Canon、内部 trace 或数据库连接信息。
    调用方可以把它转成 QQ、飞书、短信等实际平台请求；字段缺失时必须按
    JSON 兼容空值处理。
    """
    text = row.get("draft_text") or row.get("message_text") or row.get("summary") or _intent_summary(conn, row.get("intent_id")) or ""
    return {
        "outbox_id": row.get("id"),
        "intent_id": row.get("intent_id"),
        "agent_id": row.get("agent_id"),
        "target_user_id": row.get("target_user_id"),
        "message_text": text,
        "draft_text": row.get("draft_text"),
        "delivery_channel": channel,
        "created_at": row.get("created_at"),
    }


def _claim_due_outbox(conn, agent_id: str, limit: int, channel_override: str | None = None) -> list[dict[str, Any]]:
    """原子占用一批待投递 outbox。

    输入为 Agent id、批量上限和可选渠道覆盖。函数在一个 SQLite 写事务内读取
    queued outbox、写入 ``proactive_deliveries(status='running')``，并把 outbox
    更新为 ``delivering``。只有成功占用的调用方才允许进行外部 command/webhook
    副作用；并发 worker 会被 BEGIN IMMEDIATE 串行化，后到者不会重复发送。
    """
    claimed: list[dict[str, Any]] = []
    with transaction(conn):
        rows = conn.execute(
            """SELECT o.* FROM proactive_outbox o
                  LEFT JOIN proactive_intents i ON i.id=o.intent_id
                  WHERE o.agent_id=? AND o.status='queued'
                    AND (o.send_after IS NULL OR o.send_after <= datetime('now'))
                    AND (o.intent_id IS NULL OR i.status IN ('generated','queued'))
                  ORDER BY o.created_at ASC
                  LIMIT ?""",
            (agent_id, int(limit)),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            channel = str(channel_override or row.get("delivery_channel") or "command")
            msg_payload = _payload_for(conn, row, channel)
            attempt_id = new_id("prodel")
            conn.execute(
                """INSERT INTO proactive_deliveries(
                     id, outbox_id, intent_id, agent_id, target_user_id, status,
                     delivery_channel, payload_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    row.get("id"),
                    row.get("intent_id"),
                    row.get("agent_id"),
                    row.get("target_user_id"),
                    "running",
                    channel,
                    dumps(msg_payload),
                ),
            )
            updated = conn.execute(
                """UPDATE proactive_outbox
                      SET status='delivering', delivery_channel=?, error=NULL
                    WHERE id=? AND agent_id=? AND status='queued'""",
                (channel, row["id"], agent_id),
            ).rowcount
            if updated:
                claimed.append({"row": row, "attempt_id": attempt_id, "channel": channel, "payload": msg_payload})
            else:
                conn.execute(
                    """UPDATE proactive_deliveries
                          SET status='skipped', error=?, completed_at=datetime('now')
                        WHERE id=?""",
                    ("outbox was claimed by another worker", attempt_id),
                )
    return claimed


def _finish_attempt(conn, attempt_id: str, *, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    """结束一次投递尝试。

    输入是 attempt id、终态、可选结果或错误；副作用只更新
    ``proactive_deliveries``。失败不会改 outbox 为 sent，保证用户可见的“已送达”
    必须来自真实外部成功。
    """
    with transaction(conn):
        conn.execute(
            """UPDATE proactive_deliveries
                  SET status=?, result_json=?, error=?, completed_at=datetime('now')
                WHERE id=?""",
            (status, dumps(result or {}), error, attempt_id),
        )


def _dispatch_command(payload: dict[str, Any], command: str, timeout: float) -> dict[str, Any]:
    """通过服务器本地命令发送主动消息。

    输入是稳定 JSON 载荷、shell 风格 command 字符串和秒级超时；载荷经 stdin
    传入，命令退出码 0 表示适配器确认已处理。输出保留 stdout/stderr 摘要用于
    审计；非零退出、超时或命令缺失会抛异常，由上层记录失败 attempt。
    """
    if not command:
        raise RuntimeError("delivery command is not configured")
    proc = subprocess.run(
        shlex.split(command),
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    result = {"returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    if proc.returncode != 0:
        raise RuntimeError(f"delivery command failed: {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
    parsed = _json_dict(proc.stdout)
    return {**result, "parsed": parsed}


def _dispatch_webhook(payload: dict[str, Any], url: str, timeout: float) -> dict[str, Any]:
    """通过 HTTP webhook 发送主动消息。

    输入是稳定 JSON 载荷、webhook URL 和秒级超时；输出保留 HTTP 状态和响应
    摘要。只有 2xx 被视为成功；其它状态或网络异常会抛出并保留 queued outbox。
    """
    if not url:
        raise RuntimeError("delivery webhook url is not configured")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(1024 * 256).decode("utf-8", errors="replace")
        status = int(getattr(resp, "status", 200))
    if status < 200 or status >= 300:
        raise RuntimeError(f"delivery webhook failed: HTTP {status}: {body[:500]}")
    return {"status": status, "body": body[-4000:], "parsed": _json_dict(body)}


def _dispatch(payload: dict[str, Any], *, mode: str, command: str, webhook_url: str, timeout: float) -> dict[str, Any]:
    """按配置选择具体投递适配器。

    输入中的 mode 已由配置解析收敛；调用方负责传入 command/webhook_url。stdout
    只用于本地调试或日志型投递，command/webhook 用于服务器真实渠道。
    """
    if mode == "command":
        return _dispatch_command(payload, command, timeout)
    if mode == "webhook":
        return _dispatch_webhook(payload, webhook_url, timeout)
    if mode == "stdout":
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout, flush=True)
        return {"stdout": True}
    raise RuntimeError("proactive delivery is disabled")


def deliver_queued_outbox(
    conn,
    agent_id: str,
    *,
    limit: int = 10,
    dry_run: bool = False,
    **payload: Any,
) -> dict[str, Any]:
    """投递 Agent 已排队的主动消息。

    输入为 Agent id、批量上限、dry-run 标志，以及可选的
    ``delivery_mode`` / ``delivery_command`` / ``webhook_url`` 覆盖；默认读取
    服务器环境变量。函数由 heartbeat 脚本、CLI、tool handler 或测试调用。
    输出包含候选数、成功/失败 attempt 和配置摘要。副作用是：非 dry-run 时
    先把 outbox 原子 claim 为 delivering，再调用外部 command/webhook/stdout；
    成功后在数据库中 mark sent，失败时写 delivery attempt 并把 outbox 放回
    queued 以便下次重试。外部适配器仍应按 outbox_id 幂等，以覆盖进程崩溃后
    “外部已发但本地未标 sent”的极端场景。
    """
    cfg = delivery_config_status(payload)
    quiet_policy = _gate_policy(None, _get_canon_policy(conn, agent_id))
    quiet_preview = quiet_hours_status(quiet_policy)
    if dry_run or not cfg.get("enabled"):
        rows = _due_outbox(conn, agent_id, max(1, int(limit)))
        return {
            "ok": True,
            "status": "dry_run" if dry_run else "disabled",
            "config": cfg,
            "quiet_hours": {"active": bool(quiet_preview.get("active")), "next_allowed_at": quiet_preview.get("next_allowed_at"), "timezone": quiet_preview.get("timezone"), "skipped": True},
            "stale_claims": {"requeued_count": 0, "outbox_ids": [], "attempt_ids": [], "skipped": True},
            "candidate_count": len(rows),
            "candidates": [
                {"id": r.get("id"), "intent_id": r.get("intent_id"), "target_user_id": r.get("target_user_id"), "draft_text": r.get("draft_text")}
                for r in rows
            ],
            "delivered": [],
            "failed": [],
        }

    mode = cfg["mode"]
    stale = _reap_stale_delivery_claims(conn, agent_id, float(payload.get("claim_ttl_minutes") or 30))
    quiet = _defer_due_outbox_for_quiet_hours(conn, agent_id, max(1, int(limit)))
    if quiet.get("active"):
        return {
            "ok": True,
            "status": "deferred_quiet_hours" if quiet.get("deferred_count") else "noop",
            "config": cfg,
            "quiet_hours": quiet,
            "stale_claims": stale,
            "candidate_count": 0,
            "delivered": [],
            "failed": [],
        }
    command = str(payload.get("delivery_command") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_COMMAND") or "").strip()
    webhook_url = str(payload.get("webhook_url") or os.getenv("LIFEENGINE_PROACTIVE_DELIVERY_WEBHOOK_URL") or "").strip()
    timeout = float(cfg["timeout_seconds"])
    delivered: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    claimed = _claim_due_outbox(conn, agent_id, max(1, int(limit)), str(payload.get("delivery_channel") or mode))
    for item in claimed:
        row = item["row"]
        channel = item["channel"]
        msg_payload = item["payload"]
        attempt_id = item["attempt_id"]
        try:
            result = _dispatch(msg_payload, mode=mode, command=command, webhook_url=webhook_url, timeout=timeout)
            result = {**result, "delivery_attempt_id": attempt_id, "delivery_mode": mode, "delivery_channel": channel}
            with transaction(conn):
                conn.execute(
                    """UPDATE proactive_deliveries
                          SET status='done', result_json=?, completed_at=datetime('now')
                        WHERE id=?""",
                    (dumps(result), attempt_id),
                )
                sent = mark_outbox_sent(conn, agent_id, row["id"], result=result, manual=False)
            try:
                append_journal(conn, "agent", agent_id, "proactive_outbox_delivered", {"outbox_id": row["id"], "delivery_attempt_id": attempt_id, "mode": mode}, "proactive_delivery")
            except Exception:
                # 外部消息已经发送且 outbox 已标 sent；journal 只是审计补充，
                # 不能再把真实送达反判为失败，避免 sent/failed 双重状态。
                pass
            delivered.append({"outbox_id": row["id"], "delivery_attempt_id": attempt_id, "outbox": sent.get("outbox")})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            _finish_attempt(conn, attempt_id, status="failed", error=error)
            with transaction(conn):
                conn.execute("UPDATE proactive_outbox SET status='queued', error=? WHERE id=? AND status='delivering'", (error, row["id"]))
            failed.append({"outbox_id": row["id"], "delivery_attempt_id": attempt_id, "error": error})
    return {
        "ok": not failed,
        "status": "delivered" if delivered and not failed else ("partial" if delivered else ("failed" if failed else "noop")),
        "config": cfg,
        "quiet_hours": quiet,
        "stale_claims": stale,
        "candidate_count": len(claimed),
        "delivered": delivered,
        "failed": failed,
    }
