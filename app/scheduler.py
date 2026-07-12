"""Lightweight in-process scheduler + autopilot.

Runs the aggregator + matcher on a configurable interval, then (if the user has
enabled "全自动模式" / autopilot) finishes the job with NO human clicks:

  1. auto-approve drafted applications at/above the confidence threshold
  2. auto-send approved applications by email (unattended, rate-limited)
  3. auto-draft + auto-approve HR chat replies, so they are ready to ship

The only step that still needs a human is the *final* send inside a third-party
site (e.g. clicking send in BOSS). Everything up to that point can be hands-off.

Runs in a daemon thread; safe to start once per process.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import database as db
from . import aggregator
from . import matcher
from . import chat as chat_mod


class Scheduler:
    def __init__(self) -> None:
        self._timer: Optional[threading.Timer] = None
        self._stop = False
        self._running = False
        self.last_autopilot: Optional[dict] = None

    # ------------------------------------------------------------------ #
    def _autopilot(self) -> dict:
        """Run the hands-off pipeline. Returns a small activity report."""
        settings = db.get_settings()
        ap = settings.get("autopilot", {})
        if not ap.get("enabled"):
            return {"enabled": False}
        from . import submitter

        llm_cfg = settings.get("llm", {})
        llm_on = bool(llm_cfg.get("enabled") and llm_cfg.get("api_key"))
        report: dict = {"enabled": True, "approved": 0, "sent": 0, "chat_replies": 0}

        # 1) auto-approve drafted applications above the confidence threshold
        if ap.get("auto_approve", True):
            min_score = float(ap.get("min_score", 75))
            for a in db.list_applications(status="drafted"):
                if (a.get("match_score") or 0) >= min_score:
                    db.update_application_status(a["id"], "approved")
                    report["approved"] += 1

        # 2) unattended email send of approved applications (rate-limited)
        if ap.get("auto_send", False):
            cap = int(ap.get("max_send_per_day", 30))
            sent_today = submitter.count_sent_today()
            for a in db.list_applications(status="approved"):
                if sent_today >= cap:
                    break
                if not (a.get("email_to") or "").strip():
                    continue  # no target address (e.g. a demo job) -> skip
                res = submitter.send_email(a["id"], force=True)
                if res.get("ok"):
                    sent_today += 1
                    report["sent"] += 1

        # 3) HR chat: auto-draft + auto-approve replies, fully hands-off
        if ap.get("auto_chat_reply", True):
            for conv in db.list_conversations():
                msgs = db.list_messages(conv["id"])
                inbound = next((m for m in reversed(msgs) if m["direction"] == "inbound"), None)
                if not inbound:
                    continue
                # has an outbound already been produced after this inbound?
                has_out = any(m["direction"] == "outbound" and m["id"] > inbound["id"] for m in msgs)
                if not has_out:
                    reply = chat_mod.generate_reply(conv, inbound["text"], use_llm=llm_on)
                    if reply and reply.get("text"):
                        db.add_message(conv["id"], "outbound", reply["text"],
                                       status="drafted", reply_to=inbound["id"])
                        report["chat_replies"] += 1
                # approve any pending outbound drafts so they are ready to ship
                for m in db.list_messages(conv["id"]):
                    if m["direction"] == "outbound" and m["status"] in ("drafted", "received"):
                        db.update_message(m["id"], {"status": "approved"})
                        report["chat_replies"] += 1

        self.last_autopilot = report
        return report

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if self._stop:
            return
        try:
            settings = db.get_settings()
            sch = settings.get("schedule", {})
            aggregator.run_all(settings)
            matcher.match_and_store(
                min_score=float(sch.get("min_match_score", 60)),
                auto_draft=bool(sch.get("auto_draft", True)),
            )
            # hands-off pipeline (no-op unless autopilot is enabled)
            self._autopilot()
        except Exception:
            pass
        finally:
            self._schedule_next()

    def _schedule_next(self) -> None:
        if self._stop:
            return
        settings = db.get_settings()
        interval = int(settings.get("schedule", {}).get("refresh_interval_minutes", 360))
        self._timer = threading.Timer(max(60, interval * 60), self._tick)
        self._timer.daemon = True
        self._timer.start()

    def kick(self) -> None:
        """Trigger an immediate tick (e.g. right after the user enables autopilot)."""
        if self._stop:
            return
        threading.Timer(0.5, self._tick).start()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop = False
        self._tick()  # run once immediately, then schedule

    def stop(self) -> None:
        self._stop = True
        if self._timer:
            self._timer.cancel()


scheduler = Scheduler()
