import hashlib
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from memory import KST


_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


def _parse_hour_minute(raw: Any, default_hour: int) -> Tuple[int, int]:
    text = str(raw or "").strip()
    match = _TIME_RE.search(text)
    if not match:
        return max(0, min(23, int(default_hour))), 0
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour, minute


def _stable_pick(ids: List[int], seed_text: str) -> int:
    if not ids:
        return 0
    h = hashlib.sha1(seed_text.encode("utf-8", "ignore")).hexdigest()
    idx = int(h[:8], 16) % len(ids)
    return ids[idx]


def _text_blob(loc: Any) -> str:
    parts = [
        str(getattr(loc, "district", "") or ""),
        str(getattr(loc, "name", "") or ""),
        str(getattr(loc, "category", "") or ""),
        str(getattr(loc, "description", "") or ""),
    ]
    return " ".join(parts).lower()


def _contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(w in lowered for w in words)


def _location_groups(locations: List[Any]) -> Dict[str, List[int]]:
    all_ids: List[int] = []
    home_ids: List[int] = []
    work_ids: List[int] = []
    social_ids: List[int] = []

    home_words = {
        "home", "house", "residence", "apartment", "apt", "living",
        "\uc9d1", "\uc219\uc18c", "\ud558\uc6b0\uc2a4", "\uac70\uc8fc",
    }
    work_words = {
        "office", "work", "lab", "studio", "research", "corp", "hq", "biz",
        "\uc0ac\ubb34", "\uc5c5\ubb34", "\uc5f0\uad6c", "\uc624\ud53c\uc2a4", "\ud0c0\uc6cc",
    }
    social_words = {
        "park", "cafe", "coffee", "mall", "club", "bar", "gallery", "plaza",
        "beach", "river", "arcade", "game", "concert",
        "\uacf5\uc6d0", "\uce74\ud398", "\ubab0", "\uac24\ub7ec\ub9ac", "\uad11\uc7a5", "\uc0b0\ucc45",
    }

    for loc in locations:
        try:
            lid = int(getattr(loc, "id"))
        except Exception:
            continue
        all_ids.append(lid)
        blob = _text_blob(loc)
        if _contains_any(blob, home_words):
            home_ids.append(lid)
        if _contains_any(blob, work_words):
            work_ids.append(lid)
        if _contains_any(blob, social_words):
            social_ids.append(lid)

    if not all_ids:
        return {"all": [], "home": [], "work": [], "social": []}

    if not home_ids:
        home_ids = [all_ids[0]]
    if not work_ids:
        work_ids = [all_ids[1]] if len(all_ids) > 1 else [all_ids[0]]
    if not social_ids:
        social_ids = [lid for lid in all_ids if lid not in set(home_ids + work_ids)] or list(all_ids)

    return {
        "all": all_ids,
        "home": list(dict.fromkeys(home_ids)),
        "work": list(dict.fromkeys(work_ids)),
        "social": list(dict.fromkeys(social_ids)),
    }


def _task_entries(daily_schedule: Any) -> Tuple[int, int, List[Tuple[int, int, str]]]:
    wake_h, _ = _parse_hour_minute("08:00", 8)
    sleep_h, _ = _parse_hour_minute("23:00", 23)
    entries: List[Tuple[int, int, str]] = []

    if isinstance(daily_schedule, dict):
        wake_h, _ = _parse_hour_minute(daily_schedule.get("wake_time"), 8)
        sleep_h, _ = _parse_hour_minute(daily_schedule.get("sleep_time"), 23)
        tasks = list(daily_schedule.get("daily_tasks", []) or [])
    elif isinstance(daily_schedule, list):
        tasks = list(daily_schedule)
    else:
        tasks = []

    for item in tasks:
        if isinstance(item, dict):
            raw_time = item.get("time") or item.get("at") or item.get("start") or ""
            raw_act = item.get("activity") or item.get("task") or item.get("content") or ""
            if not raw_time and item.get("hour") is not None:
                try:
                    raw_time = f"{int(item.get('hour')):02d}:00"
                except Exception:
                    raw_time = ""
            h, m = _parse_hour_minute(raw_time, -1)
            if raw_time and 0 <= h <= 23:
                entries.append((h, m, str(raw_act or "").strip()))
            continue

        text = str(item or "").strip()
        if not text:
            continue
        mobj = _TIME_RE.search(text)
        if not mobj:
            continue
        h = max(0, min(23, int(mobj.group(1))))
        mm = max(0, min(59, int(mobj.group(2))))
        act = text[mobj.end():].strip(" -:|")
        entries.append((h, mm, act))

    entries.sort(key=lambda x: (x[0], x[1]))
    return wake_h, sleep_h, entries


def _is_sleep_hour(hour: int, wake_h: int, sleep_h: int) -> bool:
    if wake_h == sleep_h:
        return False
    if wake_h < sleep_h:
        return hour < wake_h or hour >= sleep_h
    return sleep_h <= hour < wake_h


def _pick_location_for_activity(
    hour: int,
    activity: str,
    groups: Dict[str, List[int]],
    persona_id: int,
    date_key: str,
) -> int:
    text = (activity or "").lower()

    sleep_words = {
        "sleep", "bed", "rest", "night",
        "\uc218\uba74", "\ucde8\uce68", "\ud734\uc2dd", "\uc790\ub2e4",
    }
    work_words = {
        "work", "office", "meeting", "project", "study", "class", "task", "lab",
        "\uc5c5\ubb34", "\uc791\uc5c5", "\ud68c\uc758", "\uacf5\ubd80", "\uc218\uc5c5", "\ucd9c\uadfc",
    }
    social_words = {
        "eat", "meal", "lunch", "dinner", "walk", "date", "shop", "game", "chat",
        "cafe", "bar", "park", "concert", "movie",
        "\uc2dd\uc0ac", "\uc810\uc2ec", "\uc800\ub141", "\uc0b0\ucc45", "\ub370\uc774\ud2b8", "\uc1fc\ud551", "\ub180\uae30",
    }

    if _contains_any(text, sleep_words):
        pool = groups["home"]
    elif _contains_any(text, work_words):
        pool = groups["work"]
    elif _contains_any(text, social_words):
        pool = groups["social"]
    else:
        if 9 <= hour <= 17:
            pool = groups["work"] + groups["social"]
        else:
            pool = groups["social"] + groups["home"]
        if not pool:
            pool = groups["all"]

    seed = f"{persona_id}:{date_key}:{hour}:{activity}"
    picked = _stable_pick(pool, seed)
    if picked:
        return picked
    return _stable_pick(groups["all"], f"{persona_id}:{date_key}:{hour}:fallback")


def compute_hourly_location_plan(
    persona_id: int,
    daily_schedule: Any,
    locations: List[Any],
    when: Optional[datetime] = None,
) -> Dict[str, int]:
    now = when or datetime.now(KST)
    date_key = now.strftime("%Y-%m-%d")
    groups = _location_groups(locations)
    if not groups["all"]:
        return {}

    wake_h, sleep_h, entries = _task_entries(daily_schedule)
    by_hour: Dict[str, int] = {}
    first_loc = groups["all"][0]
    last_activity = ""

    for hour in range(24):
        hour_key = f"{hour:02d}"
        if _is_sleep_hour(hour, wake_h, sleep_h):
            by_hour[hour_key] = _stable_pick(groups["home"], f"{persona_id}:{date_key}:{hour}:sleep") or first_loc
            continue

        active_activity = ""
        for eh, em, act in entries:
            if eh < hour or (eh == hour and em <= 59):
                active_activity = act
            else:
                break

        if active_activity:
            last_activity = active_activity
        elif last_activity:
            active_activity = last_activity
        else:
            active_activity = "routine"

        by_hour[hour_key] = _pick_location_for_activity(hour, active_activity, groups, persona_id, date_key)

    return by_hour


def planned_location_id_for_datetime(
    persona_id: int,
    daily_schedule: Any,
    locations: List[Any],
    when: Optional[datetime] = None,
) -> Optional[int]:
    now = when or datetime.now(KST)
    by_hour = compute_hourly_location_plan(persona_id, daily_schedule, locations, now)
    if not by_hour:
        return None
    key = f"{now.hour:02d}"
    return by_hour.get(key) or by_hour.get("00")
