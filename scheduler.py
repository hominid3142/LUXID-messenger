import asyncio
import json
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal
from models import ChatRoom, User, Persona, FeedPost, FeedComment, ScheduledAction, EveRelationship, UserPersonaRelationship, MapLocation
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import random
import engine
import fal_client
from memory import (
    KST,
    volatile_memory,
    update_shared_memory,
    set_ticker_active_eve_count,
)
try:
    from location_planner import compute_hourly_location_plan, planned_location_id_for_datetime
except Exception:
    compute_hourly_location_plan = None
    planned_location_id_for_datetime = None


def _build_ethnicity_prompt(white: int, black: int, asian: int) -> str:
    parts = []
    if white and white > 0:
        parts.append(f"{white}% White")
    if black and black > 0:
        parts.append(f"{black}% Black")
    if asian and asian > 0:
        parts.append(f"{asian}% Asian")
    # Feed image default should stay Korean unless explicit ethnicity weights exist.
    return ", ".join(parts) if parts else "Korean"


def _safe_log_text(text: str, max_len: int = 20) -> str:
    s = (text or "")[:max_len]
    return s.encode("cp949", "replace").decode("cp949")


class AEScheduler:
    def __init__(self):
        # KST(한국 시간) 기준으로 스케줄링
        self.scheduler = AsyncIOScheduler(timezone=KST)
        self.hourly_post_min = int(os.environ.get("HOURLY_POST_MIN", "4"))
        self.hourly_post_max = int(os.environ.get("HOURLY_POST_MAX", "10"))
        self.hourly_comment_min = int(os.environ.get("HOURLY_COMMENT_MIN", "10"))
        self.hourly_comment_max = int(os.environ.get("HOURLY_COMMENT_MAX", "22"))
        self.nightly_gallery_add_limit = int(os.environ.get("NIGHTLY_EVE_PHOTO_ADDS", "10"))
        self.max_profile_photos = 3

    def _clamp_0_100(self, value: int) -> int:
        return max(0, min(100, int(value)))

    def _sync_persona_locations_for_hour(self, db: Session, personas: list[Persona], now: datetime) -> int:
        if not personas:
            return 0
        all_locations = db.query(MapLocation).all()
        if not all_locations:
            return 0

        all_ids = sorted([int(loc.id) for loc in all_locations if getattr(loc, "id", None) is not None])
        if not all_ids:
            return 0

        date_key = now.strftime("%Y-%m-%d")
        hour_key = f"{now.hour:02d}"
        updated = 0

        for persona in personas:
            slots: dict = {}
            pd = dict(persona.profile_details or {})
            plan_block = pd.get("hourly_location_plan") if isinstance(pd.get("hourly_location_plan"), dict) else {}

            if (
                compute_hourly_location_plan
                and (
                    not plan_block
                    or str(plan_block.get("date") or "") != date_key
                    or not isinstance(plan_block.get("slots"), dict)
                )
            ):
                try:
                    fresh_slots = compute_hourly_location_plan(
                        persona_id=persona.id,
                        daily_schedule=persona.daily_schedule,
                        locations=all_locations,
                        when=now,
                    )
                except Exception:
                    fresh_slots = {}
                if fresh_slots:
                    slots = dict(fresh_slots)
                    pd["hourly_location_plan"] = {
                        "date": date_key,
                        "slots": slots,
                    }
                    persona.profile_details = pd
            elif isinstance(plan_block.get("slots"), dict):
                slots = dict(plan_block.get("slots") or {})

            target_location_id = slots.get(hour_key) or slots.get("00")
            if not target_location_id:
                target_location_id = all_ids[(int(persona.id) + now.hour) % len(all_ids)]
            try:
                target_location_id = int(target_location_id)
            except Exception:
                target_location_id = all_ids[0]

            if persona.current_location_id != target_location_id:
                persona.current_location_id = target_location_id
                updated += 1

        return updated

    def _resolve_persona_location_for_time(
        self,
        db: Session,
        persona: Persona,
        when: datetime,
        all_locations: list[MapLocation] | None = None,
    ) -> tuple[int | None, str | None, str | None]:
        locations = all_locations if all_locations is not None else db.query(MapLocation).all()
        if not locations:
            return None, None, None

        loc_map = {}
        all_ids = []
        for loc in locations:
            try:
                lid = int(loc.id)
            except Exception:
                continue
            loc_map[lid] = loc
            all_ids.append(lid)
        if not all_ids:
            return None, None, None

        location_id = None
        if planned_location_id_for_datetime:
            try:
                location_id = planned_location_id_for_datetime(
                    persona_id=persona.id,
                    daily_schedule=persona.daily_schedule,
                    locations=locations,
                    when=when,
                )
            except Exception:
                location_id = None

        if not location_id:
            location_id = persona.current_location_id
        if not location_id:
            location_id = all_ids[(int(persona.id) + int(when.hour)) % len(all_ids)]

        try:
            location_id = int(location_id)
        except Exception:
            location_id = all_ids[0]

        loc = loc_map.get(location_id)
        if not loc:
            loc = loc_map.get(all_ids[0])
            location_id = all_ids[0]
        return (
            location_id,
            str(getattr(loc, "name", "") or "").strip() or None,
            str(getattr(loc, "district", "") or "").strip() or None,
        )

    def _apply_feed_activity_state_delta(self, db: Session, persona_id: int) -> tuple[int, int]:
        delta_erotic = random.randint(-10, 10)
        delta_mood = random.randint(-10, 10)

        rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona_id).all()
        for room in rooms:
            room.v_erotic = self._clamp_0_100((room.v_erotic or 0) + delta_erotic)
            room.v_v_mood = self._clamp_0_100((room.v_v_mood or 0) + delta_mood)
            vs = volatile_memory.get(room.id)
            if isinstance(vs, dict):
                vs["v_erotic"] = room.v_erotic
                vs["v_v_mood"] = room.v_v_mood

        pairs = db.query(UserPersonaRelationship).filter(
            UserPersonaRelationship.persona_id == persona_id
        ).all()
        for pair in pairs:
            pair.erotic = self._clamp_0_100((pair.erotic or 0) + delta_erotic)
            pair.mood = self._clamp_0_100((pair.mood or 0) + delta_mood)
            pair.updated_at = datetime.utcnow()

        return delta_erotic, delta_mood

    def _coerce_msg_dt(self, value: str, now: datetime) -> datetime | None:
        if not value:
            return None
        v = str(value).strip()
        if not v:
            return None
        patterns = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%H:%M:%S",
            "%H:%M",
            "%I:%M:%S %p",
            "%I:%M %p",
        ]
        for pat in patterns:
            try:
                parsed = datetime.strptime(v, pat)
            except Exception:
                continue
            if pat.startswith("%Y"):
                return parsed.replace(tzinfo=KST)
            candidate = now.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
            if candidate > now + timedelta(minutes=5):
                candidate = candidate - timedelta(days=1)
            return candidate
        return None

    def _extract_recent_dialogue_12h(self, room: ChatRoom, now: datetime, limit: int = 36) -> list[dict]:
        history = list(room.history or [])
        if not history:
            return []
        cutoff = now - timedelta(hours=12)
        picked = []
        for msg in history[-120:]:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip().lower()
            if role not in ("user", "assistant"):
                continue
            text = str(msg.get("content") or "").strip()
            if not text:
                continue
            raw_ts = msg.get("timestamp") or msg.get("ts") or msg.get("time") or ""
            dt = self._coerce_msg_dt(raw_ts, now)
            if dt is not None and dt < cutoff:
                continue
            picked.append({
                "role": "user" if role == "user" else "eve",
                "text": text[:280],
                "ts": str(raw_ts)[:32],
            })
        if not picked:
            for msg in history[-30:]:
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role") or "").strip().lower()
                if role not in ("user", "assistant"):
                    continue
                text = str(msg.get("content") or "").strip()
                if not text:
                    continue
                picked.append({
                    "role": "user" if role == "user" else "eve",
                    "text": text[:280],
                    "ts": str(msg.get("timestamp") or msg.get("ts") or "")[:32],
                })
        return picked[-limit:]

    def _extract_medium_summaries(self, room: ChatRoom, persona: Persona, user_id: int, limit: int = 10) -> list[str]:
        summaries = []
        state = volatile_memory.get(room.id) or {}
        for item in list(state.get("medium_term_logs", []) or []):
            txt = str(item or "").strip()
            if txt:
                summaries.append(txt)

        for entry in list(persona.shared_memory or []):
            if not isinstance(entry, dict):
                continue
            if entry.get("category") != "conversation":
                continue
            src_uid = entry.get("source_user_id")
            if src_uid not in (None, user_id):
                continue
            txt = str(entry.get("fact") or "").strip()
            if txt:
                summaries.append(txt)

        dedup = []
        seen = set()
        for s in summaries:
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(s)
        return dedup[-limit:]

    def _upsert_user_pair_relationship(
        self,
        db: Session,
        room: ChatRoom,
        category: str,
        summary_3line: str,
        delta: int,
        now: datetime,
    ) -> UserPersonaRelationship:
        pair = db.query(UserPersonaRelationship).filter(
            UserPersonaRelationship.user_id == room.owner_id,
            UserPersonaRelationship.persona_id == room.persona_id
        ).first()
        if not pair:
            pair = UserPersonaRelationship(
                user_id=room.owner_id,
                persona_id=room.persona_id,
            )
            db.add(pair)

        current_score = pair.relationship_score if pair.relationship_score is not None else (room.v_relationship or 20)
        next_score = max(0, min(100, int(current_score) + int(delta)))

        pair.relationship_category = category or "낯선 사람"
        pair.relationship_score = next_score
        pair.likeability = room.v_likeability if room.v_likeability is not None else (pair.likeability or 50)
        pair.erotic = room.v_erotic if room.v_erotic is not None else (pair.erotic or 30)
        pair.mood = room.v_v_mood if room.v_v_mood is not None else (pair.mood or 50)
        pair.relationship_summary_3line = summary_3line[:500] if summary_3line else None
        pair.relationship_last_defined_at = now
        pair.updated_at = datetime.utcnow()

        room.relationship_category = pair.relationship_category
        room.v_relationship = next_score
        room.relationship_summary_3line = pair.relationship_summary_3line
        room.relationship_last_defined_at = now
        return pair

    async def _resolve_romance_for_persona(
        self,
        db: Session,
        persona: Persona,
        room_contexts: list[dict],
        now: datetime,
    ) -> dict:
        if not room_contexts:
            return {"mode": "noop", "selected_user_id": None}

        def _score_of(ctx: dict) -> int:
            pair = ctx.get("pair")
            room = ctx.get("room")
            if pair and pair.relationship_score is not None:
                return int(pair.relationship_score)
            if room and room.v_relationship is not None:
                return int(room.v_relationship)
            return 20

        # Normalize broken states: keep at most one current partner.
        couples = []
        for ctx in room_contexts:
            pair = ctx.get("pair")
            room = ctx.get("room")
            is_couple = bool(
                (pair and str(pair.romance_state or "") == "커플")
                or (room and str(getattr(room, "romance_state", "") or "") == "커플")
            )
            if is_couple:
                couples.append(ctx)
        if len(couples) > 1:
            keep = sorted(couples, key=_score_of, reverse=True)[0]
            keep_uid = keep["owner"].id
            for ctx in couples:
                if ctx["owner"].id == keep_uid:
                    continue
                pair = ctx.get("pair")
                room = ctx.get("room")
                if pair:
                    pair.romance_state = "싱글"
                    pair.romance_partner_label = None
                    pair.updated_at = datetime.utcnow()
                if room:
                    room.romance_state = "싱글"
                    room.romance_partner_label = None
                    room.romance_decided_at = now

        current_partner_user_id = None
        for ctx in room_contexts:
            pair = ctx.get("pair")
            room = ctx.get("room")
            is_couple = bool(
                (pair and str(pair.romance_state or "") == "커플")
                or (room and str(getattr(room, "romance_state", "") or "") == "커플")
            )
            if is_couple:
                current_partner_user_id = ctx["owner"].id
                break

        pending_candidates = []
        for ctx in room_contexts:
            pair = ctx.get("pair")
            room = ctx.get("room")
            owner = ctx.get("owner")
            if not owner:
                continue
            pair_conf_pending = bool(pair and pair.confession_pending)
            room_conf_pending = bool(room and getattr(room, "confession_pending", False))
            fact_tail = list(ctx.get("fact_warehouse") or [])[-30:]
            confession_facts = [str(x) for x in fact_tail if isinstance(x, str) and "고백받음" in x]
            confession_candidates = []
            if pair and isinstance(pair.confession_candidates, list):
                confession_candidates = list(pair.confession_candidates)
            elif room and isinstance(getattr(room, "confession_candidates", None), list):
                confession_candidates = list(room.confession_candidates)

            if not (pair_conf_pending or room_conf_pending or confession_facts):
                continue
            if not confession_candidates and confession_facts:
                confession_candidates = [
                    {
                        "user_id": owner.id,
                        "user_name": owner.display_name or owner.username,
                        "at": now.strftime("%Y-%m-%d %H:%M"),
                        "message": confession_facts[-1][:180],
                    }
                ]
            if not confession_candidates:
                continue

            pending_candidates.append(
                {
                    "user_id": owner.id,
                    "user_name": owner.display_name or owner.username,
                    "relationship_category": (pair.relationship_category if pair else room.relationship_category) or "낯선 사람",
                    "relationship_score": _score_of(ctx),
                    "summary_3line": (pair.relationship_summary_3line if pair else room.relationship_summary_3line) or "",
                    "recent_dialogue_12h": list(ctx.get("recent_12h") or []),
                    "confession_candidates": confession_candidates[-8:],
                    "fact_warehouse_tail": fact_tail[-20:],
                }
            )

        if pending_candidates:
            decision = await engine.decide_romance_outcome(
                persona_payload={
                    "persona_id": persona.id,
                    "name": persona.name,
                    "mbti": persona.mbti,
                    "profile_details": persona.profile_details or {},
                },
                pending_candidates=pending_candidates,
                current_partner_user_id=current_partner_user_id,
            )
            decision_type = str(decision.get("decision") or "reject_all").strip().lower()
            selected_user_id = decision.get("selected_user_id")
            if decision_type != "accept_one":
                selected_user_id = None

            for ctx in room_contexts:
                pair = ctx.get("pair")
                room = ctx.get("room")
                owner = ctx.get("owner")
                if not owner:
                    continue
                is_selected = selected_user_id is not None and owner.id == int(selected_user_id)
                partner_label = (owner.display_name or owner.username) if is_selected else None

                if pair:
                    pair.romance_state = "커플" if is_selected else "싱글"
                    pair.romance_partner_label = partner_label
                    pair.confession_pending = False
                    pair.confession_received_at = None
                    pair.confession_candidates = []
                    pair.updated_at = datetime.utcnow()
                if room:
                    room.romance_state = "커플" if is_selected else "싱글"
                    room.romance_partner_label = partner_label
                    room.confession_pending = False
                    room.confession_received_at = None
                    room.confession_candidates = []
                    room.romance_decided_at = now

            return {
                "mode": "confession_resolution",
                "decision": decision_type,
                "selected_user_id": selected_user_id,
            }

        # No pending confession: evaluate breakup health for current couple.
        breakup_ids = []
        for ctx in room_contexts:
            pair = ctx.get("pair")
            room = ctx.get("room")
            owner = ctx.get("owner")
            if not owner:
                continue
            is_couple = bool(
                (pair and str(pair.romance_state or "") == "커플")
                or (room and str(getattr(room, "romance_state", "") or "") == "커플")
            )
            if not is_couple:
                continue
            rel_score = _score_of(ctx)
            talk_count = len(ctx.get("recent_12h") or [])
            should_breakup = rel_score <= 25 or (rel_score <= 40 and talk_count < 2)
            if not should_breakup:
                continue
            breakup_ids.append(owner.id)
            if pair:
                pair.romance_state = "싱글"
                pair.romance_partner_label = None
                pair.updated_at = datetime.utcnow()
            if room:
                room.romance_state = "싱글"
                room.romance_partner_label = None
                room.romance_decided_at = now

        if breakup_ids:
            return {
                "mode": "breakup",
                "selected_user_id": None,
                "breakup_user_ids": breakup_ids,
            }
        return {"mode": "noop", "selected_user_id": current_partner_user_id}

    async def _run_relationship_review(self, run_label: str, write_diary: bool = False) -> tuple[int, int]:
        db = SessionLocal()
        persona_count = 0
        rel_updates = 0
        try:
            now = datetime.now(KST)
            personas = db.query(Persona).all()

            for persona in personas:
                rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()
                if not rooms:
                    continue

                per_user_contexts = []
                room_contexts = []
                registry_map = {
                    e.get("user_id"): e
                    for e in (persona.user_registry or [])
                    if isinstance(e, dict) and e.get("user_id") is not None
                }

                for room in rooms:
                    owner = room.owner or db.query(User).filter(User.id == room.owner_id).first()
                    if not owner:
                        continue

                    recent_12h = self._extract_recent_dialogue_12h(room, now, limit=36)
                    medium_summaries = self._extract_medium_summaries(room, persona, owner.id, limit=10)
                    fact_warehouse = list(room.fact_warehouse or [])[-30:]

                    persona_payload = {
                        "persona_traits": engine.build_persona_traits(persona),
                    }
                    user_payload = {
                        "username": owner.username,
                        "display_name": owner.display_name or owner.username,
                    }
                    context_payload = {
                        "current_relationship": room.relationship_category or "낯선 사람",
                        "existing_summary_3line": getattr(room, "relationship_summary_3line", "") or "",
                        "recent_dialogue_12h": recent_12h,
                        "medium_summaries": medium_summaries,
                        "fact_warehouse": fact_warehouse,
                    }

                    review = await engine.evaluate_user_relationship_snapshot(
                        persona_payload=persona_payload,
                        user_payload=user_payload,
                        context_payload=context_payload,
                    )

                    category = str(review.get("relationship_category") or "낯선 사람").strip()
                    summary_3line = str(review.get("summary_3line") or "").strip()
                    delta = int(review.get("relationship_score_delta", 0))
                    delta = max(-10, min(10, delta))

                    pair = self._upsert_user_pair_relationship(
                        db=db,
                        room=room,
                        category=category,
                        summary_3line=summary_3line,
                        delta=delta,
                        now=now,
                    )
                    rel_updates += 1

                    reg = registry_map.get(owner.id) or {
                        "user_id": owner.id,
                        "display_name": owner.display_name or owner.username,
                        "relationship": "낯선 사람",
                        "last_talked": None,
                        "memo": "",
                    }
                    reg["relationship"] = category
                    reg["memo"] = summary_3line[:300]
                    registry_map[owner.id] = reg

                    per_user_contexts.append({
                        "user_id": owner.id,
                        "user_name": owner.display_name or owner.username,
                        "relationship": category,
                        "relationship_score": int(pair.relationship_score if pair and pair.relationship_score is not None else (room.v_relationship or 20)),
                        "summary_3line": summary_3line,
                        "romance_state": (pair.romance_state if pair else room.romance_state) or "싱글",
                        "romance_partner_label": (pair.romance_partner_label if pair else room.romance_partner_label),
                        "confession_pending": bool((pair.confession_pending if pair else room.confession_pending)),
                        "confession_candidates": list((pair.confession_candidates if pair else room.confession_candidates) or [])[-8:],
                        "recent_dialogue_12h": recent_12h,
                        "medium_summaries": medium_summaries,
                        "fact_warehouse": fact_warehouse,
                    })
                    room_contexts.append({
                        "room": room,
                        "owner": owner,
                        "pair": pair,
                        "recent_12h": recent_12h,
                        "medium_summaries": medium_summaries,
                        "fact_warehouse": fact_warehouse,
                    })

                persona.user_registry = list(registry_map.values())

                if write_diary:
                    await self._resolve_romance_for_persona(
                        db=db,
                        persona=persona,
                        room_contexts=room_contexts,
                        now=now,
                    )

                db.commit()
                persona_count += 1
                await asyncio.sleep(0.15)

            print(f">> RELATIONSHIP_REVIEW[{run_label}]: personas={persona_count}, updates={rel_updates}, midnight_finalize={write_diary}")
            return persona_count, rel_updates
        except Exception as e:
            db.rollback()
            print(f"relationship review error[{run_label}]: {e}")
            return persona_count, rel_updates
        finally:
            db.close()

    async def noon_relationship_job(self):
        await self._run_relationship_review(run_label="12:00", write_diary=False)

    async def midnight_relationship_job(self):
        db = SessionLocal()
        try:
            gallery_added = await self._nightly_gallery_fill(db)
            print(f">> MIDNIGHT: nightly gallery added={gallery_added}")
        finally:
            db.close()
        await self._run_relationship_review(run_label="00:00", write_diary=True)

    def _normalize_persona_gallery(self, persona: Persona) -> list[dict]:
        raw = persona.profile_images if isinstance(persona.profile_images, list) else []
        gallery: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                url = item.strip()
                if url:
                    gallery.append({"url": url})
                continue
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                gallery.append({
                    "url": url,
                    "prompt": str(item.get("prompt") or "").strip(),
                    "shot_type": str(item.get("shot_type") or "").strip(),
                    "model": str(item.get("model") or "").strip(),
                    "created_at": str(item.get("created_at") or "").strip(),
                })

        primary = str(persona.profile_image_url or "").strip()
        if primary and not any(str(x.get("url") or "").strip() == primary for x in gallery):
            first_shot = "face_closeup" if persona.gender == "여성" else "outdoor_full_body"
            gallery.insert(0, {
                "url": primary,
                "prompt": str(persona.image_prompt or "").strip(),
                "shot_type": first_shot,
                "model": "",
                "created_at": ""
            })
        dedup = []
        seen = set()
        for item in gallery:
            u = str(item.get("url") or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            dedup.append(item)
        return dedup[:self.max_profile_photos]

    def _shot_plan(self, gender: str) -> list[str]:
        if gender == "여성":
            return ["face_closeup", "full_body", "prop"]
        return ["outdoor_full_body", "hobby", "social"]

    def _shot_prompt(self, persona: Persona, shot_type: str) -> str:
        base = f"{persona.age} years old, korean, candid dating profile photography, natural smartphone look"
        if shot_type == "face_closeup":
            return f"close-up face portrait, soft natural light, eye contact, {base}"
        if shot_type == "full_body":
            return f"full body standing shot, natural pose, clean background, {base}"
        if shot_type == "prop":
            return f"half-body with a meaningful prop, lifestyle composition, {base}"
        if shot_type == "outdoor_full_body":
            return f"outdoor full body shot, street or park background, natural pose, {base}"
        if shot_type == "hobby":
            return f"candid hobby scene, actively doing a hobby, lifestyle framing, {base}"
        if shot_type == "social":
            return f"friendly social vibe, interaction moment, bright candid style, {base}"
        return f"candid profile photo, {base}"

    async def _generate_persona_gallery_image(self, persona: Persona, prompt: str) -> tuple[str | None, str | None]:
        def _extract_image_url(result):
            if not result or not isinstance(result, dict):
                return None
            if isinstance(result.get("images"), list) and result["images"]:
                first = result["images"][0]
                if isinstance(first, dict):
                    return first.get("url")
            data_obj = result.get("data")
            if isinstance(data_obj, dict) and isinstance(data_obj.get("images"), list) and data_obj["images"]:
                first = data_obj["images"][0]
                if isinstance(first, dict):
                    return first.get("url")
            return None

        if persona.face_base_url:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        fal_client.subscribe,
                        "fal-ai/nano-banana/edit",
                        arguments={
                            "prompt": prompt,
                            "image_urls": [persona.face_base_url],
                            "num_images": 1,
                            "aspect_ratio": "1:1"
                        }
                    ),
                    timeout=35
                )
                url = _extract_image_url(result)
                if url:
                    return url, "fal-ai/nano-banana/edit"
            except Exception:
                pass

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/flux-2",
                    arguments={"prompt": prompt, "image_size": "square"}
                ),
                timeout=35
            )
            url = _extract_image_url(result)
            if url:
                return url, "fal-ai/flux-2"
        except Exception:
            return None, None
        return None, None

    async def _nightly_gallery_fill(self, db: Session):
        target_quota = self.nightly_gallery_add_limit
        added = 0
        touched_persona_ids = set()

        personas = db.query(Persona).all()
        valid_personas = [p for p in personas if p.profile_image_url]

        while added < target_quota:
            candidates = []
            for p in valid_personas:
                gallery = self._normalize_persona_gallery(p)
                if len(gallery) < self.max_profile_photos:
                    candidates.append((len(gallery), p.id, p, gallery))
            
            if not candidates:
                break
            
            candidates.sort(key=lambda x: (x[0], x[1]))
            round_added = 0

            for _, _, persona, gallery in candidates:
                if added >= target_quota:
                    break
                
                plan = self._shot_plan(persona.gender)
                used_types = {str(item.get("shot_type") or "").strip() for item in gallery}
                next_type = next((x for x in plan if x not in used_types), None)
                if not next_type:
                    continue

                prompt = self._shot_prompt(persona, next_type)
                image_url, model_used = await self._generate_persona_gallery_image(persona, prompt)
                if not image_url:
                    continue

                gallery.append({
                    "url": image_url,
                    "prompt": prompt,
                    "shot_type": next_type,
                    "model": model_used or "",
                    "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                })
                persona.profile_images = gallery[:self.max_profile_photos]
                if not persona.profile_image_url and persona.profile_images:
                    persona.profile_image_url = persona.profile_images[0]["url"]
                
                added += 1
                round_added += 1
                touched_persona_ids.add(persona.id)
                await asyncio.sleep(0.3)
            
            if round_added == 0:
                break

        if added > 0:
            db.commit()
            
        print(f">> NIGHTLY_GALLERY: target_quota={target_quota}, gallery_added={added}, unique_personas_touched={len(touched_persona_ids)}")
        return added

    async def daily_briefing_job(self):
        """
        [v3.0.0] 매일 자정에 실행되는 일괄 업데이트 작업.
        
        기존 버그 수정: 방(ChatRoom) 단위가 아닌 페르소나(Persona) 단위로 루프.
        한 이브의 모든 방에서 중기 기억을 수집하여 통합 일기에 반영.
        """
        print(">> SCHEDULER: Starting Daily Briefing (v3.0.0 Persona-based)...")
        db = SessionLocal()
        try:
            # 페르소나 단위로 처리 (핵심 변경)
            personas = db.query(Persona).all()
            
            count = 0
            for persona in personas:
                if not persona:
                    continue
                
                # 해당 이브의 모든 채팅방 조회
                rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()
                if not rooms:
                    continue
                
                # [v3.0.0] 모든 방에서 중기 기억 수집 (기억 소실 방지)
                aggregated_logs = []
                for room in rooms:
                    v_state = volatile_memory.get(room.id, {})
                    medium_logs = v_state.get('medium_term_logs', [])
                    if medium_logs:
                        owner = db.query(User).filter(User.id == room.owner_id).first()
                        owner_name = owner.display_name or owner.username if owner else "Unknown"
                        for log in medium_logs:
                            aggregated_logs.append(f"[{owner_name}] {log}")
                
                # 첫 번째 방을 기준으로 sync_eve_life 실행 (스케줄 갱신은 한 번만)
                try:
                    primary_room = rooms[0]
                    life_result = await engine.sync_eve_life(room_id=primary_room.id, db=db)
                    
                    # [v3.1.0] daily_events + diary를 shared_memory에 저장
                    if life_result:
                        daily_events = life_result.get('daily_events', [])
                        diary_entry = life_result.get('diary_entry', '')
                        diary_is_public = life_result.get('diary_is_public', True)
                        
                        memory_entries = []
                        
                        # 일과 이벤트 → category: daily_event
                        for evt in daily_events:
                            if isinstance(evt, dict):
                                memory_entries.append({
                                    "fact": evt.get("event", ""),
                                    "is_public": evt.get("is_public", True),
                                    "category": "daily_event"
                                })
                        
                        # 일기 → category: diary
                        if diary_entry:
                            memory_entries.append({
                                "fact": diary_entry,
                                "is_public": diary_is_public,
                                "category": "diary"
                            })
                        
                        if memory_entries:
                            update_shared_memory(
                                db, persona.id, memory_entries,
                                source_user_id=None  # 유저 무관 (이브 자체 경험)
                            )
                    
                    # [v3.0.0] user_registry의 관계 정보를 ChatRoom에서 동기화
                    registry = list(persona.user_registry or [])
                    registry_map = {e.get("user_id"): e for e in registry}
                    
                    for room in rooms:
                        uid = room.owner_id
                        if uid in registry_map:
                            registry_map[uid]["relationship"] = room.relationship_category or "낯선 사람"
                        else:
                            owner = db.query(User).filter(User.id == uid).first()
                            if owner:
                                registry_map[uid] = {
                                    "user_id": uid,
                                    "display_name": owner.display_name or owner.username,
                                    "relationship": room.relationship_category or "낯선 사람",
                                    "last_talked": None,
                                    "memo": ""
                                }
                    
                    persona.user_registry = list(registry_map.values())
                    db.commit()
                    
                    count += 1
                    # API 부하 분산을 위한 2초 대기
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"Error processing persona {persona.id}: {e}")
            
            gallery_added = await self._nightly_gallery_fill(db)
            print(f">> SCHEDULER: Completed Daily Briefing. Processed {count} personas, gallery_added={gallery_added}.")
                
        except Exception as e:
            print(f"Scheduler Critical Error: {e}")
        finally:
            db.close()

    async def publish_scheduled_posts_job(self):
        """매분 실행: 예약 시간이 지난 포스트를 is_published=True로 전환"""
        db = SessionLocal()
        try:
            now = datetime.now(KST)
            all_locations = db.query(MapLocation).all()
            posts = db.query(FeedPost).filter(
                FeedPost.is_published == False,
                FeedPost.scheduled_at <= now
            ).all()
            for post in posts:
                if post.persona_id and not (post.location_name or post.location_district):
                    persona = db.query(Persona).filter(Persona.id == post.persona_id).first()
                    if persona:
                        location_id, location_name, location_district = self._resolve_persona_location_for_time(
                            db=db,
                            persona=persona,
                            when=now,
                            all_locations=all_locations,
                        )
                        post.location_id = location_id
                        post.location_name = location_name
                        post.location_district = location_district
                post.is_published = True
                print(f">> FEED: Published scheduled post {post.id} by persona {post.persona_id}")
            db.commit()
        except Exception as e:
            print(f"publish_scheduled_posts_job Error: {e}")
        finally:
            db.close()

    def _hourly_budgets(self, db: Session, now: datetime) -> tuple[int, int]:
        """Decide hourly post/comment budget with simple hour and activity weighting."""
        active_users_24h = db.query(User).filter(User.last_active >= (now - timedelta(hours=24))).count()
        total_personas = db.query(Persona).count()
        base_posts = max(self.hourly_post_min, min(self.hourly_post_max, total_personas // 25 + 3))
        base_comments = max(self.hourly_comment_min, min(self.hourly_comment_max, total_personas // 12 + 8))
        hour = now.hour
        if hour in (20, 21, 22):
            base_posts += 2
            base_comments += 4
        elif hour in (2, 3, 4, 5):
            base_posts = max(self.hourly_post_min, base_posts - 2)
            base_comments = max(self.hourly_comment_min, base_comments - 3)
        if active_users_24h <= 2:
            base_posts = max(self.hourly_post_min, base_posts - 1)
            base_comments = max(self.hourly_comment_min, base_comments - 2)
        return (
            max(self.hourly_post_min, min(self.hourly_post_max, base_posts)),
            max(self.hourly_comment_min, min(self.hourly_comment_max, base_comments)),
        )

    def _pick_candidate_personas(self, db: Session, now: datetime, limit: int = 80) -> list[Persona]:
        """Pick personas likely to feel active while limiting repetition."""
        all_personas = db.query(Persona).all()
        if not all_personas:
            return []
        recent_cut = now - timedelta(hours=3)
        scored = []
        for per in all_personas:
            recent_posts = db.query(FeedPost).filter(
                FeedPost.persona_id == per.id,
                FeedPost.created_at >= recent_cut
            ).count()
            recent_comments = db.query(FeedComment).filter(
                FeedComment.persona_id == per.id,
                FeedComment.created_at >= recent_cut
            ).count()
            cooldown_penalty = (recent_posts * 3) + recent_comments
            freshness_bonus = random.randint(0, 4)
            score = max(0, 10 - cooldown_penalty + freshness_bonus)
            scored.append((score, per))
        scored.sort(key=lambda t: t[0], reverse=True)
        top = [per for _, per in scored[: max(limit, 10)]]
        random.shuffle(top)
        return top[:limit]

    def _build_context_note(self, db: Session, persona: Persona) -> dict:
        """Internal-only note for what recent context to use in this action."""
        room = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).order_by(desc(ChatRoom.id)).first()
        last_user_chat = ""
        if room and room.history:
            for msg in reversed(room.history[-20:]):
                if msg.get("role") == "user":
                    last_user_chat = str(msg.get("content") or "")[:140]
                    break
        recent_post = db.query(FeedPost).filter(
            FeedPost.persona_id == persona.id
        ).order_by(desc(FeedPost.id)).first()
        recent_self_feed = str(recent_post.content)[:140] if recent_post else ""
        diary_hint = ""
        shared_journal = list(persona.shared_journal or [])
        if shared_journal:
            diary_hint = str(shared_journal[-1].get("content", ""))[:140]
        return {
            "last_user_chat": last_user_chat,
            "recent_self_feed": recent_self_feed,
            "diary_hint": diary_hint,
            "pick_rule": "Use at most 1-2 items to keep posts natural.",
        }

    def _build_persona_feed_input(self, db: Session, persona: Persona) -> dict:
        """Collect persona + recent user/eve conversation snippets for feed planning."""
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

        # Normalize today's schedule payload to a compact, prompt-friendly shape.
        today_schedule = {}
        raw_schedule = persona.daily_schedule or {}
        if isinstance(raw_schedule, dict):
            today_schedule = {
                "wake_time": raw_schedule.get("wake_time", ""),
                "sleep_time": raw_schedule.get("sleep_time", ""),
                "daily_tasks": list(raw_schedule.get("daily_tasks", []) or [])[:8],
            }
        elif isinstance(raw_schedule, list):
            compact = []
            for item in raw_schedule[:12]:
                if not isinstance(item, dict):
                    continue
                compact.append({
                    "time": item.get("time", ""),
                    "activity": item.get("activity", ""),
                })
            today_schedule = {"timeline": compact}

        recent_user_chats = []
        rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).order_by(desc(ChatRoom.id)).limit(4).all()
        for room in rooms:
            owner_name = room.owner.display_name if room.owner and room.owner.display_name else (room.owner.username if room.owner else f"user-{room.owner_id}")
            for msg in reversed((room.history or [])[-20:]):
                if msg.get("role") == "user":
                    recent_user_chats.append({
                        "user": owner_name,
                        "text": str(msg.get("content") or "")[:140]
                    })
                    break
            if len(recent_user_chats) >= 3:
                break

        related_users = []
        seen_user = set()
        for room in db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).order_by(desc(ChatRoom.id)).limit(12).all():
            owner = room.owner
            if not owner:
                continue
            if owner.id in seen_user:
                continue
            seen_user.add(owner.id)
            related_users.append({
                "user_id": owner.id,
                "name": owner.display_name or owner.username,
                "relationship": room.relationship_category or "",
            })
            if len(related_users) >= 10:
                break

        for reg in list(persona.user_registry or []):
            if not isinstance(reg, dict):
                continue
            uid = reg.get("user_id")
            if uid in seen_user:
                continue
            seen_user.add(uid)
            related_users.append({
                "user_id": uid,
                "name": reg.get("display_name") or (f"user-{uid}" if uid else "user"),
                "relationship": reg.get("relationship", ""),
            })
            if len(related_users) >= 10:
                break

        recent_eve_chats = []
        rels = db.query(EveRelationship).filter(
            or_(EveRelationship.persona_a_id == persona.id, EveRelationship.persona_b_id == persona.id)
        ).order_by(desc(EveRelationship.last_talked)).limit(4).all()
        for rel in rels:
            summaries = list(rel.conversation_summaries or [])
            if not summaries:
                continue
            other_id = rel.persona_b_id if rel.persona_a_id == persona.id else rel.persona_a_id
            other = db.query(Persona).filter(Persona.id == other_id).first()
            recent_eve_chats.append({
                "with": other.name if other else f"eve-{other_id}",
                "summary": str(summaries[-1])[:160]
            })
            if len(recent_eve_chats) >= 3:
                break

        related_eves = []
        rels_for_list = db.query(EveRelationship).filter(
            or_(EveRelationship.persona_a_id == persona.id, EveRelationship.persona_b_id == persona.id)
        ).order_by(desc(EveRelationship.interaction_count), desc(EveRelationship.last_talked)).limit(12).all()
        other_ids = []
        for rel in rels_for_list:
            other_ids.append(rel.persona_b_id if rel.persona_a_id == persona.id else rel.persona_a_id)
        other_map = {}
        if other_ids:
            other_map = {p.id: p for p in db.query(Persona).filter(Persona.id.in_(list(set(other_ids)))).all()}
        for rel in rels_for_list:
            other_id = rel.persona_b_id if rel.persona_a_id == persona.id else rel.persona_a_id
            other = other_map.get(other_id)
            related_eves.append({
                "persona_id": other_id,
                "name": other.name if other else f"eve-{other_id}",
                "relationship": rel.relationship_type or "",
                "interactions": rel.interaction_count or 0,
            })
            if len(related_eves) >= 10:
                break

        my_last_feed = {}
        recent_self_post = db.query(FeedPost).filter(
            FeedPost.persona_id == persona.id,
            FeedPost.is_published == True
        ).order_by(desc(FeedPost.created_at)).first()
        if recent_self_post:
            my_last_feed = {
                "id": recent_self_post.id,
                "content": str(recent_self_post.content or "")[:220],
                "time": str(recent_self_post.created_at),
                "has_image": bool(getattr(recent_self_post, "image_url", None)),
            }

        return {
            "id": persona.id,
            "name": persona.name,
            "mbti": persona.mbti,
            "profile_details": persona.profile_details,
            "current_time_kst": now_kst,
            "today_schedule": today_schedule,
            "related_users": related_users,
            "related_eves": related_eves,
            "recent_user_chats": recent_user_chats,
            "recent_eve_chats": recent_eve_chats,
            "my_last_feed": my_last_feed,
        }

    async def _plan_hourly_actions(self, db: Session, now: datetime) -> tuple[int, int, int]:
        """Plan one hour worth of post/comment actions and enqueue them."""
        post_budget, comment_budget = self._hourly_budgets(db, now)
        candidates = self._pick_candidate_personas(db, now, limit=100)
        if not candidates:
            return (post_budget, comment_budget, 0)

        current_feed = db.query(FeedPost).filter(
            FeedPost.is_published == True
        ).order_by(desc(FeedPost.created_at)).limit(12).all()

        planned = 0
        actions_to_save: list[ScheduledAction] = []
        accepted_activities = []
        seen_persona_posts = set()
        seen_persona_comments = {}
        post_count = 0
        comment_count = 0
        hour_start = now.replace(minute=0, second=0, microsecond=0)

        for i in range(0, len(candidates), 10):
            batch = candidates[i:i + 10]
            batch_dicts = [self._build_persona_feed_input(db, e) for e in batch]
            activities = await engine.generate_feed_activity(batch_dicts, current_feed)
            for act in activities or []:
                pid = act.get("persona_id")
                atype = act.get("action")
                if not pid or atype not in ("post", "comment"):
                    continue
                content = str(act.get("content") or "").strip()
                if not content:
                    continue
                persona = next((e for e in batch if e.id == pid), None)
                if not persona:
                    continue
                if atype == "post":
                    if post_count >= post_budget or pid in seen_persona_posts:
                        continue
                    seen_persona_posts.add(pid)
                    post_count += 1
                else:
                    if comment_count >= comment_budget:
                        continue
                    seen_persona_comments[pid] = seen_persona_comments.get(pid, 0) + 1
                    if seen_persona_comments[pid] > 2:
                        continue
                    comment_count += 1

                run_minute = random.randint(0, 59)
                run_second = random.randint(0, 59)
                run_at = hour_start + timedelta(minutes=run_minute, seconds=run_second)
                context_note = self._build_context_note(db, persona)
                target_post_id = act.get("target_post_id")
                if atype == "comment" and not target_post_id and current_feed:
                    pick_pool = [p for p in current_feed if p.persona_id != pid] or current_feed
                    target_post_id = random.choice(pick_pool).id
                actions_to_save.append(
                    ScheduledAction(
                        run_at=run_at,
                        action_type=atype,
                        persona_id=pid,
                        target_post_id=target_post_id,
                        plan_meta={
                            "content": content,
                            "tagged_persona_ids": act.get("tagged_persona_ids", []),
                            "tag_activity": act.get("tag_activity", ""),
                            "generate_image": bool(act.get("generate_image", False)),
                            "image_prompt": act.get("image_prompt", ""),
                            "context_note": context_note,
                        },
                        status="scheduled",
                    )
                )
                accepted_activities.append({
                    "persona_id": pid,
                    "action": atype,
                    "target_post_id": target_post_id,
                    "target_persona_id": act.get("target_persona_id"),
                    "tagged_persona_ids": act.get("tagged_persona_ids", []),
                    "content": content,
                })
                planned += 1
                if post_count >= post_budget and comment_count >= comment_budget:
                    break
            if post_count >= post_budget and comment_count >= comment_budget:
                break

        if actions_to_save:
            db.add_all(actions_to_save)
            # Preserve existing social-graph update behavior based on chosen feed actions.
            engine.update_eve_relationships_from_feed(accepted_activities, db)
            db.commit()
        return (post_count, comment_count, planned)

    async def _execute_planned_actions_job(self):
        """Execute due scheduled actions created by hourly planner."""
        db = SessionLocal()
        try:
            now = datetime.now(KST)
            due = db.query(ScheduledAction).filter(
                ScheduledAction.status == "scheduled",
                ScheduledAction.run_at <= now
            ).order_by(ScheduledAction.run_at.asc()).limit(40).all()
            if not due:
                return
            executed = 0
            for item in due:
                item.status = "running"
                item.attempts += 1
                db.commit()
                try:
                    persona = db.query(Persona).filter(Persona.id == item.persona_id).first()
                    if not persona:
                        raise RuntimeError("persona not found")
                    meta = item.plan_meta or {}
                    act = {
                        "persona_id": item.persona_id,
                        "action": item.action_type,
                        "target_post_id": item.target_post_id,
                        "content": meta.get("content", ""),
                        "tagged_persona_ids": meta.get("tagged_persona_ids", []),
                        "tag_activity": meta.get("tag_activity", ""),
                        "generate_image": bool(meta.get("generate_image", False)),
                        "image_prompt": meta.get("image_prompt", ""),
                        "delay_minutes": 0,
                    }
                    await self._process_feed_activity(0, act, db, now.strftime("%H:00"))
                    item.status = "done"
                    item.error = None
                    item.executed_at = now
                    executed += 1
                    db.commit()
                except Exception as e:
                    item.status = "failed" if item.attempts >= 3 else "scheduled"
                    item.error = str(e)[:500]
                    if item.status == "scheduled":
                        item.run_at = now + timedelta(minutes=min(10, item.attempts * 2))
                    db.commit()
            if executed:
                print(f">> SCHEDULER: Executed planned actions: {executed}")
        except Exception as e:
            print(f"_execute_planned_actions_job Error: {e}")
        finally:
            db.close()

    async def _process_feed_activity(self, count, act, db, current_hour):
        """개별 피드 액션 처리"""
        persona_id = act.get("persona_id")
        action = act.get("action")
        if action == "none" or not persona_id:
            return count

        persona = db.query(Persona).filter(Persona.id == persona_id).first()
        if not persona:
            return count
        all_locations = db.query(MapLocation).all()

        content = act.get("content", "")
        target_post_id = act.get("target_post_id")
        generate_image = act.get("generate_image", False)
        image_prompt = act.get("image_prompt", "")
        tagged_persona_ids = act.get("tagged_persona_ids", [])
        tag_activity = str(act.get("tag_activity", "") or "").strip()
        delay_minutes = int(act.get("delay_minutes", 0))

        if isinstance(tagged_persona_ids, (list, tuple)):
            tag_ids = []
            for raw in tagged_persona_ids:
                try:
                    tid = int(raw)
                except Exception:
                    continue
                if tid == persona_id or tid in tag_ids:
                    continue
                tag_ids.append(tid)
                if len(tag_ids) >= 2:
                    break
            tagged_persona_ids = tag_ids
        else:
            tagged_persona_ids = []
        if not tagged_persona_ids:
            tag_activity = ""

        # 정각 기준 delay 적용
        now = datetime.now(KST)
        scheduled_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=delay_minutes)
        if scheduled_at < now:
            scheduled_at = now  # 과거 시간 방지

        if action == "post":
            if not content:
                return count
            image_url = None
            if generate_image:
                print(f"      [Image] Generating via fal-ai/flux-2 text-to-image")
                ethnicity_prompt = _build_ethnicity_prompt(
                    getattr(persona, "white", 0),
                    getattr(persona, "black", 0),
                    getattr(persona, "asian", 0),
                )
                image_url = await engine.generate_feed_image_t2i(ethnicity_prompt, persona.gender, persona.age, image_prompt)

            location_id, location_name, location_district = self._resolve_persona_location_for_time(
                db=db,
                persona=persona,
                when=scheduled_at,
                all_locations=all_locations,
            )

            new_post = FeedPost(
                persona_id=persona_id,
                content=content,
                tagged_persona_ids=tagged_persona_ids,
                tag_activity=tag_activity[:120] if tag_activity else None,
                location_id=location_id,
                location_name=location_name,
                location_district=location_district,
                image_url=image_url,
                image_prompt=image_prompt if generate_image else None,
                scheduled_at=scheduled_at,
                is_published=False
            )
            db.add(new_post)
            self._apply_feed_activity_state_delta(db, persona_id)
            print(f"   [POST] {persona.name}: (delay {delay_minutes}m) {_safe_log_text(content)}...")
            count += 1

        elif action == "comment":
            # Validate/repair target post so comment actions do not get dropped.
            target_post = None
            try:
                if target_post_id is not None:
                    target_post = db.query(FeedPost).filter(
                        FeedPost.id == int(target_post_id),
                        FeedPost.is_published == True
                    ).first()
            except Exception:
                target_post = None

            if not target_post:
                candidates = db.query(FeedPost).filter(
                    FeedPost.is_published == True,
                    FeedPost.persona_id != persona_id
                ).order_by(desc(FeedPost.id)).limit(30).all()
                if not candidates:
                    return count
                target_post = random.choice(candidates)
            target_post_id = target_post.id
            if not content:
                return count

            if target_post.user_id:
                target_user = db.query(User).filter(User.id == target_post.user_id).first()
                if target_user:
                    pair = db.query(UserPersonaRelationship).filter(
                        UserPersonaRelationship.user_id == target_user.id,
                        UserPersonaRelationship.persona_id == persona_id
                    ).first()
                    rel_category = str((pair.relationship_category if pair else None) or "낯선 사람")
                    rel_score = int((pair.relationship_score if pair and pair.relationship_score is not None else 20))
                    rewritten = await engine.generate_relationship_aware_feed_comment(
                        persona_payload={
                            "persona_traits": engine.build_persona_traits(persona),
                        },
                        user_payload={
                            "id": target_user.id,
                            "name": target_user.display_name or target_user.username,
                            "profile_details": target_user.profile_details or {},
                        },
                        post_payload={
                            "id": target_post.id,
                            "content": str(target_post.content or ""),
                            "created_at": target_post.created_at.strftime("%Y-%m-%d %H:%M") if target_post.created_at else "",
                            "image_url": getattr(target_post, "image_url", None),
                            "image_prompt": str(getattr(target_post, "image_prompt", "") or ""),
                        },
                        relationship_payload={
                            "category": rel_category,
                            "score": rel_score,
                        },
                        draft_comment=str(content or ""),
                    )
                    if rewritten:
                        content = rewritten

            new_comment = FeedComment(
                post_id=target_post_id,
                persona_id=persona_id,
                content=content
            )
            db.add(new_comment)
            self._apply_feed_activity_state_delta(db, persona_id)
            print(f"   [COMMENT] {persona.name} -> Post {target_post_id}: {_safe_log_text(content)}...")

        return count
    
    async def _run_social_simulations_for_batch(self, active_eves: list, db: Session):
        """이 배치에 포함된 이브들과 관계된(친구/지인) 쌍에 대해 대화 요약을 생성합니다."""
        if not active_eves:
            return
            
        active_ids = {e.id for e in active_eves}
        from models import EveRelationship
        from sqlalchemy import or_
        import engine
        
        # 자신이 속한 지인/친구 관계 조회
        relationships = db.query(EveRelationship).filter(
            EveRelationship.relationship_type.in_(["지인", "친구"]),
            or_(
                EveRelationship.persona_a_id.in_(active_ids),
                EveRelationship.persona_b_id.in_(active_ids)
            )
        ).all()
        
        for rel in relationships:
            # 두 페르소나 객체 조회
            from models import Persona
            p_a = db.query(Persona).filter(Persona.id == rel.persona_a_id).first()
            p_b = db.query(Persona).filter(Persona.id == rel.persona_b_id).first()
            
            if not p_a or not p_b:
                continue
                
            persona_a_dict = {
                "persona_traits": engine.build_persona_traits(p_a),
            }
            persona_b_dict = {
                "persona_traits": engine.build_persona_traits(p_b),
            }
            
            result = await engine.simulate_eve_conversation_summary(persona_a_dict, persona_b_dict, rel, db)
            
            summary = result.get("summary")
            fact_a = result.get("new_fact_for_a")
            fact_b = result.get("new_fact_for_b")
            
            if summary:
                current_summaries = rel.conversation_summaries or []
                current_summaries.append(summary)
                rel.conversation_summaries = current_summaries[-20:]

            if fact_a:
                update_shared_memory(db, rel.persona_a_id, [{"fact": fact_a, "is_public": True, "category": "fact"}], source_user_id=None)
            if fact_b:
                update_shared_memory(db, rel.persona_b_id, [{"fact": fact_b, "is_public": True, "category": "fact"}], source_user_id=None)

            rel.last_talked = datetime.now(KST)
            db.commit()
            print(f"   [SOCIAL SIM] {p_a.name} & {p_b.name} social simulation complete")
            await asyncio.sleep(0.5)

    async def _run_feed_to_dm_bridge(self, active_eves: list, db: Session) -> int:
        """At feed timing, each active EVE proactively DMs all connected non-admin users."""
        if not active_eves:
            return 0
        dm_sent = 0
        users = {u.id: u for u in db.query(User).filter(User.is_admin == False).all()}
        if not users:
            return 0
        for persona in active_eves:
            rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()
            sent_for_persona = 0
            seen_user_ids = set()
            for room in rooms:
                uid = int(room.owner_id)
                if uid in seen_user_ids:
                    continue
                seen_user_ids.add(uid)
                user = users.get(uid)
                if not user:
                    continue
                sent = await engine.send_feed_timing_dm_to_connected_user(persona, user, db)
                if sent:
                    dm_sent += 1
                    sent_for_persona += 1
                await asyncio.sleep(0.08)
            if sent_for_persona:
                print(f"   [FEED->DM] {persona.name} proactive DMs sent: {sent_for_persona}")
        return dm_sent

    async def hourly_feed_job(self):
        """Generate hourly feed/social actions for active Eves."""
        print(">> SCHEDULER: Starting Hourly Feed Generation...")
        db = SessionLocal()
        try:
            now = datetime.now(KST)
            current_hour = now.strftime("%H:00")
            all_personas = db.query(Persona).all()
            moved_count = self._sync_persona_locations_for_hour(db, all_personas, now)
            if moved_count:
                db.commit()
                print(f">> MAP: hourly location sync moved={moved_count} at {current_hour}")

            post_budget, comment_budget, planned_count = await self._plan_hourly_actions(db, now)
            active_for_social = self._pick_candidate_personas(db, now, limit=20)
            set_ticker_active_eve_count(len(active_for_social))
            if active_for_social:
                await self._run_social_simulations_for_batch(active_for_social, db)
            dm_sent = await self._run_feed_to_dm_bridge(active_for_social, db)
            print(
                f">> SCHEDULER: Hourly plan created at {current_hour} "
                f"(posts={post_budget}, comments={comment_budget}, queued={planned_count}, dm={dm_sent})"
            )

        except Exception as e:
            print(f"hourly_feed_job Error: {e}")
        finally:
            db.close()

    def start(self):
        # 관계 정의 스냅샷: 정오/자정
        self.scheduler.add_job(self.noon_relationship_job, CronTrigger(hour=12, minute=0, timezone=KST))
        self.scheduler.add_job(self.midnight_relationship_job, CronTrigger(hour=0, minute=0, timezone=KST))
        
        # [Phase 2] 매시 정각: 피드 활동 생성
        self.scheduler.add_job(self.hourly_feed_job, CronTrigger(minute=0, timezone=KST))
        
        # [Phase 2] 매분: 예약 포스트 발행 감시
        self.scheduler.add_job(self.publish_scheduled_posts_job, CronTrigger(minute='*', timezone=KST))
        # Hourly planner queue execution (also runs every minute)
        self.scheduler.add_job(self._execute_planned_actions_job, CronTrigger(minute='*', timezone=KST))
        
        self.scheduler.start()
        print(">> SCHEDULER: Started (mode=hourly-planner, relationship checkpoints at 12:00/00:00 KST)")
