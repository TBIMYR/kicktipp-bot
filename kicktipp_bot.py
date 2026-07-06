#!/usr/bin/env python3
"""Schlanker kicktipp-Tippbot fuer den lokalen, persoenlichen Gebrauch.

Macht nur das Noetige:
    1. Login (HTTPS)
    2. Spiele + Quoten der Tippabgabe-Seite lesen
    3. predict(match) -> Tipp
    4. Formular absenden (ausser im Dry-Run)

Aufruf:
    python kicktipp_bot.py            # Dry-Run: zeigt nur die Tipps an
    python kicktipp_bot.py --submit   # Tipps wirklich abgeben
    python kicktipp_bot.py --override # auch schon gesetzte Tipps ueberschreiben
    python kicktipp_bot.py -v         # ausfuehrliche Ausgabe (Debug-Parsing)

Zugangsdaten kommen aus (in dieser Reihenfolge):
    - kicktipp.ini  ([credentials] user=..., password=...)
    - Umgebungsvariablen KICKTIPP_USER / KICKTIPP_PASSWORD
    - interaktiver Prompt
"""

from __future__ import annotations

import argparse
import configparser
import getpass
import os
import re
import sys
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

# --- Konfiguration -----------------------------------------------------------

BASE = "https://www.kicktipp.de"
LOGIN_URL = BASE + "/info/profil/login"

# Tipprunde (Name aus der URL, z.B. www.kicktipp.de/MEINE-RUNDE/tippabgabe).
# Wird aus kicktipp.ini [game] community gelesen (oder KICKTIPP_COMMUNITY),
# damit der eigene Rundenname nicht im Code/Repo landet.
def _load_community() -> str:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(os.path.join(os.path.dirname(__file__), "kicktipp.ini"))
    if cfg.has_option("game", "community"):
        return cfg["game"]["community"]
    return os.environ.get("KICKTIPP_COMMUNITY", "DEINE-TIPPRUNDE")


COMMUNITY = _load_community()


# --- Datenmodell -------------------------------------------------------------

@dataclass
class Match:
    home: str
    away: str
    # Quoten (Sieg Heim / Unentschieden / Sieg Gast); None wenn nicht vorhanden
    odd_home: float | None = None
    odd_draw: float | None = None
    odd_away: float | None = None
    # Namen der Formularfelder fuer Heim-/Gasttipp
    field_home: str = ""
    field_away: str = ""

    def __str__(self) -> str:
        odds = ""
        if self.odd_home:
            odds = f"  [{self.odd_home}/{self.odd_draw}/{self.odd_away}]"
        return f"{self.home} - {self.away}{odds}"


# --- HIER deine Tipp-Logik ----------------------------------------------------

# Manuelle Overrides aus Fundamentals-Analyse (Verletzungen/Form), schlagen die Quote.
OVERRIDES = {
    # Brasilien ohne Neymar (Wade) & Rodrygo (Kreuzband) im Auftakt; Marokko #7 stark.
    ("Brasilien", "Marokko"): (2, 1),
    # Kanada Favorit, aber Abwehr geschwaecht (Bombito raus, Davies fraglich) -> knapper.
    ("Kanada", "Bosnien-Herzegowina"): (2, 1),
    # --- Spieltag 2 (Auftakte Gruppen E-H) ---
    # Deutschland 9 Siege in Folge, Curaçao kleinste je qualifizierte Nation -> deutlich.
    ("Deutschland", "Curaçao"): (3, 0),
    # Spanien trotz vieler Ausfaelle klar ueberlegen (Quote 1.11).
    ("Spanien", "Kap Verde"): (3, 0),
    # Belgien top in Form, aber Aegypten (Salah) trifft -> 2:1 statt 2:0.
    ("Belgien", "Ägypten"): (2, 1),
    # Ecuador knapp favorisiert, aber extrem torarm -> 0:1 statt 1:2.
    ("Elfenbeinküste", "Ecuador"): (0, 1),
    # NL dezimiert (Simons/de Ligt/Timber raus, Verbruggen fraglich), Japan in Topform
    # (schlug England) -> Muenzwurf, daher Remis statt knappem NL-Sieg.
    ("Niederlande", "Japan"): (1, 1),
    # --- Spieltag 3: Favoriten treffen auf Qualitaetsgegner -> Gegentor einplanen
    # (Turniermuster bisher: viele Remis, kaum Clean Sheets der Favoriten).
    ("Frankreich", "Senegal"): (2, 1),   # Senegal (Mane/Sarr) trifft
    ("England", "Kroatien"): (2, 1),     # Kroatien (Modric) kein Spaziergang
    ("Argentinien", "Algerien"): (2, 1), # Algeriens Angriff (Mahrez) stark
    # --- Spieltag 4 ---
    # Muenzwurf, beide 3 Pkt, Montes (MEX) gesperrt, Korea konterstark -> kein Clean Sheet.
    ("Mexiko", "Südkorea"): (2, 1),
    # Schottland sehr torarm (1 Tor MD1), Marokko knapp vorn -> schmaler Sieg ohne SCO-Tor.
    ("Schottland", "Marokko"): (0, 1),
    # Beide kamen mit 1:1, Schweiz mit peinlichem Auftakt-Remis -> Bosnien (Dzeko) trifft.
    ("Schweiz", "Bosnien-Herzegowina"): (2, 1),
    # --- Spieltag 5 (Gruppen E-H, Runde 2) mit aktueller Form ---
    # NL dezimiert & nur 2:2 vs Japan, Schweden in Topform (5:1) -> Remis.
    ("Niederlande", "Schweden"): (1, 1),
    # Spitzenspiel; DE in Topform (7:1) aber gegen Curacao 1 Gegentor -> Elfenbeinkueste trifft.
    ("Deutschland", "Elfenbeinküste"): (2, 1),
    # Belgien wackelig (nur 1:1 vs Aegypten), Iran traf zuletzt 2x -> Iran-Tor einplanen.
    ("Belgien", "Iran"): (2, 1),
    # Kap Verde hielt Spanien 0:0 (extrem defensiv, 0 Tore) -> Uruguay grindet schmal.
    ("Uruguay", "Kap Verde"): (1, 0),
    # Aegypten (Salah) knapp favorisiert, aber Neuseeland traf 2x vs Iran -> NZ trifft.
    ("Neuseeland", "Ägypten"): (1, 2),
    # Top-Duell beide 3 Pkt; Australien traf 2x im Auftakt -> kein US-Clean-Sheet.
    ("USA", "Australien"): (2, 1),
    # --- Spieltag 6 (Gruppen I-L, Runde 2) ---
    # Spitzenspiel; Oesterreich in Form (3:1) trifft.
    ("Argentinien", "Österreich"): (2, 1),
    # England firing aber defensiv anfaellig (2 Gegentore vs Kroatien); Ghana trifft.
    ("England", "Ghana"): (2, 1),
    # --- Stand 20.06.: SD5/SD6 mit aktueller Form & Verletzungen nachgeschaerft ---
    # Japan ohne Kubo (Knie), Tunesien (neuer Coach Renard) muss daheim gewinnen
    # -> Japan-Sieg, aber Tunesien trifft (Turniermuster: kaum Clean Sheets).
    ("Tunesien", "Japan"): (1, 2),
    # DR Kongo hielt Portugal 1:1 und hat echte Offensivqualitaet -> kein Clean Sheet.
    ("Kolumbien", "DR Kongo"): (2, 1),
    # Algerien (Mahrez/Amoura) Favorit, aber beide muessen gewinnen & Jordanien traf
    # gegen Oesterreich -> Algerien-Sieg mit Gegentor.
    ("Jordanien", "Algerien"): (1, 2),
    # Kroatien (Modric) Favorit, kassierte aber 4 gegen England; Panama war vs Ghana
    # konkurrenzfaehig -> Kroatien-Sieg, Panama trifft.
    ("Panama", "Kroatien"): (1, 2),
    # Pins: bereits gesetzte Tipps festhalten, deren Quote sich verschoben hat,
    # damit --override sie nicht ungewollt aendert (noch nicht analysiert).
    ("Türkei", "USA"): (1, 1),
    # Beide muessen gewinnen -> Remis hilft keinem; knapper CV-Heimsieg statt 1:1.
    ("Kap Verde", "Saudi-Arabien"): (1, 0),
    ("Japan", "Schweden"): (2, 1),
    ("DR Kongo", "Usbekistan"): (2, 1),
    ("Kolumbien", "Portugal"): (1, 2),
    # --- Stand 24.06. (Finalrunden, Quali-Mathe) — knappe Favoritensiege statt
    # ueberzogener Clean Sheets; bewusst KEINE reinen Remis-Flips. ---
    # DE qualifiziert & rotiert massiv (Schlotterbeck out), ECU muss gewinnen, aber
    # 0 Tore in 2 Spielen -> rotiertes DE gewinnt knapp, ECU bleibt torlos.
    ("Ecuador", "Deutschland"): (0, 1),
    # Beide durch, Duell um Platz 1; Haaland+Mbappe in Form -> FRA-Sieg ohne Clean Sheet.
    ("Norwegen", "Frankreich"): (1, 2),
    # Belgien stumpf (1 Tor in 2, 0:0 vs Iran) aber muss gewinnen; NZ traf beide Male.
    ("Neuseeland", "Belgien"): (1, 2),
    # Almiron (PAR) gesperrt; Australien reicht Remis & mauert -> knapper PAR-Sieg.
    ("Paraguay", "Australien"): (1, 0),
    # Ghana 0 Gegentore & reicht Remis -> tief & knapp, kein 2-Tore-Vorsprung.
    ("Kroatien", "Ghana"): (1, 0),
    # SA muss gewinnen & oeffnet, Korea reicht Remis -> knapper KOR-Sieg statt 0:2.
    ("Südafrika", "Südkorea"): (0, 1),
    # --- Stand 22.06. ---
    # Frankreich brennt (3:1 vs Senegal), Irak kassierte 4 vs Norwegen, Quote 1.09
    # -> klarer Sieg statt knappem 2:0.
    ("Frankreich", "Irak"): (3, 0),
    # Finalrunde Gr. C: Raphinha raus, Neymar dosiert, Brasilien reicht ein Remis,
    # Schottland steht tief -> knapper BRA-Sieg statt 0:2.
    ("Schottland", "Brasilien"): (0, 1),
    # Finalrunde Gr. A: Mexiko qualifiziert (0 Gegentore) & rotiert, braucht nur Remis;
    # Tschechien muss gewinnen -> Remis statt Mexiko-Sieg.
    ("Tschechien", "Mexiko"): (1, 1),
    # --- Sechzehntelfinale (Stand 28.06.) — 90-Min-Tipps. Schwere Favoriten gegen
    # harmlose Defensiv-Teams = Clean Sheet (DE/FRA/USA/ARG/ENG ueber Quote = ok);
    # echte Muenzwuerfe als REMIS (Turnier ist extrem remis-lastig!). ---
    # Kanada qualitativ besser, aber SA defensiv stabil -> knapper Sieg statt 0:2.
    ("Südafrika", "Kanada"): (0, 1),
    # Brasilien ohne Raphinha, Japan ungeschlagen & konterstark -> kein Clean Sheet.
    ("Brasilien", "Japan"): (2, 1),
    # Muenzwurf, aber K.o. erlaubt KEIN Remis -> Sieger waehlen: NL ohne Simons/Timber/
    # de Ligt/Schouten & Koeman nennt sich Underdog -> Marokko (fit, Hakimi) kommt weiter.
    ("Niederlande", "Marokko"): (1, 2),
    # Zwei beste Defensiven des Turniers (MEX 0 Gegentore), Azteca+Ruhe -> knapp 1:0.
    ("Mexiko", "Ecuador"): (1, 0),
    # Spanien ungeschlagen & klar ueberlegen; Oesterreich leck (3:3 vs Algerien) -> 2:0.
    ("Spanien", "Österreich"): (2, 0),
    # Portugal favorisiert, aber Sturm stottert & Kroatien trifft -> 2:1 statt Clean Sheet.
    ("Portugal", "Kroatien"): (2, 1),
    # Schweiz Gruppensieger & ausgeglichener; Algerien (Mahrez) trifft -> 2:1 (Wackel).
    ("Schweiz", "Algerien"): (2, 1),
    # Muenzwurf, aber K.o. erlaubt KEIN Remis -> Aegypten leichter Favorit (Quote 2.40);
    # Salah angeschlagen, aber im K.o. wohl geflickt -> Aegypten kommt weiter.
    ("Australien", "Ägypten"): (1, 2),
    # Zwei sture Defensiven, Kolumbien torarm (4 Tore, 1 Gegentor) -> knapp 1:0.
    ("Kolumbien", "Ghana"): (1, 0),
    # Frankreich klar ueberlegen, aber Schweden hat Isak+Gyoekeres -> kein Clean Sheet.
    ("Frankreich", "Schweden"): (3, 1),
    # --- Achtelfinale (Stand 02.07.) — 90-Min-Sieger, KEIN Remis moeglich. ---
    # Marokko staerker/erfahrener (2022-Halbfinalist), aber Kanada trifft daheim-nah.
    ("Kanada", "Marokko"): (1, 2),
    # MUTIGER (Rueckstand aufholen): Norwegen-Upset getippt statt Brasilien-Sieg.
    # Haaland (5 Tore)+Oedegaard, Brasilien ohne Raphinha/Paqueta, NIE gg. BRA verloren.
    ("Brasilien", "Norwegen"): (1, 2),
    # MUTIGER (Rueckstand aufholen): Mexiko-Upset getippt. Azteca+Hoehe, Mexiko im
    # ganzen Turnier 0 Gegentore, England muede/dezimiert -> Mexiko 1:0 mit Clean Sheet.
    ("Mexiko", "England"): (1, 0),
    # USA Heimvorteil & Belgien nach 120 Min muede; Balogun-Sperre von FIFA ausgesetzt
    # (05.07., wieder spielberechtigt) -> volle US-Offensive, knapper USA-Sieg 2:1.
    ("USA", "Belgien"): (2, 1),
    # --- Achtelfinale-Nachzuegler + erstes Viertelfinale (Stand 05.07.) ---
    # Argentinien klar vorn, aber kassierte 2 vs Kap Verde & Salah trifft -> 2:1.
    ("Argentinien", "Ägypten"): (2, 1),
    # Torarmes Duell zweier Defensiven; Kolumbien minimal vorn -> knapp 0:1.
    ("Schweiz", "Kolumbien"): (0, 1),
    # QF: Frankreich Favorit, aber Marokko topfit (Hakimi) & in Form -> kein Clean Sheet.
    ("Frankreich", "Marokko"): (2, 1),
    # MUTIGER (Rueckstand): Portugal-Upset. Spanien nur knapp Favorit (1.95, ~48%),
    # Portugal (Ronaldo/Bruno) schlug sie 2025 in der Nations League -> 2:1.
    ("Portugal", "Spanien"): (2, 1),
}


def predict(match: Match) -> tuple[int, int]:
    """Liefert (heim_tore, gast_tore).

    Erst manuelle Overrides (Fundamentals), sonst quotenbasierte Heuristik.
    """
    if (match.home, match.away) in OVERRIDES:
        return OVERRIDES[(match.home, match.away)]
    if not match.odd_home or not match.odd_away:
        return (1, 1)  # keine Quoten -> Default

    if abs(match.odd_home - match.odd_away) < 0.3:
        return (1, 1)  # ausgeglichen -> Unentschieden
    if match.odd_home < match.odd_away:
        diff = match.odd_away / match.odd_home
        return (2, 0) if diff > 2 else (2, 1)
    else:
        diff = match.odd_home / match.odd_away
        return (0, 2) if diff > 2 else (1, 2)


# --- Zugangsdaten ------------------------------------------------------------

def get_credentials() -> tuple[str, str]:
    cfg = configparser.ConfigParser(interpolation=None)  # Passwoerter duerfen % enthalten
    cfg.read(os.path.join(os.path.dirname(__file__), "kicktipp.ini"))
    if cfg.has_section("credentials"):
        user = cfg["credentials"].get("user")
        pw = cfg["credentials"].get("password")
        if user and pw:
            return user, pw
    user = os.environ.get("KICKTIPP_USER") or input("Username: ")
    pw = os.environ.get("KICKTIPP_PASSWORD") or getpass.getpass("Password: ")
    return user, pw


# --- Login -------------------------------------------------------------------

def login(session: requests.Session) -> None:
    """Loggt ein und behaelt das Session-Cookie. Bricht bei Fehler ab."""
    user, pw = get_credentials()

    # Login-Seite holen, damit evtl. versteckte Felder/Tokens mitgehen.
    resp = session.get(LOGIN_URL)
    resp.raise_for_status()
    form = BeautifulSoup(resp.text, "html.parser").find("form")

    data = _form_fields(form)
    data["kennung"] = user
    data["passwort"] = pw

    action = (form.get("action") if form else None) or LOGIN_URL
    if action.startswith("/"):
        action = BASE + action
    resp = session.post(action, data=data)
    resp.raise_for_status()

    if "login" not in session.cookies:
        sys.exit("Login fehlgeschlagen - Username/Passwort pruefen.")


# --- Spiele lesen ------------------------------------------------------------

def fetch_spieltag_urls(session: requests.Session) -> list[tuple[str, str]]:
    """Liest die Spieltag-Navigation und liefert [(Label, URL), ...].

    Nur regulaere Spieltage (kein Bonus). Reihenfolge wie auf der Seite.
    """
    url = f"{BASE}/{COMMUNITY}/tippabgabe"
    soup = BeautifulSoup(session.get(url).text, "html.parser")
    seen: dict[str, str] = {}
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "spieltagIndex=" not in href or "bonus=true" in href:
            continue
        label = a.get_text(strip=True)
        if not label:
            continue
        full = BASE + href if href.startswith("/") else href
        if full in seen:
            continue
        seen[full] = label
        out.append((label, full))
    return out


def fetch_matches(session: requests.Session, verbose: bool = False,
                  url: str | None = None) -> list[Match]:
    """Liest Spiele + Quoten + Formularfeldnamen von der Tippabgabe-Seite.

    >>> Das ist der Teil, der bei HTML-Aenderungen von kicktipp angepasst
        werden muss. Mit -v siehst du, was gefunden wurde. <<<
    """
    if url is None:
        url = f"{BASE}/{COMMUNITY}/tippabgabe"
    resp = session.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    content = soup.find(id="kicktipp-content")
    if content is None:
        sys.exit(f"Tippabgabe-Bereich nicht gefunden ({url}). Community korrekt?")
    tbody = content.find("tbody")
    if tbody is None:
        sys.exit("Keine Spieltabelle gefunden - HTML evtl. geaendert (mit -v pruefen).")

    matches: list[Match] = []
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        home_in = cells[3].find("input", id=lambda x: x and x.endswith("_heimTipp"))
        away_in = cells[3].find("input", id=lambda x: x and x.endswith("_gastTipp"))
        if not home_in or not away_in:
            if verbose:
                print(f"  uebersprungen (keine Tippfelder): {tr.get_text(' ', strip=True)[:60]}")
            continue

        m = Match(
            home=cells[1].get_text(strip=True),
            away=cells[2].get_text(strip=True),
            field_home=home_in.get("name", ""),
            field_away=away_in.get("name", ""),
        )
        if len(cells) > 4:
            # Quoten-Zelle sieht aus wie "1 1.42 X 4.40 2 8.00" -> Dezimalzahlen ziehen
            nums = re.findall(r"\d+[.,]\d+", cells[4].get_text(" "))
            if len(nums) >= 3:
                m.odd_home, m.odd_draw, m.odd_away = (float(n.replace(",", ".")) for n in nums[:3])
        matches.append(m)

    return matches


# --- Tipps absenden ----------------------------------------------------------

def submit_bets(session: requests.Session, matches: list[Match],
                override: bool, dry_run: bool, verbose: bool,
                url: str | None = None) -> int:
    if url is None:
        url = f"{BASE}/{COMMUNITY}/tippabgabe"
    form = BeautifulSoup(session.get(url).text, "html.parser").find("form")
    data = _form_fields(form)

    placed = 0
    for m in matches:
        # K.o.-Runden ohne feststehende Teams ueberspringen (z.B. "unbekannt").
        if "unbekannt" in f"{m.home} {m.away}".lower():
            if verbose:
                print(f"{m} -> Teams stehen noch nicht fest (uebersprungen)")
            continue
        current_home = data.get(m.field_home, "")
        current_away = data.get(m.field_away, "")
        if not override and (current_home or current_away):
            print(f"{m} -> schon getippt {current_home}:{current_away} (uebersprungen)")
            continue
        h, a = predict(m)
        print(f"{m} -> Tipp {h}:{a}")
        data[m.field_home] = str(h)
        data[m.field_away] = str(a)
        placed += 1

    if placed == 0:
        print("Nichts zu tippen.")
        return 0
    if dry_run:
        print(f"\nDRY-RUN: {placed} Tipp(s) NICHT abgesendet. Mit --submit echt abgeben.")
        return placed

    action = (form.get("action") if form else None) or url
    if action.startswith("/"):
        action = BASE + action
    # Nur match-relevante Felder posten. Die Tippabgabe-Seite enthaelt auch die
    # Bonus-Felder (fragetippForms) - leer mitgeschickt loesen die einen 500 aus
    # und wuerden zudem bestehende Bonustipps ueberschreiben.
    data = {k: v for k, v in data.items() if not k.startswith("fragetippForms")}
    data["submitbutton"] = "submitbutton"
    resp = session.post(action, data=data)
    resp.raise_for_status()
    print(f"\n{placed} Tipp(s) abgesendet.")
    return placed


# --- Helfer ------------------------------------------------------------------

def _form_fields(form) -> dict[str, str]:
    """Alle vorhandenen Formular-Inputs als dict (inkl. versteckter Felder)."""
    data: dict[str, str] = {}
    if form is None:
        return data
    for inp in form.find_all(("input", "select", "textarea")):
        name = inp.get("name")
        if name:
            data[name] = inp.get("value", "")
    return data


# --- Einstieg ----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Schlanker kicktipp-Tippbot (lokal).")
    ap.add_argument("--submit", action="store_true", help="Tipps wirklich abgeben (sonst Dry-Run).")
    ap.add_argument("--override", action="store_true", help="Auch schon gesetzte Tipps ueberschreiben.")
    ap.add_argument("--all", action="store_true",
                    help="Alle Spieltage durchgehen (statt nur dem aktuell angezeigten).")
    ap.add_argument("-v", "--verbose", action="store_true", help="Ausfuehrliche Ausgabe.")
    args = ap.parse_args()

    if COMMUNITY == "DEINE-TIPPRUNDE":
        sys.exit("Bitte oben im Skript COMMUNITY auf deine Tipprunde setzen.")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (kicktipp-bot)"

    login(session)

    if not args.all:
        matches = fetch_matches(session, verbose=args.verbose)
        if not matches:
            sys.exit("Keine Spiele mit Tippfeldern gefunden.")
        print(f"{len(matches)} Spiel(e) gefunden:\n")
        submit_bets(session, matches, override=args.override,
                    dry_run=not args.submit, verbose=args.verbose)
        return

    # --all: ueber alle Spieltage iterieren. K.o.-Runden ohne feststehende Teams
    # werden in submit_bets uebersprungen.
    total = 0
    for label, url in fetch_spieltag_urls(session):
        matches = fetch_matches(session, verbose=args.verbose, url=url)
        tippbar = [m for m in matches if "unbekannt" not in f"{m.home} {m.away}".lower()]
        if not tippbar:
            if args.verbose:
                print(f"=== {label}: keine feststehenden Spiele (uebersprungen) ===")
            continue
        print(f"=== {label}: {len(tippbar)} Spiel(e) ===")
        total += submit_bets(session, matches, override=args.override,
                             dry_run=not args.submit, verbose=args.verbose, url=url) or 0
        print()
    verb = "abgegeben" if args.submit else "im Dry-Run vorbereitet"
    print(f"Gesamt: {total} Tipp(s) {verb}.")


if __name__ == "__main__":
    main()
