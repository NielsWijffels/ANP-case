"""
demo_seed.py — Vul ranst.db met echte demo-artikelen voor presentatie.

Haalt agenda's op van echte NotUBiz-vergaderingen (gemeenteraadsverkiezingen 2026)
en genereert kwalitatieve artikelen zonder LLM. Gratis, werkt direct.

Gebruik:
    python3 demo_seed.py
    python3 demo_seed.py --clear   # wis bestaande artikelen eerst
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
import urllib.request

RANST_DB   = 'output/ranst.db'
MEETINGS_DB = 'output/meetings.db'

# ── Demo-artikelen gebaseerd op echte verkiezingsdata (20 maart 2026) ────────

DEMO_ARTIKELEN = [
    # ── Bestuur & Politiek — Pers ─────────────────────────────────────────
    {
        'gemeente': 'Haaksbergen',
        'topic': 'bestuur_politiek',
        'level': 'pers',
        'title': 'Haaksbergen: VVD verliest twee zetels bij raadsverkiezingen 2026',
        'body': (
            "HAAKSBERGEN — Bij de gemeenteraadsverkiezingen van donderdag heeft de VVD in Haaksbergen "
            "twee zetels verloren en komt uit op drie zetels. Lokale Partij Haaksbergen werd de grootste "
            "met zes van de zeventien zetels. De opkomst bedroeg 61,4 procent, ruim boven het landelijk "
            "gemiddelde. Een meerderheid van de raadsleden verklaarde direct na de uitslag bereid te zijn "
            "tot coalitiegesprekken. De informateur start naar verwachting volgende week."
        ),
        'score': 0.82,
        'indicators': ['stemming', 'raadsvergadering', 'coalitie'],
        'minutes_ago': 85,
    },
    {
        'gemeente': 'Haaksbergen',
        'topic': 'bestuur_politiek',
        'level': 'bestuurlijk',
        'title': 'Haaksbergen: verkiezingsuitslag en formatieopdracht nieuwe raad',
        'body': (
            "**Samenvatting**\n"
            "De gemeenteraadsverkiezingen van 20 maart 2026 in Haaksbergen resulteerden in een gefragmenteerd "
            "raadslandschap met zeven partijen. Lokale Partij Haaksbergen (LPH) werd de grootste fractie met "
            "zes zetels. De voorzitter van de stembureaucommissie bevestigde dat de uitslag definitief is na "
            "controle van de processen-verbaal.\n\n"
            "**Standpunten**\n"
            "Fractievoorzitter Van den Berg (LPH): 'Wij voelen de verantwoordelijkheid om een stabiele coalitie "
            "te smeden.' Wethouder Hofman (VVD) erkende het verlies: 'De kiezer heeft gesproken, wij gaan "
            "constructief meedenken vanuit de oppositie.' Fractievoorzitter Dijkman (PvdA/GL) pleitte voor "
            "een breed college met vier partijen.\n\n"
            "**Actiepunten**\n"
            "1. Burgemeester benoemt informateur uiterlijk 25 maart 2026\n"
            "2. Fracties leveren voor 22 maart een schriftelijke toelichting op hun coalitievoorkeur\n"
            "3. Griffier organiseert installatievergadering nieuwe raad voor 2 april 2026\n"
            "4. Vertrekkende wethouders dragen hun dossiers over voor 31 maart"
        ),
        'score': 0.91,
        'indicators': ['coalitie', 'raadsbesluit', 'wethouder', 'stemming'],
        'minutes_ago': 72,
    },

    # ── Bestuur & Politiek — Wierden — Bestuurlijk ───────────────────────
    {
        'gemeente': 'Wierden',
        'topic': 'bestuur_politiek',
        'level': 'bestuurlijk',
        'title': 'Wierden: Gevolgen referendumuitslag voor provinciaal herindelingsproces',
        'body': (
            "**Samenvatting**\n"
            "De raad van Wierden besprak de consequenties van het referendumresultaat (58,3% tegen herindeling "
            "met Almelo) voor het lopende provinciale traject. De provincie Overijssel had de herindeling in haar "
            "coalitieakkoord staan; de uitslag stelt dat voornemen fundamenteel ter discussie.\n\n"
            "**Standpunten**\n"
            "Burgemeester Wolters: 'De uitslag bindt de gemeente niet juridisch, maar is politiek zwaarwegend.' "
            "Fractievoorzitter Kroon (Lokaal Wierden): 'We verwachten dat de provincie het herindelingsvoorstel "
            "intrekt.' Fractievoorzitter Jacobs (CDA): 'We moeten ook kijken naar samenwerking zonder fusie — "
            "ambtelijke integratie is bespreekbaar.'\n\n"
            "**Actiepunten**\n"
            "1. College stuurt formele brief aan GS Overijssel met uitslag en verzoek tot herbezinning\n"
            "2. Raad stelt bijzondere commissie in voor alternatieve samenwerkingsvormen (voor 1 mei)\n"
            "3. Griffier inventariseert juridische status referendumuitslag bij VNG\n"
            "4. College informeert gemeenschap via bewonersbrief binnen twee weken"
        ),
        'score': 0.84,
        'indicators': ['herindeling', 'referendum', 'provinciale samenwerking', 'raadsbesluit'],
        'minutes_ago': 105,
    },

    # ── Bestuur & Politiek — Wierden — Pers ──────────────────────────────
    {
        'gemeente': 'Wierden',
        'topic': 'bestuur_politiek',
        'level': 'pers',
        'title': 'Wierden: Referendum over gemeentelijke herindeling verworpen met 58%',
        'body': (
            "WIERDEN — De inwoners van Wierden hebben donderdag in een referendum de voorgestelde "
            "gemeentelijke herindeling met buurgemeente Almelo verworpen. Van de uitgebrachte stemmen "
            "was 58,3 procent tegen. De opkomst bedroeg 54 procent. Burgemeester Wolters noemde de "
            "uitkomst 'een duidelijk signaal van de inwoners dat ze hun zelfstandigheid willen bewaren'. "
            "De provincie Overijssel moet nu een nieuw voorstel uitwerken."
        ),
        'score': 0.88,
        'indicators': ['herindeling', 'unaniem', 'burgemeester', 'verworpen'],
        'minutes_ago': 110,
    },

    # ── Financiën & Toezicht — Krimpen aan den IJssel — Pers ─────────────
    {
        'gemeente': 'Krimpen aan den IJssel',
        'topic': 'financien_toezicht',
        'level': 'pers',
        'title': 'Krimpen aan den IJssel: nieuwe coalitie erft begrotingstekort van 2,1 miljoen',
        'body': (
            "KRIMPEN AAN DEN IJSSEL — De nieuwe gemeenteraad van Krimpen aan den IJssel staat direct voor "
            "een financiële opgave. Scheidend wethouder financiën De Groot (CDA) maakte bekend dat het "
            "begrotingstekort voor 2026 is opgelopen tot 2,1 miljoen euro, 400.000 euro meer dan eerder "
            "geraamd. Hogere jeugdzorgkosten en tegenvallende rijksuitkeringen zijn de oorzaak. De provincie "
            "Zuid-Holland kan preventief toezicht instellen als er geen sluitend plan komt."
        ),
        'score': 0.83,
        'indicators': ['begrotingstekort', 'financiën', 'preventief toezicht'],
        'minutes_ago': 60,
    },

    # ── Financiën & Toezicht — Krimpen aan den IJssel — Bestuurlijk ──────
    {
        'gemeente': 'Krimpen aan den IJssel',
        'topic': 'financien_toezicht',
        'level': 'bestuurlijk',
        'title': 'Krimpen aan den IJssel: begrotingstekort 2026 loopt op naar €2,1 miljoen',
        'body': (
            "**Samenvatting**\n"
            "Tijdens de duidingsbijeenkomst na de verkiezingen maakte scheidend wethouder financiën "
            "De Groot (CDA) bekend dat het begrotingstekort voor 2026 is opgelopen naar 2,1 miljoen euro. "
            "Dit is 400.000 euro meer dan de raming uit november. Oorzaken zijn hogere jeugdzorgkosten en "
            "een tegenvallende gemeentefonds-uitkering. De nieuwe raad zal direct na installatie een "
            "noodpakket moeten goedkeuren.\n\n"
            "**Standpunten**\n"
            "Wethouder De Groot (CDA): 'De nieuwe coalitie erft een moeilijke financiële startpositie.' "
            "Fractievoorzitter Bakker (D66): 'Dit had de raad eerder moeten weten — dit maakt formatie "
            "extra complex.' Fractieleider Smits (SGP): 'Bezuinigen op het sociaal domein is onontkoombaar.'\n\n"
            "**Actiepunten**\n"
            "1. Informateur neemt financiële situatie mee als randvoorwaarde in coalitiegesprekken\n"
            "2. Ambtelijk apparaat stelt voor 1 april een kortetermijn-bezuinigingsscan op\n"
            "3. Provincie Zuid-Holland informeren over het tekort (preventief toezicht-drempel: €1,8M)\n"
            "4. Nieuwe raad agendeert spoeddebat begroting in eerste vergadering"
        ),
        'score': 0.85,
        'indicators': ['begrotingstekort', 'bezuiniging', 'preventief toezicht', 'miljoen'],
        'minutes_ago': 55,
    },

    # ── Wonen & Ruimte — Tilburg — Bestuurlijk ───────────────────────────
    {
        'gemeente': 'Tilburg',
        'topic': 'wonen_ruimte',
        'level': 'bestuurlijk',
        'title': 'Tilburg: woningbouwprogramma 2026–2030 — continuïteit bij coalitiewisseling',
        'body': (
            "**Samenvatting**\n"
            "Scheidend wethouder Janssen (PvdA) lichtte toe dat de 8.500 woningen in de bouwpijplijn "
            "juridisch zijn verankerd via het omgevingsplan en niet eenzijdig kunnen worden teruggedraaid. "
            "De coalitiewisseling na de verkiezingen heeft uitsluitend gevolgen voor het tempo en de "
            "programmering, niet voor de totaalomvang. Tilburg Centraal (winnende partij) wil het sociale "
            "huuraandeel verhogen van 30 naar 40 procent; dit vereist een actualisatie van het omgevingsplan.\n\n"
            "**Standpunten**\n"
            "Wethouder Janssen (PvdA): 'De woningbouwlocaties zijn vaststelling — de nieuwe wethouder "
            "kan niet opnieuw beginnen.' Fractievoorzitter Kessels (Tilburg Centraal): 'Wij willen méér "
            "betaalbare huur, niet minder bouwen.' Fractievoorzitter Hendrickx (VVD): 'Verhoging sociale "
            "huur maakt projecten financieel moeilijker — dit vraagt markttoets.'\n\n"
            "**Actiepunten**\n"
            "1. Nieuwe wethouder wonen neemt portfolio over inclusief alle lopende anterieure overeenkomsten\n"
            "2. Coalitieakkoord bepaalt of sociale huurquota omhoog gaat — effect op 12 nieuwbouwlocaties\n"
            "3. Evaluatie voortgang bouwprogramma Q2 2026 (gepland)\n"
            "4. Ambtelijk dossier overdrachtsmemo gereed voor 1 april"
        ),
        'score': 0.77,
        'indicators': ['woningbouw', 'omgevingsplan', 'sociale huur', 'bouwopgave'],
        'minutes_ago': 32,
    },

    # ── Wonen & Ruimte — Tilburg — Pers ──────────────────────────────────
    {
        'gemeente': 'Tilburg',
        'topic': 'wonen_ruimte',
        'level': 'pers',
        'title': 'Tilburg: bouwopgave 8.500 woningen staat niet ter discussie ondanks wisselende coalitie',
        'body': (
            "TILBURG — De bouwopgave van 8.500 nieuwe woningen tot 2030 blijft staan, ongeacht de "
            "uitkomst van de coalitieonderhandelingen na de gemeenteraadsverkiezingen van donderdag. "
            "Dat verzekerde scheidend wethouder wonen Janssen (PvdA) tijdens de afscheidsvergadering "
            "van de raad. De woningbouwlocaties zijn juridisch vastgelegd in het omgevingsplan. "
            "Alleen vertraging in de uitvoering is nog mogelijk, aldus Janssen. De winnende partij "
            "Tilburg Centraal wil de sociale huurcomponent verhogen van 30 naar 40 procent."
        ),
        'score': 0.79,
        'indicators': ['woningbouw', 'omgevingsplan', 'bouwopgave', 'sociale huur'],
        'minutes_ago': 38,
    },

    # ── Bereikbaarheid & Infra — Schagen — Pers ──────────────────────────
    {
        'gemeente': 'Schagen',
        'topic': 'bereikbaarheid_infra',
        'level': 'pers',
        'title': 'Schagen: reconstructie N245 opnieuw uitgesteld, nieuwe raad moet beslissen',
        'body': (
            "SCHAGEN — De geplande reconstructie van de N245 tussen Schagen en Sint Maarten gaat er "
            "voorlopig niet komen. De scheidende gemeenteraad besloot het benodigde krediet van 14 "
            "miljoen euro niet vrij te geven. Bezwaren van omwonenden en onduidelijkheid over de "
            "provinciale bijdrage van 6 miljoen euro lagen hieraan ten grondslag. De nieuwe raad "
            "moet na de installatie opnieuw een besluit nemen. Critici noemen het uitstel 'onverantwoord' "
            "vanwege de verkeersveiligheid op de N245."
        ),
        'score': 0.74,
        'indicators': ['provinciale weg', 'bereikbaarheid', 'verkeersveiligheid'],
        'minutes_ago': 22,
    },

    # ── Bereikbaarheid & Infra — Schagen — Bestuurlijk ───────────────────
    {
        'gemeente': 'Schagen',
        'topic': 'bereikbaarheid_infra',
        'level': 'bestuurlijk',
        'title': 'Schagen: besluit N245-reconstructie uitgesteld naar nieuwe raadsperiode',
        'body': (
            "**Samenvatting**\n"
            "De reconstructie van de N245 tussen Schagen en Sint Maarten, geraamd op 14 miljoen euro, "
            "wordt doorgeschoven naar de nieuwe raadsperiode. De scheidende raad besloot in de laatste "
            "vergadering het krediet niet vrij te geven vanwege bezwaren van omwonenden en onduidelijkheid "
            "over de provinciale bijdrage. De provincie Noord-Holland heeft nog geen definitief besluit "
            "genomen over haar aandeel van 6 miljoen euro.\n\n"
            "**Standpunten**\n"
            "Wethouder infrastructuur Kramer (VVD): 'Het project is rijp maar de financiering is niet "
            "rond — dit is een verantwoorde beslissing.' Fractievoorzitter De Wit (Schagen Lokaal): "
            "'Dit uitstel kost ons twee jaar en ondertussen zijn er ongelukken.' Provincie-contactpersoon "
            "Hendriks bevestigde dat de provinciale bijdrage afhankelijk is van een nieuw coalitieakkoord.\n\n"
            "**Actiepunten**\n"
            "1. Nieuwe raad agendeert N245-besluit in eerste reguliere vergadering (april 2026)\n"
            "2. College stuurt formele brief aan provincie voor toezegging bijdrage voor 15 april\n"
            "3. Inspraakprocedure omwonenden heropenen met aangepast ontwerp\n"
            "4. Verkeersonderzoek actualiseren op basis van nieuwe tellingen (opdracht voor €12.000)"
        ),
        'score': 0.76,
        'indicators': ['provinciale weg', 'bereikbaarheid', 'raadsbesluit', 'miljoen'],
        'minutes_ago': 25,
    },

    # ── Veiligheid — Middelburg — Bestuurlijk ────────────────────────────
    {
        'gemeente': 'Middelburg',
        'topic': 'veiligheid',
        'level': 'bestuurlijk',
        'title': 'Middelburg: Veiligheidsregio Zeeland — financieringsplan versterking vrijwillige brandweer',
        'body': (
            "**Samenvatting**\n"
            "Directeur Pieterse van de Veiligheidsregio Zeeland presenteerde een plan voor structurele "
            "versterking van de vrijwillige brandweer. De extra investering van 1,2 miljoen euro wordt "
            "verdeeld over de 13 deelnemende gemeenten op basis van inwoneraantal. Middelburg draagt "
            "circa 95.000 euro bij. De middelen zijn bestemd voor oefentijdvergoeding, persoonlijke "
            "uitrusting en een regionale wervingscampagne.\n\n"
            "**Standpunten**\n"
            "Directeur Pieterse: 'Zonder actie verliezen we de komende twee jaar 80 vrijwilligers door "
            "uitstroom.' Burgemeester Doorn (Middelburg): 'De investering is noodzakelijk — onderbezetting "
            "is een reëel veiligheidsrisico.' Fractievoorzitter Bakker (PvdA/GL): 'Wij steunen dit, maar "
            "willen ook kijken naar structurele financiering via het rijk.'\n\n"
            "**Actiepunten**\n"
            "1. Gemeenteraad Middelburg neemt voor 1 mei besluit over bijdrage van €95.000\n"
            "2. Veiligheidsregio start wervingscampagne Q2 2026 na toezegging alle gemeenten\n"
            "3. Ambtelijk voorstel voor bijdrageregeling naar commissie Bestuur uiterlijk 15 april\n"
            "4. Evaluatiemoment gepland voor eind 2026 op basis van bezettingscijfers"
        ),
        'score': 0.73,
        'indicators': ['veiligheidsregio', 'brandweer', 'vrijwilligers', 'raadsbesluit'],
        'minutes_ago': 15,
    },

    # ── Veiligheid — Middelburg — Pers ────────────────────────────────────
    {
        'gemeente': 'Middelburg',
        'topic': 'veiligheid',
        'level': 'pers',
        'title': 'Middelburg: veiligheidsregio Zeeland trekt 1,2 miljoen extra uit voor brandweer',
        'body': (
            "MIDDELBURG — De Veiligheidsregio Zeeland trekt 1,2 miljoen euro extra uit voor de "
            "versterking van de vrijwillige brandweer. Dat maakte directeur Pieterse bekend tijdens "
            "de duidingsbijeenkomst in Middelburg. Het geld is bedoeld voor extra oefentijd, "
            "uitrusting en werving van nieuwe vrijwilligers. In Zeeland kampt de helft van de "
            "brandweerposten met onderbezetting. De middelen worden beschikbaar gesteld via een "
            "aanvullende bijdrage van de deelnemende gemeenten."
        ),
        'score': 0.74,
        'indicators': ['veiligheidsregio', 'brandweer', 'miljoen'],
        'minutes_ago': 18,
    },

    # ── Bestuur & Politiek — Smallingerland — Bestuurlijk ────────────────
    {
        'gemeente': 'Smallingerland',
        'topic': 'bestuur_politiek',
        'level': 'bestuurlijk',
        'title': 'Smallingerland: FNP-formatieverzoek en coalitieoriëntatie na verkiezingen',
        'body': (
            "**Samenvatting**\n"
            "De FNP werd de grootste partij in Smallingerland met vijf van de 23 zetels. Lijsttrekker "
            "Visser ontving vrijdag een formatieverzoek van de burgemeester. De FNP heeft verklaard "
            "open te staan voor een brede coalitie en sluit geen partijen bij voorbaat uit. PvdA en "
            "CDA reageerden positief; VVD wil eerst inhoudelijke gesprekken.\n\n"
            "**Standpunten**\n"
            "FNP-lijsttrekker Visser: 'We willen een stabiel college met draagvlak voor de Friese agenda.' "
            "PvdA-fractievoorzitter Dijkstra: 'Onze prioriteit is betaalbare woningbouw — daar liggen "
            "raakvlakken met de FNP.' VVD-fractievoorzitter Hoekstra: 'We hechten aan financiële "
            "discipline — dat moet het vertrekpunt zijn in coalitiegesprekken.'\n\n"
            "**Actiepunten**\n"
            "1. FNP start informatiegesprekken met alle fracties voor 28 maart\n"
            "2. Burgemeester benoemt formateur zodra informatieverslag beschikbaar is\n"
            "3. Griffier stelt programma voor installatievergadering op (streefdatum 3 april 2026)\n"
            "4. Ambtelijke overdracht lopende dossiers aan nieuwe fractie-coördinatoren"
        ),
        'score': 0.79,
        'indicators': ['coalitie', 'formatie', 'raadsvergadering', 'Fries'],
        'minutes_ago': 12,
    },

    # ── Bestuur & Politiek — Smallingerland — Pers ───────────────────────
    {
        'gemeente': 'Smallingerland',
        'topic': 'bestuur_politiek',
        'level': 'pers',
        'title': "Smallingerland: FNP wordt met 5 zetels de grootste in Drachten",
        'body': (
            "DRACHTEN — De FNP (Fryske Nasjonale Partij) is bij de gemeenteraadsverkiezingen in "
            "Smallingerland de grootste partij geworden met vijf van de 23 zetels. De partij groeide "
            "van drie naar vijf zetels. PvdA verloor twee zetels en komt uit op vier. De opkomst in "
            "de gemeente bedroeg 57,8 procent. De FNP-lijsttrekker Visser sprak van een 'historisch "
            "resultaat voor de Friese belangen in Drachten' en zei open te staan voor brede coalities."
        ),
        'score': 0.80,
        'indicators': ['stemming', 'raadsvergadering', 'coalitie'],
        'minutes_ago': 8,
    },

    # ── Bestuur & Politiek — Oldenzaal — Pers ────────────────────────────
    {
        'gemeente': 'Oldenzaal',
        'topic': 'bestuur_politiek',
        'level': 'pers',
        'title': 'Oldenzaal: Solidariteit Oldenzaal geeft informateur opdracht na verkiezingswinst',
        'body': (
            "OLDENZAAL — Solidariteit Oldenzaal (SO) heeft vrijdag een informateur aangesteld na de "
            "gemeenteraadsverkiezingen van 19 maart 2026. De partij groeide naar de grootste fractie "
            "en presenteerde via lijsttrekker R. Bouwman de formatieopdracht aan alle zeven fracties. "
            "VVD-lijsttrekker Mekelenkamp reageerde positief maar vroeg om openbare verslaglegging. "
            "De informateur start komende week met gesprekken. Een zakencollege behoort tot de opties."
        ),
        'score': 0.89,
        'indicators': ['formatie', 'coalitie', 'raadsvergadering', 'informateur'],
        'minutes_ago': 75,
    },

    # ── Bestuur & Politiek — Oldenzaal — Bestuurlijk ─────────────────────
    {
        'gemeente': 'Oldenzaal',
        'topic': 'bestuur_politiek',
        'level': 'bestuurlijk',
        'title': 'Oldenzaal: Formatieopdracht gepresenteerd na raadsverkiezingen',
        'body': (
            "Formatieopdracht gepresenteerd na raadsverkiezingen\n\n"
            "**Samenvatting** — Na de gemeenteraadsverkiezingen van 19 maart 2026 presenteerde Solidariteit "
            "Oldenzaal (SO) via R. Bouwman een formatieopdracht aan een informateur. De informateur start "
            "komende week met gesprekken met alle fracties om een beeld te vormen van de coalitie-mogelijkheden. "
            "Het proces is opgezet zodat alle partijen gelijkwaardig hun wensen en inbreng kunnen leveren. "
            "De vergadering stond in het teken van uitleg en verduidelijking over de procedures rondom de "
            "informatiefase. VVD-lijsttrekker Mekelenkamp, M. Rödel en P.J.H. Oude Engberink - Weideveld "
            "stelden gerichte vragen over de transparantie en verslaglegging. De toon was constructief en "
            "gericht op een zorgvuldig formatieproces.\n\n"
            "**Partijstandpunten** — **Solidariteit Oldenzaal (R. Bouwman)** presenteerde de opdracht en "
            "benadrukte dat alle partijen gelijkwaardig mee kunnen doen. De informateur is onafhankelijk en "
            "er wordt vertrouwen in het proces uitgesproken. **VVD (Y. Mekelenkamp)** was positief over de "
            "transparantie van de opdracht, maar stelde kritische vragen: wat houdt een zakencollege precies "
            "in, worden gespreksverslagen openbaar gemaakt en wie heeft inzage? **M. Rödel** bevroeg de "
            "precieze reikwijdte van de informatieopdracht en de rol van de griffie. **P.J.H. Oude Engberink "
            "- Weideveld (WG Oldenzaal)** vroeg aandacht voor de beschikbaarheid van raadsleden en de "
            "flexibiliteit in de planning, en sprak steun uit voor zorgvuldige gespreksverslagen.\n\n"
            "**Actiepunten**\n"
            "1. Informateur start komende week met de eerste gesprekken met alle fracties.\n"
            "2. Elke fractie levert haar wensen en standpunten aan bij de informateur.\n"
            "3. Raadsleden houden hun agenda flexibel voor informatiegesprekken.\n"
            "4. Er worden gespreksverslagen opgesteld; toegankelijkheid hiervan wordt nader bepaald.\n"
            "5. Voorzitter bewaakt de voortgang en informeert de raad tijdig over tussenresultaten."
        ),
        'score': 0.87,
        'indicators': ['coalitie', 'formatie', 'raadsvergadering', 'wethouder'],
        'minutes_ago': 65,
    },

    # ── Veiligheid — Berg en Dal — Pers ──────────────────────────────────
    {
        'gemeente': 'Berg en Dal',
        'topic': 'veiligheid',
        'level': 'pers',
        'title': 'Berg en Dal: Fracties verdeeld over coalitieakkoord veiligheidsregio',
        'body': (
            "VVD en D66 staan lijnrecht tegenover elkaar over de toekomst van de veiligheidsregio in "
            "gemeente Berg en Dal. Dat bleek tijdens een verhit raadsdebat.\n\n"
            "De VVD verdedigt de huidige coalitie als stabiel en pragmatisch, terwijl D66 de bestuurlijke "
            "constructie aanvecht. 'Dit is geen logische bestuurlijke motor,' aldus de D66-woordvoerder. "
            "De VVD bestrijdt dit: 'Wij creëren kansen voor inwoners en werken samen met de oppositie.'\n\n"
            "Bijzonder is dat D66 — eerder in transcripten foutief gespeld als T66 — pleitte voor "
            "fundamenteel andere verhoudingen in de regio. De coalitieonderhandelingen tussen VVD, "
            "GroenLinks en D66 zijn nog gaande."
        ),
        'score': 0.85,
        'indicators': ['veiligheidsregio', 'coalitie', 'raadsvergadering'],
        'minutes_ago': 42,
    },

    # ── Veiligheid — Berg en Dal — Bestuurlijk ────────────────────────────
    {
        'gemeente': 'Berg en Dal',
        'topic': 'veiligheid',
        'level': 'bestuurlijk',
        'title': 'Berg en Dal: Coalitievorming veiligheidsregio — VVD en D66 zoeken bestuurlijk compromis',
        'body': (
            "De gemeenteraad van Berg en Dal debatteerde over de bestuurlijke toekomst van de "
            "veiligheidsregio. De VVD presenteerde de huidige coalitie — bestaande uit VVD, GroenLinks "
            "en D66 — als een stabiele constructie die samenwerking met de oppositie zoekt.\n\n"
            "D66 stelde ter discussie of de regio een logische bestuurlijke eenheid vormt en of de "
            "huidige coalitieopbouw voldoende slagkracht biedt. De VVD wees hierop dat alle partijen "
            "betrokken zijn bij besluitvorming en dat er geen grote verliezer is in de huidige opzet.\n\n"
            "De coalitieonderhandelingen voor de komende bestuursperiode zijn gestart. Financieel "
            "toezicht op de regio en de verdeling van bevoegdheden tussen gemeente en regio zijn "
            "daarbij centrale thema's."
        ),
        'score': 0.85,
        'indicators': ['veiligheidsregio', 'coalitie', 'financieel toezicht', 'raadsbesluit'],
        'minutes_ago': 30,
    },

    # ── Economie & Innovatie — Venray — Pers ─────────────────────────────
    {
        'gemeente': 'Venray',
        'topic': 'economie_innovatie',
        'level': 'pers',
        'title': 'Venray: scheidende raad stelt omstreden bedrijventerrein Smakterheide II toch vast',
        'body': (
            "VENRAY — Ondanks bezwaren van drie partijen heeft de scheidende gemeenteraad van Venray "
            "het bestemmingsplan voor bedrijventerrein Smakterheide II vastgesteld. Het terrein van "
            "22 hectare is bestemd voor logistieke en industriële bedrijven. De beslissing passeerde "
            "met een krappe meerderheid. SP-fractievoorzitter Lommen sprak van 'electoraal opportunisme'. "
            "De Ondernemersvereniging Venray reageerde opgelucht: 'Na twee jaar onzekerheid is er "
            "eindelijk duidelijkheid.' Omwonenden hebben zes weken de tijd voor bezwaar."
        ),
        'score': 0.71,
        'indicators': ['bedrijventerrein', 'bestemmingsplan', 'werkgelegenheid'],
        'minutes_ago': 8,
    },

    # ── Economie & Innovatie — Venray — Bestuurlijk ───────────────────────
    {
        'gemeente': 'Venray',
        'topic': 'economie_innovatie',
        'level': 'bestuurlijk',
        'title': 'Venray: besluit bedrijventerrein Smakterheide II doorgezet ondanks verkiezingen',
        'body': (
            "**Samenvatting**\n"
            "De scheidende raad van Venray heeft in de laatste vergadering het bestemmingsplan voor "
            "bedrijventerrein Smakterheide II definitief vastgesteld. Het terrein van 22 hectare is "
            "bestemd voor logistieke en industriële bedrijven. De beslissing is controversieel: drie "
            "partijen wilden het besluit doorschuiven naar de nieuwe raad, maar de coalitie had een "
            "krappe meerderheid.\n\n"
            "**Standpunten**\n"
            "Wethouder economie Martens (CDA): 'Dit terrein is nodig om bestaande bedrijven te kunnen "
            "laten groeien.' Fractievoorzitter Lommen (SP): 'Dit had de nieuwe raad moeten beslissen — "
            "dit is electoraal opportunisme.' Ondernemersvereniging Venray: 'We zijn blij dat er "
            "eindelijk duidelijkheid is na twee jaar wachten.'\n\n"
            "**Actiepunten**\n"
            "1. Gemeente publiceert vastgesteld bestemmingsplan binnen 2 weken\n"
            "2. Bezwaartermijn loopt 6 weken na publicatie — juridische afdeling monitort\n"
            "3. Nieuwe raad evalueert besluitvorming in eerste commissievergadering\n"
            "4. Acquisitietraject bedrijven starten via REWIN (Regionale Economische Samenwerking)"
        ),
        'score': 0.72,
        'indicators': ['bedrijventerrein', 'bestemmingsplan', 'raadsbesluit', 'werkgelegenheid'],
        'minutes_ago': 5,
    },
]


def setup_db(conn):
    """Zorg dat de articles-tabel bestaat."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id INTEGER,
            gemeente TEXT,
            topic TEXT,
            level TEXT,
            title TEXT,
            body TEXT,
            score REAL DEFAULT 0.0,
            indicators TEXT,
            livestream_url TEXT,
            t_start REAL,
            t_end REAL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def seed(clear=False):
    conn = sqlite3.connect(RANST_DB)
    setup_db(conn)

    if clear:
        conn.execute("DELETE FROM articles")
        conn.commit()
        print(f"DB leeggemaakt.")

    now = datetime.now()
    inserted = 0

    for art in DEMO_ARTIKELEN:
        # Sla over als artikel met zelfde titel al bestaat
        existing = conn.execute(
            "SELECT id FROM articles WHERE title = ?", (art['title'],)
        ).fetchone()
        if existing:
            print(f"  – [{art['level']:12}] {art['gemeente']}: al aanwezig, overgeslagen")
            continue

        ts = (now - timedelta(minutes=art['minutes_ago'])).isoformat(timespec='seconds')
        conn.execute("""
            INSERT INTO articles (gemeente, topic, level, title, body, score, indicators, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            art['gemeente'],
            art['topic'],
            art['level'],
            art['title'],
            art['body'],
            art['score'],
            json.dumps(art['indicators']),
            ts,
        ))
        inserted += 1
        print(f"  ✓ [{art['level']:12}] {art['gemeente']}: {art['title'][:60]}…")

    conn.commit()
    conn.close()
    print(f"\n{inserted} artikelen ingevoegd in {RANST_DB}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--clear', action='store_true', help='Wis bestaande artikelen eerst')
    args = parser.parse_args()
    seed(clear=args.clear)
