#!/usr/bin/env python3
"""
Gemeente Vergaderschema Extractor
Haalt aankomende vergaderingen + livestream-tijden op voor alle gemeenten.

Bronnen:
- NotUBiz API (159 gemeenten): volledige event data met datum, tijd, locatie
- iBabs/raadsinformatie: scraping van kalender-pagina's

Output: output/gemeente_schedules.json
"""

import json
import os
import re
import ssl
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

SSL_CTX = ssl.create_default_context()
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
TODAY = datetime.now().strftime('%Y-%m-%d')


def _fetch_json(url, timeout=12):
    """Haal JSON op van een URL."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        return json.loads(resp.read(131072).decode('utf-8'))
    except Exception:
        return None


def _fetch_html(url, timeout=10, max_bytes=65536):
    """Haal HTML op van een URL."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'nl,en;q=0.5',
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        return resp.read(max_bytes).decode('utf-8', errors='ignore')
    except Exception:
        return None


def extract_notubiz_schedule(gemeente_data):
    """Haal vergaderschema op via NotUBiz API."""
    notubiz_source = None
    for s in gemeente_data.get('sources', []):
        if s['platform'] == 'notubiz':
            notubiz_source = s
            break

    if not notubiz_source or 'notubiz_id' not in notubiz_source:
        return []

    org_id = notubiz_source['notubiz_id']
    data = _fetch_json(f'https://api.notubiz.nl/organisations/{org_id}/events')
    if not data:
        return []

    events = data.get('events', {}).get('event', [])
    if not isinstance(events, list):
        events = [events] if events else []

    meetings = []
    for event in events:
        attrs = event.get('@attributes', {})
        event_date = attrs.get('date', '')
        event_time = attrs.get('time', '')

        # Alleen toekomstige events
        if event_date < TODAY:
            continue

        category = event.get('category', {})
        cat_type = category.get('type', {}).get('label', '')

        meeting = {
            'date': event_date,
            'time': event_time,
            'title': event.get('title', ''),
            'type': cat_type,
            'category': category.get('title', ''),
            'location': event.get('location', ''),
            'url': event.get('url', '').split(' ')[0],  # URL bevat soms datum na spatie
            'chairman': event.get('chairman', ''),
            'source': 'notubiz_api',
            'has_livestream': cat_type in ('Raad', 'Commissie'),
        }
        meetings.append(meeting)

    # Sorteer op datum/tijd
    meetings.sort(key=lambda m: (m['date'], m['time']))
    return meetings


def extract_ibabs_schedule(gemeente_data):
    """Probeer vergaderschema te scrapen van iBabs-pagina."""
    ibabs_source = None
    for s in gemeente_data.get('sources', []):
        if s['platform'] == 'ibabs':
            ibabs_source = s
            break

    if not ibabs_source:
        return []

    base_url = ibabs_source['url'].rstrip('/')
    html = _fetch_html(f"{base_url}/calendar")
    if not html:
        return []

    meetings = []
    # iBabs kalender heeft typisch items met datum, titel en link
    # Zoek patronen als "2026-03-20" of "20 maart 2026" met bijbehorende titels
    date_patterns = re.findall(
        r'(\d{4}-\d{2}-\d{2})[^"]*?(?:title|naam|meeting)[^>]*>([^<]+)',
        html, re.IGNORECASE
    )
    for date_str, title in date_patterns:
        if date_str >= TODAY:
            meetings.append({
                'date': date_str,
                'time': '',
                'title': title.strip(),
                'type': '',
                'category': '',
                'location': '',
                'url': base_url,
                'chairman': '',
                'source': 'ibabs_scrape',
                'has_livestream': True,
            })

    meetings.sort(key=lambda m: m['date'])
    return meetings


def extract_raadsinformatie_schedule(gemeente_data):
    """Probeer vergaderschema te scrapen van raadsinformatie.nl pagina."""
    ri_source = None
    for s in gemeente_data.get('sources', []):
        if s['platform'] == 'gemeenteoplossingen':
            ri_source = s
            break

    if not ri_source:
        return []

    # Als het een notubiz-redirect is, skip (wordt al via API gedaan)
    if 'notubiz.nl' in ri_source['url']:
        return []

    html = _fetch_html(ri_source['url'])
    if not html:
        return []

    meetings = []
    # raadsinformatie.nl pagina's bevatten vergaderlinks met datums
    # Typisch patroon: /vergadering/123456/Titel%20datum
    pattern = re.findall(
        r'vergadering/(\d+)/([^"\'<]+)',
        html
    )
    for meeting_id, title_date in pattern:
        title_date = urllib.request.unquote(title_date).strip()
        # Probeer datum te extracten
        date_match = re.search(r'(\d{2})-(\d{2})-(\d{4})', title_date)
        if date_match:
            d, m, y = date_match.groups()
            date_str = f"{y}-{m}-{d}"
        else:
            date_str = ''

        title = re.sub(r'\s*\d{2}-\d{2}-\d{4}\s*$', '', title_date).strip()

        if not date_str or date_str >= TODAY:
            meetings.append({
                'date': date_str,
                'time': '',
                'title': title,
                'type': '',
                'category': '',
                'location': '',
                'url': f"{ri_source['url'].rstrip('/')}/vergadering/{meeting_id}",
                'chairman': '',
                'source': 'raadsinformatie_scrape',
                'has_livestream': True,
            })

    # Deduplicate op meeting_id (via URL)
    seen = set()
    unique = []
    for m in meetings:
        if m['url'] not in seen:
            seen.add(m['url'])
            unique.append(m)
    unique.sort(key=lambda m: m.get('date', ''))
    return unique


def get_schedule_for_gemeente(gemeente_data):
    """Haal het volledige vergaderschema op voor een gemeente."""
    gemeente = gemeente_data['gemeente']
    result = {
        'gemeente': gemeente,
        'meetings': [],
        'checked_at': datetime.now().isoformat(),
        'source_used': None,
    }

    # NotUBiz API is de meest betrouwbare bron
    meetings = extract_notubiz_schedule(gemeente_data)
    if meetings:
        result['meetings'] = meetings
        result['source_used'] = 'notubiz_api'
        return result

    # Fallback: raadsinformatie.nl scraping
    meetings = extract_raadsinformatie_schedule(gemeente_data)
    if meetings:
        result['meetings'] = meetings
        result['source_used'] = 'raadsinformatie_scrape'
        return result

    # Fallback: iBabs scraping
    meetings = extract_ibabs_schedule(gemeente_data)
    if meetings:
        result['meetings'] = meetings
        result['source_used'] = 'ibabs_scrape'
        return result

    return result


def main():
    # Laad gemeente stream data
    streams_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', 'gemeente_streams.json')
    with open(streams_file, 'r', encoding='utf-8') as f:
        streams_data = json.load(f)

    gemeenten = streams_data['gemeenten']
    total = len(gemeenten)

    print("=" * 70)
    print("GEMEENTE VERGADERSCHEMA EXTRACTOR")
    print(f"Ophalen van schema's voor {total} gemeenten...")
    print(f"Datum: {TODAY}")
    print("=" * 70)

    start_time = time.time()
    results = []
    with_meetings = 0

    # Parallel ophalen (max 8 tegelijk)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_schedule_for_gemeente, g): g for g in gemeenten}

        for i, future in enumerate(as_completed(futures), 1):
            gdata = futures[future]
            gemeente = gdata['gemeente']
            try:
                result = future.result()
                results.append(result)

                n = len(result['meetings'])
                if n > 0:
                    with_meetings += 1
                    # Toon eerstvolgende vergadering
                    upcoming = result['meetings'][0]
                    print(f"  [{i}/{total}] {gemeente}: {n} vergaderingen | Eerst: {upcoming['date']} {upcoming['time']} - {upcoming['title']} ({result['source_used']})")
                else:
                    print(f"  [{i}/{total}] {gemeente}: geen schema gevonden")

                if i % 50 == 0:
                    elapsed = time.time() - start_time
                    pct = (i / total) * 100
                    print(f"\n  VOORTGANG: {pct:.0f}% | Met schema: {with_meetings}/{i}\n")

            except Exception as e:
                print(f"  [{i}/{total}] {gemeente}: ERROR ({e})")
                results.append({
                    'gemeente': gemeente,
                    'meetings': [],
                    'checked_at': datetime.now().isoformat(),
                    'source_used': None,
                    'error': str(e),
                })

    # Sorteer resultaten
    results.sort(key=lambda r: r['gemeente'])

    # Statistieken
    elapsed = time.time() - start_time
    sources_used = {}
    total_meetings = 0
    upcoming_today = 0
    upcoming_week = 0
    week_from_now = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    for r in results:
        src = r.get('source_used')
        if src:
            sources_used[src] = sources_used.get(src, 0) + 1
        for m in r.get('meetings', []):
            total_meetings += 1
            if m.get('date') == TODAY:
                upcoming_today += 1
            if m.get('date', '') <= week_from_now:
                upcoming_week += 1

    # Output
    output = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'reference_date': TODAY,
            'total_gemeenten': len(results),
            'gemeenten_with_schedule': sum(1 for r in results if r['meetings']),
            'total_meetings': total_meetings,
            'meetings_today': upcoming_today,
            'meetings_this_week': upcoming_week,
            'sources_used': sources_used,
            'scan_duration_seconds': round(elapsed, 1),
        },
        'gemeenten': results,
    }

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'gemeente_schedules.json')

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Samenvatting
    print("\n" + "=" * 70)
    print("RESULTATEN")
    print("=" * 70)
    print(f"\nTotaal gemeenten:         {len(results)}")
    print(f"Met vergaderschema:       {output['metadata']['gemeenten_with_schedule']}")
    print(f"Totaal vergaderingen:     {total_meetings}")
    print(f"Vandaag ({TODAY}):     {upcoming_today}")
    print(f"Deze week:                {upcoming_week}")
    print(f"Scan duur:                {elapsed:.0f} seconden")
    print(f"\nBronnen:")
    for src, count in sorted(sources_used.items(), key=lambda x: -x[1]):
        print(f"  {src:30s} {count:4d} gemeenten")
    print(f"\nOpgeslagen: {output_file}")

    # Toon vergaderingen van vandaag
    today_meetings = []
    for r in results:
        for m in r.get('meetings', []):
            if m.get('date') == TODAY:
                today_meetings.append((r['gemeente'], m))

    if today_meetings:
        print(f"\n{'=' * 70}")
        print(f"VERGADERINGEN VANDAAG ({TODAY})")
        print(f"{'=' * 70}")
        for gemeente, m in sorted(today_meetings, key=lambda x: x[1].get('time', '')):
            livestream = " [LIVESTREAM]" if m.get('has_livestream') else ""
            print(f"  {m.get('time', '??:??'):>5s}  {gemeente:30s}  {m['title']}{livestream}")
            if m.get('url'):
                print(f"         {m['url']}")

    return output_file


if __name__ == '__main__':
    main()
