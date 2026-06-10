#!/usr/bin/env python3
"""Bonusfragen der kicktipp-Runde tippen (Gruppensieger, Halbfinale, WM, Torjaeger).

Picks sind favoritenbasiert und stehen unten in PICKS - einfach anpassen.
Default ist Dry-Run; mit --submit wird wirklich abgesendet.
Deadline der Bonusfragen: 11.06.26 21:00.
"""

from __future__ import annotations

import argparse
import sys

import requests
from bs4 import BeautifulSoup

import kicktipp_bot as kb

BONUS_URL = f"{kb.BASE}/{kb.COMMUNITY}/tippabgabe?bonus=true&spieltagIndex=1"

# --- Picks: hier deine Tipps eintragen (Teamnamen exakt wie im Dropdown) ------
# Leere Werte werden uebersprungen. Mit -v / Dry-Run siehst du, was gesetzt wird.
GROUP_WINNERS = {
    "A": "", "B": "", "C": "", "D": "",
    "E": "", "F": "", "G": "", "H": "",
    "I": "", "J": "", "K": "", "L": "",
}
SEMIFINAL = ["", "", "", ""]   # 4 Teams, die das Halbfinale erreichen
CHAMPION = ""                  # Weltmeister
TOPSCORER_TEAM = ""            # Team des Torschuetzenkoenigs


def pick_for(question: str, semi_iter) -> str | None:
    q = question.lower()
    if "gruppe" in q:
        for letter, team in GROUP_WINNERS.items():
            if f"gruppe {letter.lower()}" in q:
                return team
    if "halbfinale" in q:
        return next(semi_iter, None)
    if "weltmeister" in q:
        return CHAMPION
    if "meisten toren" in q or "torsch" in q:
        return TOPSCORER_TEAM
    return None


def option_value(select, team: str) -> str | None:
    """Wert der Option finden, deren Text dem Team entspricht (umlautrobust)."""
    for o in select.find_all("option"):
        if o.get_text(strip=True).replace("ç", "c") == team.replace("ç", "c"):
            return o.get("value")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true", help="Bonustipps wirklich abgeben.")
    args = ap.parse_args()

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (kicktipp-bot)"
    kb.login(s)

    form = BeautifulSoup(s.get(BONUS_URL).text, "html.parser").find("form")
    if form is None:
        sys.exit("Bonus-Formular nicht gefunden.")

    data = kb._form_fields(form)
    semi_iter = iter(SEMIFINAL)

    chosen = 0
    for sel in form.find_all("select"):
        name = sel.get("name")
        tr = sel.find_parent("tr")
        question = tr.get_text(" ", strip=True) if tr else ""
        team = pick_for(question, semi_iter)
        if not team:
            print(f"  ? keine Regel fuer: {question[:60]}")
            continue
        val = option_value(sel, team)
        if not val:
            print(f"  ! Option '{team}' nicht gefunden in: {question[:50]}")
            continue
        label = question.split("21:00", 1)[-1].strip()[:48]
        print(f"  {label:50} -> {team}")
        data[name] = val
        chosen += 1

    if not args.submit:
        print(f"\nDRY-RUN: {chosen} Bonustipps NICHT abgesendet. Mit --submit echt abgeben.")
        return

    action = form.get("action") or BONUS_URL
    if action.startswith("/"):
        action = kb.BASE + action
    data["submitbutton"] = "submitbutton"
    r = s.post(action, data=data)
    r.raise_for_status()
    print(f"\n{chosen} Bonustipps abgesendet.")


if __name__ == "__main__":
    main()
