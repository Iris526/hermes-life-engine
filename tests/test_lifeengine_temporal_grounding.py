"""Reply-path temporal grounding: the agent must feel *when now is* and *how long
since we last spoke*, as precise structured facts (not prose instructions).

Regression for: after a multi-hour gap the agent kept the previous thread going
(e.g. still offering dinner in the small hours) because the reply capsule had no
current local time, no day phase, and no elapsed-since-last-exchange signal.
"""
from __future__ import annotations

import os
import tempfile

from lifeengine.canon import get_active_canon
from lifeengine.conversation import temporal_grounding
from lifeengine.runtime import LifeEngineRuntime
from lifeengine.time_utils import to_epoch

OWNER_KIND = "agent"
OWNER_ID = "default-agent"


def _agent(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", tempfile.mkdtemp(prefix="le_temporal_"))
    rt = LifeEngineRuntime()
    rt.setup("temporal grounding test agent")
    rt.commit_canon()
    rt.control("resume")
    rt.living("init_resources")
    return rt


def _record_exchange(rt, *, turn_id, at_iso):
    rt.conn.execute(
        "INSERT INTO conversation_activity_judgments(id, owner_kind, owner_id, session_id, turn_id, "
        "judgment_type, agent_time_policy, created_at_ts) VALUES(?,?,?,?,?,?,?,?)",
        (f"j-{turn_id}", OWNER_KIND, OWNER_ID, "s1", turn_id, "ambient_chat", "free", to_epoch(at_iso)),
    )
    rt.conn.commit()


def test_precise_gap_and_small_hours_phase(monkeypatch):
    rt = _agent(monkeypatch)
    try:
        canon = get_active_canon(rt.conn, OWNER_KIND, OWNER_ID)  # default tz Asia/Tokyo
        _record_exchange(rt, turn_id="t1", at_iso="2026-07-02 20:00:00+09:00")  # chatted at 8pm
        g = temporal_grounding(rt.conn, OWNER_KIND, OWNER_ID, canon=canon,
                               now="2026-07-03 00:12:00+09:00", session_id="s1", turn_id="t2")
        # It is the small hours, not "evening".
        assert g["phase"] == "small_hours" and g["phase_label"] == "凌晨"
        assert g["now_local"] == "2026-07-03 00:12"
        # Precise gap, not a coarse band.
        assert g["since_last_exchange"]["minutes"] == 252
        assert g["since_last_exchange"]["human"] == "4小时12分钟前"
    finally:
        rt.close()


def test_meal_window_reads_relative_to_now(monkeypatch):
    rt = _agent(monkeypatch)
    try:
        canon = get_active_canon(rt.conn, OWNER_KIND, OWNER_ID)
        # 21:00 Tokyo — dinner window (19:00–21:30) is still open.
        g = temporal_grounding(rt.conn, OWNER_KIND, OWNER_ID, canon=canon, now="2026-07-02 21:00:00+09:00")
        dinner = next(w for w in g["today_windows"] if w["key"] == "dinner")
        assert dinner["relation"] == "in_window"

        # 23:30 Tokyo — dinner window has closed; must read 'passed', not 'pending-to-suggest'.
        g2 = temporal_grounding(rt.conn, OWNER_KIND, OWNER_ID, canon=canon, now="2026-07-02 23:30:00+09:00")
        dinner2 = next(w for w in g2["today_windows"] if w["key"] == "dinner")
        assert dinner2["relation"] == "passed"
        assert dinner2["minutes"] == 120  # 120 min since the 21:30 window close
    finally:
        rt.close()


def test_first_exchange_has_no_fabricated_gap(monkeypatch):
    rt = _agent(monkeypatch)
    try:
        canon = get_active_canon(rt.conn, OWNER_KIND, OWNER_ID)
        g = temporal_grounding(rt.conn, OWNER_KIND, OWNER_ID, canon=canon,
                               now="2026-07-02 21:00:00+09:00", session_id="s1", turn_id="t1")
        assert g["since_last_exchange"] is None  # never invent a gap
    finally:
        rt.close()


def test_time_block_reaches_the_reply_capsule(monkeypatch):
    rt = _agent(monkeypatch)
    try:
        capsule = rt.build_context_for_turn("s1", "t1", "在吗", sender_id="anonymous-user")
        # The sharp temporal anchor is present in the injected context text.
        assert '"time"' in capsule
        assert '"phase"' in capsule and '"now_local"' in capsule
    finally:
        rt.close()
