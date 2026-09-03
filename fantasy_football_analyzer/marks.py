"""Persistent drafted-player marks for live drafts.

ESPN's REST API freezes during a live draft, so picks reach the analyzer from
the Chrome extension (WebSocket feed, pick-history scraper) or by hand. Marks
carry the team and the auction price, are ordered by arrival so the pick
sequence survives, and are written to disk on every change so a container
restart mid-draft loses nothing.
"""

import json
import os
import threading

MARKS_PATH = os.path.expanduser("~/.fantasy_football_analyzer_marks.json")


class MarkStore:
    def __init__(self, path=None):
        self.path = path or MARKS_PATH
        self._data = None  # {league_id(str): {pid(str): {team_id, bid, seq}}}
        self._lock = threading.Lock()

    def _load(self):
        if self._data is not None:
            return
        self._data = {}
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:
            self._data = {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except Exception:
            pass  # disk trouble must never break the draft

    # Mock rehearsal mode is persisted with the marks, and mock marks live in
    # their own namespace ("mock:<league>") so a rehearsal never touches the
    # real board — whichever way a restart or a forgotten toggle goes.
    def is_mock(self, league_id):
        with self._lock:
            self._load()
            return bool((self._data.get("__mock__") or {}).get(str(league_id)))

    def set_mock(self, league_id, enabled):
        with self._lock:
            self._load()
            modes = self._data.setdefault("__mock__", {})
            modes[str(league_id)] = bool(enabled)
            self._save()

    def _ns(self, league_id):
        modes = self._data.get("__mock__") or {}
        return f"mock:{league_id}" if modes.get(str(league_id)) else str(league_id)

    def get(self, league_id):
        """{pid: {"team_id", "bid", "seq"}} for a league, ordered by arrival."""
        with self._lock:
            self._load()
            marks = self._data.get(self._ns(league_id)) or {}
            out = {}
            for pid, info in sorted(marks.items(), key=lambda kv: kv[1].get("seq", 0)):
                out[int(pid)] = {
                    "team_id": info.get("team_id"),
                    "bid": int(info.get("bid") or 0),
                    "seq": info.get("seq", 0),
                }
            return out

    def set(self, league_id, pid, team_id=None, bid=None):
        """Record a mark. Known team/price win over later teamless reports."""
        with self._lock:
            self._load()
            marks = self._data.setdefault(self._ns(league_id), {})
            key = str(pid)
            existing = marks.get(key)
            if existing is None:
                seq = max((m.get("seq", 0) for m in marks.values()), default=0) + 1
                marks[key] = {"team_id": None, "bid": 0, "seq": seq}
                existing = marks[key]
            if isinstance(team_id, int):
                existing["team_id"] = team_id
            if isinstance(bid, int) and bid > 0:
                existing["bid"] = bid
            self._save()
            return len(marks)

    def remove(self, league_id, pid):
        with self._lock:
            self._load()
            marks = self._data.get(self._ns(league_id)) or {}
            marks.pop(str(pid), None)
            self._save()
            return len(marks)

    def clear(self, league_id):
        with self._lock:
            self._load()
            self._data.pop(self._ns(league_id), None)
            self._save()


store = MarkStore()
