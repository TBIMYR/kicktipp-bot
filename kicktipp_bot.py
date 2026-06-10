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

def fetch_matches(session: requests.Session, verbose: bool = False) -> list[Match]:
    """Liest Spiele + Quoten + Formularfeldnamen von der Tippabgabe-Seite.

    >>> Das ist der Teil, der bei HTML-Aenderungen von kicktipp angepasst
        werden muss. Mit -v siehst du, was gefunden wurde. <<<
    """
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
                override: bool, dry_run: bool, verbose: bool) -> None:
    url = f"{BASE}/{COMMUNITY}/tippabgabe"
    form = BeautifulSoup(session.get(url).text, "html.parser").find("form")
    data = _form_fields(form)

    placed = 0
    for m in matches:
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
        return
    if dry_run:
        print(f"\nDRY-RUN: {placed} Tipp(s) NICHT abgesendet. Mit --submit echt abgeben.")
        return

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
    ap.add_argument("-v", "--verbose", action="store_true", help="Ausfuehrliche Ausgabe.")
    args = ap.parse_args()

    if COMMUNITY == "DEINE-TIPPRUNDE":
        sys.exit("Bitte oben im Skript COMMUNITY auf deine Tipprunde setzen.")

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (kicktipp-bot)"

    login(session)
    matches = fetch_matches(session, verbose=args.verbose)
    if not matches:
        sys.exit("Keine Spiele mit Tippfeldern gefunden.")
    print(f"{len(matches)} Spiel(e) gefunden:\n")
    submit_bets(session, matches, override=args.override,
                dry_run=not args.submit, verbose=args.verbose)


if __name__ == "__main__":
    main()
