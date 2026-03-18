#!/usr/bin/env python3
"""
Gemeente Livestream Finder
Zoekt automatisch livestream-bronnen voor alle Nederlandse gemeenten.
Checkt: NotuBiz, iBabs, GemeenteOplossingen, YouTube, Open Raadsinformatie
"""

import json
import os
import time
import re
import urllib.request
import urllib.error
import ssl
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# SSL context voor HTTPS requests
SSL_CTX = ssl.create_default_context()

# ============================================================================
# ALLE 342 NEDERLANDSE GEMEENTEN (per 1 januari 2024)
# ============================================================================

GEMEENTEN = [
    # Drenthe
    "Aa en Hunze", "Assen", "Borger-Odoorn", "Coevorden", "Emmen",
    "Hoogeveen", "Meppel", "Midden-Drenthe", "Noordenveld", "Tynaarlo",
    "Westerveld", "De Wolden",
    # Flevoland
    "Almere", "Dronten", "Lelystad", "Noordoostpolder", "Urk", "Zeewolde",
    # Friesland
    "Achtkarspelen", "Ameland", "Dantumadeel", "De Fryske Marren",
    "Harlingen", "Heerenveen", "Leeuwarden", "Noardeast-Fryslân",
    "Ooststellingwerf", "Opsterland", "Schiermonnikoog",
    "Smallingerland", "Súdwest-Fryslân", "Terschelling", "Vlieland",
    "Waadhoeke", "Weststellingwerf",
    # Gelderland
    "Aalten", "Apeldoorn", "Arnhem", "Barneveld", "Berg en Dal",
    "Berkelland", "Beuningen", "Bronckhorst", "Brummen", "Buren",
    "Culemborg", "Doesburg", "Doetinchem", "Druten", "Duiven",
    "Ede", "Elburg", "Epe", "Ermelo", "Harderwijk",
    "Hattem", "Heerde", "Heumen", "Lingewaard", "Lochem",
    "Maasdriel", "Montferland", "Neder-Betuwe", "Nijkerk", "Nijmegen",
    "Nunspeet", "Oldebroek", "Oost Gelre", "Oude IJsselstreek", "Overbetuwe",
    "Putten", "Renkum", "Rheden", "Rijssen-Holten", "Rozendaal",
    "Scherpenzeel", "Tiel", "Voorst", "Wageningen", "West Betuwe",
    "West Maas en Waal", "Westervoort", "Winterswijk", "Wijchen",
    "Zaltbommel", "Zevenaar", "Zutphen",
    # Groningen
    "Eemsdelta", "Groningen", "Het Hogeland", "Midden-Groningen",
    "Oldambt", "Pekela", "Stadskanaal", "Veendam", "Westerkwartier",
    "Westerwolde",
    # Limburg
    "Beek", "Beekdaelen", "Bergen (L)", "Brunssum", "Beesel",
    "Echt-Susteren", "Eijsden-Margraten", "Gennep", "Gulpen-Wittem",
    "Heerlen", "Horst aan de Maas", "Kerkrade", "Landgraaf",
    "Leudal", "Maasgouw", "Maastricht", "Meerssen", "Mook en Middelaar",
    "Nederweert", "Peel en Maas", "Roerdalen", "Roermond",
    "Simpelveld", "Sittard-Geleen", "Stein", "Vaals",
    "Venlo", "Venray", "Voerendaal", "Weert", "Valkenburg aan de Geul",
    # Noord-Brabant
    "Altena", "Asten", "Baarle-Nassau", "Bergen op Zoom", "Bernheze",
    "Best", "Bladel", "Boekel", "Boxtel", "Breda",
    "Cranendonck", "Cuijk", "Deurne", "Dongen", "Drimmelen",
    "Eersel", "Eindhoven", "Etten-Leur", "Geertruidenberg", "Geldrop-Mierlo",
    "Gemert-Bakel", "Gilze en Rijen", "Goirle", "Grave", "Halderberge",
    "Heeze-Leende", "Helmond", "Heusden", "Hilvarenbeek",
    "Laarbeek", "Land van Cuijk", "Loon op Zand", "Maashorst",
    "Meierijstad", "Moerdijk", "Nuenen, Gerwen en Nederwetten",
    "Oirschot", "Oisterwijk", "Oss", "Oosterhout", "Reusel-De Mierden",
    "Roosendaal", "Rucphen", "Sint-Michielsgestel", "Someren",
    "Son en Breugel", "Steenbergen", "Tilburg", "Uden",
    "Valkenswaard", "Veldhoven", "Vught", "Waalre", "Waalwijk",
    "Woensdrecht", "Zundert", "'s-Hertogenbosch",
    # Noord-Holland
    "Aalsmeer", "Alkmaar", "Amstelveen", "Amsterdam", "Bergen (NH)",
    "Beverwijk", "Blaricum", "Bloemendaal", "Castricum", "Den Helder",
    "Diemen", "Dijk en Waard", "Edam-Volendam", "Enkhuizen",
    "Gooise Meren", "Haarlem", "Haarlemmermeer", "Heemskerk",
    "Heemstede", "Heiloo", "Hilversum", "Hoorn", "Huizen",
    "Koggenland", "Landsmeer", "Laren", "Medemblik", "Oostzaan",
    "Opmeer", "Ouder-Amstel", "Purmerend", "Schagen",
    "Stede Broec", "Texel", "Uitgeest", "Uithoorn",
    "Velsen", "Waterland", "Weesp", "Wormerland",
    "Zaanstad", "Zandvoort",
    # Overijssel
    "Almelo", "Borne", "Dalfsen", "Deventer", "Dinkelland",
    "Enschede", "Haaksbergen", "Hardenberg", "Hellendoorn", "Hengelo",
    "Kampen", "Losser", "Oldenzaal", "Olst-Wijhe", "Ommen",
    "Raalte", "Rijssen-Holten", "Staphorst", "Steenwijkerland",
    "Tubbergen", "Twenterand", "Wierden", "Zwolle",
    # Utrecht
    "Amersfoort", "Baarn", "Bunnik", "Bunschoten", "De Bilt",
    "De Ronde Venen", "Eemnes", "Houten", "IJsselstein",
    "Leusden", "Lopik", "Montfoort", "Nieuwegein", "Oudewater",
    "Renswoude", "Rhenen", "Soest", "Stichtse Vecht",
    "Utrecht", "Utrechtse Heuvelrug", "Veenendaal", "Vijfheerenlanden",
    "Wijk bij Duurstede", "Woerden", "Woudenberg", "Zeist",
    # Zeeland
    "Borsele", "Goes", "Hulst", "Kapelle", "Middelburg",
    "Noord-Beveland", "Reimerswaal", "Schouwen-Duiveland", "Sluis",
    "Terneuzen", "Tholen", "Veere", "Vlissingen",
    # Zuid-Holland
    "Alblasserdam", "Alphen aan den Rijn", "Barendrecht", "Bodegraven-Reeuwijk",
    "Brielle", "Capelle aan den IJssel", "Delft", "Dordrecht",
    "Goeree-Overflakkee", "Gorinchem", "Gouda", "Hardinxveld-Giessendam",
    "Hellevoetsluis", "Hendrik-Ido-Ambacht", "Hillegom",
    "Hoeksche Waard", "Kaag en Braassem", "Katwijk", "Krimpen aan den IJssel",
    "Krimpenerwaard", "Lansingerland", "Leiden", "Leiderdorp",
    "Leidschendam-Voorburg", "Lisse", "Maassluis", "Midden-Delfland",
    "Molenlanden", "Nieuwkoop", "Noordwijk", "Oegstgeest",
    "Papendrecht", "Pijnacker-Nootdorp", "Ridderkerk", "Rijswijk",
    "Rotterdam", "Schiedam", "Sliedrecht", "Súdwest-Fryslân",
    "Teylingen", "Voorschoten", "Waddinxveen", "Wassenaar",
    "Westland", "Westvoorne", "Zoetermeer", "Zoeterwoude",
    "Zuidplas", "'s-Gravenhage",
    # Caribisch Nederland
    "Bonaire", "Sint Eustatius", "Saba",
]

# Verwijder duplicaten
GEMEENTEN = sorted(list(set(GEMEENTEN)))

# Bekende afwijkende subdomeinen (naam in onze lijst → notubiz subdomain)
SPECIAL_SLUGS = {
    "'s-Gravenhage": ["denhaag"],
    "'s-Hertogenbosch": ["shertogenbosch", "s-hertogenbosch"],
    "Súdwest-Fryslân": ["sudwestfryslan"],
    "Noardeast-Fryslân": ["noardeastfryslan"],
    "De Fryske Marren": ["defryskemarren"],
    "Nuenen, Gerwen en Nederwetten": ["nuenen"],
    "Bergen (L)": ["bergenl", "bergenlb", "bergen"],
    "Bergen (NH)": ["bergennh", "bergen"],
    "Dantumadeel": ["dantumadiel", "dantumadeel"],
}

# Bekende Facebook-pagina's voor gemeenten met afwijkende namen
FACEBOOK_KNOWN = {
    "Bonaire": "https://www.facebook.com/konsehoinsular/",
    "Sint Eustatius": "https://www.facebook.com/sinteustatiusgov/",
    "Saba": "https://www.facebook.com/PublicEntitySaba/",
}


def _slugs(name):
    """Genereer meerdere URL-vriendelijke slug-varianten van een gemeentenaam.
    Retourneert lijst van unieke slugs, meest waarschijnlijke eerst."""
    s = name.lower()
    s = s.replace("\u2019s-", "")  # typographic apostrophe
    s = s.replace("'s-", "")       # regular apostrophe
    s = s.replace(" (l)", "")
    s = s.replace(" (nh)", "")

    # Slug met streepjes ("hoeksche-waard")
    hyphenated = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    # Slug zonder streepjes ("hoekschewaard")
    compact = re.sub(r'[^a-z0-9]', '', s)

    # Extra varianten voor veelvoorkomende patronen
    slugs = []

    # Speciale mapping eerst als die bestaat (originele naam met hoofdletters)
    if name in SPECIAL_SLUGS:
        slugs.extend(SPECIAL_SLUGS[name])

    # Compact eerst — werkt vaker bij NotuBiz
    if compact != hyphenated:
        slugs.append(compact)
    slugs.append(hyphenated)

    # "gemeente" prefix variant
    slugs.append(f"gemeente{compact}")

    return list(dict.fromkeys(slugs))  # uniek, volgorde behouden


def _slug(name):
    """Primaire slug (compact, zonder streepjes)"""
    return _slugs(name)[0]


def _fetch_body(url, timeout=8, max_bytes=4096):
    """Haal URL op en retourneer (status, final_url, body_snippet).
    Leest max max_bytes van de body voor content-validatie."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; GemeenteStreamFinder/1.0)'
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        body = resp.read(max_bytes).decode('utf-8', errors='ignore').lower()
        return resp.status, resp.url, body
    except urllib.error.HTTPError as e:
        return e.code, None, ''
    except Exception:
        return 0, None, ''


# ============================================================================
# INVALID BODY MARKERS — pagina's die HTTP 200 geven maar NIET echt bestaan
# ============================================================================
INVALID_MARKERS = [
    'ongeldige site',        # iBabs standaard foutpagina
    'deze pagina bestaat niet',
    'page not found',
    'niet gevonden',
]


def _is_valid_body(body):
    """Check of de body geen bekende foutmelding bevat."""
    for marker in INVALID_MARKERS:
        if marker in body:
            return False
    return True


def _is_generic_redirect(final_url, base_domain):
    """Check of de redirect naar de generieke homepage gaat (bv. https://www.notubiz.nl/)."""
    if not final_url:
        return False
    # Strip naar domein
    clean = final_url.rstrip('/').lower()
    generics = [
        f'https://www.{base_domain}',
        f'https://{base_domain}',
        f'http://www.{base_domain}',
        f'http://{base_domain}',
    ]
    return clean in generics


# ============================================================================
# NOTUBIZ API CACHE — laad alle organisaties 1x en match op naam
# ============================================================================
_NOTUBIZ_CACHE = None


def _load_notubiz_orgs():
    """Laad alle NotUBiz organisaties via hun publieke API."""
    global _NOTUBIZ_CACHE
    if _NOTUBIZ_CACHE is not None:
        return _NOTUBIZ_CACHE

    try:
        req = urllib.request.Request(
            'https://api.notubiz.nl/organisations',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        )
        resp = urllib.request.urlopen(req, timeout=30, context=SSL_CTX)
        data = json.loads(resp.read().decode('utf-8'))
        orgs = data.get('organisations', {}).get('organisation', [])

        # Bouw lookup: genormaliseerde naam → org info
        lookup = {}
        for org in orgs:
            raw_name = org.get('name', '').strip()
            # Normaliseer: verwijder "Gemeente " prefix, lowercase, strip
            clean = raw_name.lower()
            for prefix in ['gemeente ', 'deelgemeente ']:
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
            clean = clean.strip()
            # Maak slug voor matching
            slug = re.sub(r'[^a-z0-9]', '', clean)
            org_id = org.get('@attributes', {}).get('id', '')
            # Genereer de subdomain: naam zonder spaties/speciale tekens
            subdomain = re.sub(r'[^a-z0-9]', '', clean)
            lookup[slug] = {
                'name': raw_name,
                'id': org_id,
                'subdomain': subdomain,
                'url': f"https://{subdomain}.notubiz.nl",
            }
            # Ook met streepjes als key
            slug_hyphen = re.sub(r'[^a-z0-9]+', '-', clean).strip('-')
            if slug_hyphen != slug:
                lookup[slug_hyphen] = lookup[slug]

        _NOTUBIZ_CACHE = lookup
    except Exception:
        _NOTUBIZ_CACHE = {}

    return _NOTUBIZ_CACHE


def _get_notubiz_real_url(org_id):
    """Haal de echte stream-URL op via de events API van een organisatie.
    Retourneert het basis-domein (bv. https://alkmaar.raadsinformatie.nl)."""
    try:
        req = urllib.request.Request(
            f'https://api.notubiz.nl/organisations/{org_id}/events',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        )
        resp = urllib.request.urlopen(req, timeout=10, context=SSL_CTX)
        data = json.loads(resp.read(65536).decode('utf-8'))
        events = data.get('events', {}).get('event', [])
        if events and isinstance(events, list):
            event_url = events[0].get('url', '')
            match = re.search(r'(https?://[^/]+)', event_url)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


def check_notubiz(gemeente):
    """Check NotuBiz platform via API-lookup.
    Haalt de echte stream-URL op via de events API."""
    orgs = _load_notubiz_orgs()

    # Probeer alle slug-varianten
    for v in _slugs(gemeente):
        if v in orgs:
            info = orgs[v]
            # Haal de echte URL op via events API
            real_url = _get_notubiz_real_url(info['id'])
            url = real_url if real_url else info['url']
            return {
                'platform': 'notubiz',
                'url': url,
                'notubiz_id': info['id'],
                'notubiz_name': info['name'],
                'status': 'found',
            }

    return None


def check_ibabs(gemeente):
    """Check iBabs/Bestuurlijke Informatie: {gemeente}.bestuurlijkeinformatie.nl
    Leest de body om 'Ongeldige site!' false positives eruit te filteren."""
    for v in _slugs(gemeente):
        url = f"https://{v}.bestuurlijkeinformatie.nl"
        status, final_url, body = _fetch_body(url)
        if status != 200:
            continue
        if _is_generic_redirect(final_url, 'bestuurlijkeinformatie.nl'):
            continue
        if not _is_valid_body(body):
            continue
        return {'platform': 'ibabs', 'url': final_url or url, 'status': 'found'}

    return None


def check_gemeenteoplossingen(gemeente):
    """Check raadsinformatie-subdomeinen.
    Filtert redirects naar generieke notubiz.nl homepage."""
    slugs = _slugs(gemeente)
    for v in slugs:
        urls = [
            f"https://raad.{v}.nl",
            f"https://raadsinformatie.{v}.nl",
            f"https://{v}.raadsinformatie.nl",
        ]
        for url in urls:
            status, final_url, body = _fetch_body(url)
            if status != 200:
                continue
            # raadsinformatie.nl redirect vaak naar notubiz.nl voor gemeenten die NotUBiz gebruiken
            if _is_generic_redirect(final_url, 'notubiz.nl'):
                continue
            if _is_generic_redirect(final_url, 'raadsinformatie.nl'):
                continue
            if not _is_valid_body(body):
                continue
            return {'platform': 'gemeenteoplossingen', 'url': final_url or url, 'status': 'found'}

    return None


def check_youtube(gemeente):
    """Check YouTube voor gemeenteraad kanaal."""
    slugs = _slugs(gemeente)
    tried = set()
    for v in slugs:
        candidates = [
            f"https://www.youtube.com/@gemeenteraad{v}",
            f"https://www.youtube.com/@gemeente{v}",
        ]
        for url in candidates:
            if url in tried:
                continue
            tried.add(url)
            status, final_url, body = _fetch_body(url)
            if status == 200 and _is_valid_body(body):
                return {'platform': 'youtube', 'url': final_url or url, 'status': 'found'}

    return None


def check_facebook(gemeente):
    """Check Facebook voor gemeente-pagina.
    Facebook geeft login-wall (200) voor bestaande pagina's en 404 voor niet-bestaande."""
    # Eerst bekende pagina's checken
    if gemeente in FACEBOOK_KNOWN:
        url = FACEBOOK_KNOWN[gemeente]
        status, final_url, body = _fetch_body(url)
        if status == 200:
            return {'platform': 'facebook', 'url': url, 'status': 'found'}

    # Standaard patronen proberen
    slugs = _slugs(gemeente)
    tried = set()
    for v in slugs:
        candidates = [
            f"https://www.facebook.com/gemeente{v}/",
            f"https://www.facebook.com/gemeenteraad{v}/",
            f"https://www.facebook.com/gemeente.{v}/",
        ]
        for url in candidates:
            if url in tried:
                continue
            tried.add(url)
            status, final_url, body = _fetch_body(url)
            if status != 200:
                continue
            # Facebook geeft 200 + login-wall voor bestaande pagina's
            # maar redirect naar /login/ met de pagina in 'next' param
            # Check dat het geen generieke facebook error page is
            if 'the link you followed may be broken' in body:
                continue
            if 'page isn' in body and 'available' in body:
                continue
            return {'platform': 'facebook', 'url': url, 'status': 'found'}

    return None


def find_streams_for_gemeente(gemeente):
    """Zoek alle beschikbare stream-bronnen voor een gemeente"""
    result = {
        'gemeente': gemeente,
        'slug': _slug(gemeente),
        'sources': [],
        'checked_at': datetime.now().isoformat(),
    }

    # Check alle platforms
    checks = [
        ('NotuBiz', check_notubiz),
        ('iBabs', check_ibabs),
        ('GemeenteOplossingen', check_gemeenteoplossingen),
        ('YouTube', check_youtube),
        ('Facebook', check_facebook),
    ]

    for name, check_fn in checks:
        try:
            found = check_fn(gemeente)
            if found:
                result['sources'].append(found)
        except Exception as e:
            pass

    result['num_sources'] = len(result['sources'])
    result['primary_source'] = result['sources'][0] if result['sources'] else None

    return result


def main():
    print("=" * 70)
    print("GEMEENTE LIVESTREAM FINDER")
    print(f"Controleren van {len(GEMEENTEN)} gemeenten...")
    print("Platforms: NotuBiz, iBabs, GemeenteOplossingen, YouTube, Facebook")
    print("=" * 70)

    start_time = time.time()
    results = []
    found_count = 0
    total = len(GEMEENTEN)

    # Parallel checken (max 10 tegelijk om niet te agressief te zijn)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(find_streams_for_gemeente, g): g for g in GEMEENTEN}

        for i, future in enumerate(as_completed(futures), 1):
            gemeente = futures[future]
            try:
                result = future.result()
                results.append(result)

                if result['num_sources'] > 0:
                    found_count += 1
                    sources = ', '.join([s['platform'] for s in result['sources']])
                    print(f"  [{i}/{total}] {gemeente}: {sources}")
                else:
                    print(f"  [{i}/{total}] {gemeente}: -")

                # Progress update elke 50 gemeenten
                if i % 50 == 0:
                    elapsed = time.time() - start_time
                    pct = (i / total) * 100
                    eta = (elapsed / i) * (total - i) / 60
                    print(f"\n  VOORTGANG: {pct:.0f}% | Gevonden: {found_count}/{i} | ETA: {eta:.1f} min\n")

            except Exception as e:
                print(f"  [{i}/{total}] {gemeente}: ERROR ({e})")
                results.append({
                    'gemeente': gemeente,
                    'slug': _slug(gemeente),
                    'sources': [],
                    'num_sources': 0,
                    'primary_source': None,
                    'error': str(e)
                })

    # Sorteer op gemeentenaam
    results.sort(key=lambda r: r['gemeente'])

    # Statistieken
    elapsed = time.time() - start_time
    with_source = [r for r in results if r['num_sources'] > 0]
    without_source = [r for r in results if r['num_sources'] == 0]

    platform_counts = {}
    for r in results:
        for s in r.get('sources', []):
            p = s['platform']
            platform_counts[p] = platform_counts.get(p, 0) + 1

    # Output
    output = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'total_gemeenten': len(results),
            'gemeenten_with_source': len(with_source),
            'gemeenten_without_source': len(without_source),
            'coverage_pct': round(len(with_source) / len(results) * 100, 1),
            'platform_counts': platform_counts,
            'scan_duration_seconds': round(elapsed, 1),
        },
        'gemeenten': results
    }

    # Sla op
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'gemeente_streams.json')

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Samenvatting
    print("\n" + "=" * 70)
    print("RESULTATEN")
    print("=" * 70)
    print(f"\nTotaal gemeenten:     {len(results)}")
    print(f"Met stream-bron:      {len(with_source)} ({output['metadata']['coverage_pct']}%)")
    print(f"Zonder bron:          {len(without_source)}")
    print(f"Scan duur:            {elapsed:.0f} seconden")
    print(f"\nPer platform:")
    for platform, count in sorted(platform_counts.items(), key=lambda x: -x[1]):
        print(f"  {platform:25s} {count:4d} gemeenten")
    print(f"\nOpgeslagen: {output_file}")

    # Toon gemeenten zonder bron
    if without_source:
        print(f"\nGemeenten ZONDER gevonden bron ({len(without_source)}):")
        for r in without_source[:20]:
            print(f"  - {r['gemeente']}")
        if len(without_source) > 20:
            print(f"  ... en {len(without_source) - 20} meer")

    return output_file


if __name__ == '__main__':
    main()
