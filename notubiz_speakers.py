#!/usr/bin/env python3
"""
NotUBiz Speaker Mapper — Haalt sprekerslijst + tijdlijn op via de NotUBiz API
en koppelt Whisper-segmenten automatisch aan echte namen.

Gebruik:
    from notubiz_speakers import fetch_speaker_timeline, assign_speakers

    # Haal tijdlijn op voor een specifiek event
    timeline = fetch_speaker_timeline(event_id=1462423)

    # Of zoek automatisch op basis van video-bestandsnaam
    timeline = fetch_speaker_timeline(org_id=281, video_filename="04.03.26 Amsterdam WV HZ.mp4")

    # Koppel Whisper-segmenten aan echte namen
    enriched = assign_speakers(segments, timeline)
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

API_BASE = "https://api.notubiz.nl"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ── API helpers ────────────────────────────────────────────────────────────

def _api_get(path: str, timeout: int = 20) -> dict:
    """GET request naar de NotUBiz API."""
    url = f"{API_BASE}{path}"
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _safe_list(obj) -> list:
    """Zorg dat een API-veld altijd een lijst is (soms dict als er 1 item is)."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    return []


# ── Lookup-functies ────────────────────────────────────────────────────────

def get_org_id_for_gemeente(gemeente: str) -> Optional[int]:
    """Zoek het NotUBiz organisation ID voor een gemeente.

    Probeert eerst de lokale gemeente_streams.json, dan de API.
    """
    # Probeer lokale cache
    streams_file = os.path.join(os.path.dirname(__file__), "output", "gemeente_streams.json")
    if os.path.exists(streams_file):
        with open(streams_file, "r") as f:
            streams = json.load(f)
        for entry in streams:
            if entry.get("gemeente", "").lower() == gemeente.lower():
                nid = entry.get("notubiz_id")
                if nid:
                    return int(nid)

    # Fallback: zoek via API
    data = _api_get("/organisations")
    orgs = _safe_list(data.get("organisations", {}).get("organisation", []))
    gemeente_lower = gemeente.lower()
    for org in orgs:
        name = org.get("name", "").lower()
        if gemeente_lower in name or name in gemeente_lower:
            return int(org.get("@attributes", {}).get("id", 0)) or None
    return None


def find_event_by_filename(org_id: int, video_filename: str) -> Optional[int]:
    """Zoek een event ID op basis van de video-bestandsnaam in de media."""
    data = _api_get(f"/organisations/{org_id}/events")
    events = _safe_list(data.get("events", {}).get("event", []))

    target = video_filename.lower().strip()

    for event in events:
        eid = int(event.get("@attributes", {}).get("id", 0))
        if not eid:
            continue
        # Check event detail voor media
        try:
            detail = _api_get(f"/events/{eid}")
            ev = _safe_list(detail.get("event", []))
            if not ev:
                continue
            videos = _safe_list(ev[0].get("media", {}).get("video", []))
            for v in videos:
                if v.get("filename", "").lower().strip() == target:
                    return eid
        except Exception:
            continue
    return None


def find_event_by_date_title(org_id: int, date: str, title_query: str = "") -> Optional[int]:
    """Zoek een event op basis van datum (YYYY-MM-DD) en optioneel deel van de titel."""
    data = _api_get(f"/organisations/{org_id}/events")
    events = _safe_list(data.get("events", {}).get("event", []))

    query_lower = title_query.lower() if title_query else ""

    for event in events:
        attrs = event.get("@attributes", {})
        if attrs.get("date") == date:
            if not query_lower or query_lower in event.get("title", "").lower():
                return int(attrs.get("id", 0)) or None
    return None


# ── Speaker timeline ──────────────────────────────────────────────────────

def fetch_speaker_timeline(
    event_id: Optional[int] = None,
    org_id: Optional[int] = None,
    video_filename: Optional[str] = None,
    gemeente: Optional[str] = None,
    date: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Haal de volledige sprekerstijdlijn op voor een vergadering.

    Geeft een dict terug met:
        - speakers: {speaker_id: {name, label, function, party, ...}}
        - turns: [{start_time, end_time, speaker_id, speaker_label}, ...]
        - event_title: str
        - event_url: str

    Zoekstrategieën (eerste die matcht):
        1. Direct via event_id
        2. org_id + video_filename
        3. gemeente + video_filename
        4. org_id + date + title
        5. gemeente + date + title
    """
    # Bepaal event_id als dat nog niet is opgegeven
    if event_id is None:
        if org_id is None and gemeente:
            org_id = get_org_id_for_gemeente(gemeente)
            if org_id is None:
                raise ValueError(f"Gemeente '{gemeente}' niet gevonden in NotUBiz")

        if org_id and video_filename:
            event_id = find_event_by_filename(org_id, video_filename)
        elif org_id and date:
            event_id = find_event_by_date_title(org_id, date, title or "")

    if event_id is None:
        raise ValueError("Geen event gevonden. Geef event_id, of org_id/gemeente + video_filename/date.")

    # Haal event detail op
    data = _api_get(f"/events/{event_id}")
    event = _safe_list(data.get("event", []))
    if not event:
        raise ValueError(f"Event {event_id} niet gevonden")
    event = event[0]

    # Bouw speaker_id → info lookup
    raw_speakers = _safe_list(event.get("speakers", {}).get("speaker", []))
    speakers = {}
    for s in raw_speakers:
        sid = s.get("@attributes", {}).get("id")
        if sid is None:
            continue
        sid = int(sid)
        name = s.get("name", "").strip().rstrip(",")
        party = s.get("party", {}).get("name", "")
        function = s.get("function", "")
        firstname = s.get("firstname", "").strip()
        lastname = s.get("lastname", "").strip()

        # Maak een leesbaar label
        if party and party != "Geen partij":
            label = f"{name} ({party})"
        elif function:
            label = f"{name} ({function})"
        else:
            label = name

        speakers[sid] = {
            "name": name,
            "label": label,
            "firstname": firstname,
            "lastname": lastname,
            "function": function,
            "party": party if party != "Geen partij" else "",
            "photo": s.get("photo", ""),
        }

    # Verzamel alle speaker_indexation entries uit alle agendapunten
    agenda_items = _safe_list(event.get("agenda", {}).get("agendaitem", []))
    raw_turns = []

    for item in agenda_items:
        si = item.get("speaker_indexation", {})
        indices = _safe_list(si.get("speaker_index", []))
        for idx in indices:
            attrs = idx.get("@attributes", {})
            speaker_id = attrs.get("speaker_id")
            start_time = attrs.get("start_time")
            if speaker_id is not None and start_time is not None:
                raw_turns.append({
                    "start_time": int(start_time),
                    "speaker_id": int(speaker_id),
                })

    # Sorteer op start_time en voeg end_time toe
    raw_turns.sort(key=lambda x: x["start_time"])
    turns = []
    for i, turn in enumerate(raw_turns):
        sid = turn["speaker_id"]
        speaker_info = speakers.get(sid, {"label": f"Spreker {sid}", "name": f"Spreker {sid}"})
        end_time = raw_turns[i + 1]["start_time"] if i + 1 < len(raw_turns) else None
        turns.append({
            "start_time": turn["start_time"],
            "end_time": end_time,
            "speaker_id": sid,
            "speaker_label": speaker_info["label"],
            "speaker_name": speaker_info["name"],
        })

    return {
        "event_id": event_id,
        "event_title": event.get("title", ""),
        "event_url": event.get("url", ""),
        "speakers": speakers,
        "turns": turns,
    }


# ── Speaker matching ──────────────────────────────────────────────────────

def assign_speakers(segments: list, timeline: dict) -> list:
    """Koppel Whisper-segmenten aan echte sprekernamen via de NotUBiz-tijdlijn.

    Args:
        segments: lijst van dicts met minstens 'start' (seconds) en 'text'
        timeline: resultaat van fetch_speaker_timeline()

    Returns:
        Dezelfde segmenten, maar met 'speaker' vervangen door de echte naam
        en extra velden 'party' en 'function' toegevoegd.
    """
    turns = timeline.get("turns", [])
    speakers = timeline.get("speakers", {})

    if not turns:
        return segments

    for seg in segments:
        seg_start = seg.get("start", 0)

        # Zoek de turn die op dit moment actief is
        matched_turn = None
        for turn in turns:
            t_start = turn["start_time"]
            t_end = turn["end_time"]
            if t_end is None:
                # Laatste turn — geldig tot het einde
                if seg_start >= t_start:
                    matched_turn = turn
            elif t_start <= seg_start < t_end:
                matched_turn = turn
                break

        if matched_turn:
            sid = matched_turn["speaker_id"]
            speaker_info = speakers.get(sid, {})
            seg["speaker"] = matched_turn["speaker_label"]
            seg["speaker_name"] = matched_turn.get("speaker_name", "")
            seg["party"] = speaker_info.get("party", "")
            seg["function"] = speaker_info.get("function", "")
        else:
            seg["speaker"] = "Onbekend"

    return segments


# ── Utility ────────────────────────────────────────────────────────────────

def print_timeline(timeline: dict):
    """Print een leesbare tijdlijn."""
    turns = timeline.get("turns", [])
    print(f"\n{'=' * 60}")
    print(f"  {timeline.get('event_title', 'Onbekend')}")
    print(f"  {len(turns)} sprekerbeurten | {len(timeline.get('speakers', {}))} sprekers")
    print(f"{'=' * 60}")

    for i, turn in enumerate(turns):
        t = turn["start_time"]
        mins, secs = divmod(t, 60)
        end = turn["end_time"]
        if end:
            end_m, end_s = divmod(end, 60)
            duration = end - t
            end_str = f"{end_m}:{end_s:02d}"
        else:
            duration = "?"
            end_str = "einde"
        print(f"  {mins:3d}:{secs:02d} - {end_str:>6s}  ({str(duration):>4s}s)  {turn['speaker_label']}")


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Gebruik:")
        print("  python notubiz_speakers.py <event_id>")
        print("  python notubiz_speakers.py --org <org_id> --file <video_filename>")
        print("  python notubiz_speakers.py --gemeente Amsterdam --file '04.03.26 Amsterdam WV HZ.mp4'")
        sys.exit(1)

    # Simpele arg parsing
    args = sys.argv[1:]
    if args[0].isdigit():
        timeline = fetch_speaker_timeline(event_id=int(args[0]))
    elif "--gemeente" in args:
        gi = args.index("--gemeente")
        gemeente = args[gi + 1]
        filename = None
        if "--file" in args:
            fi = args.index("--file")
            filename = args[fi + 1]
        timeline = fetch_speaker_timeline(gemeente=gemeente, video_filename=filename)
    elif "--org" in args:
        oi = args.index("--org")
        org_id = int(args[oi + 1])
        filename = None
        if "--file" in args:
            fi = args.index("--file")
            filename = args[fi + 1]
        timeline = fetch_speaker_timeline(org_id=org_id, video_filename=filename)
    else:
        print(f"Onbekend argument: {args[0]}")
        sys.exit(1)

    print_timeline(timeline)

    # Optioneel: sla op als JSON
    out_file = f"output/speakers_{timeline['event_id']}.json"
    os.makedirs("output", exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    print(f"\nOpgeslagen: {out_file}")
