# kicktipp-bot

Schlanker, lokaler Bot, der Tipps auf [kicktipp.de](https://www.kicktipp.de) automatisch setzt —
Spielergebnisse und Bonusfragen. Modern (`requests` + `BeautifulSoup`, HTTPS), Default ist **Dry-Run**.

> Inoffiziell: kicktipp hat keine API, der Bot arbeitet per Web-Login/Scraping.
> Nur für den eigenen Account und auf eigene Verantwortung verwenden.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp kicktipp.ini.example kicktipp.ini   # dann Email, Passwort & Rundenname eintragen
```

`kicktipp.ini` enthält deine Zugangsdaten und ist per `.gitignore` ausgeschlossen —
sie wird **nie** committet.

```ini
[credentials]
user = deine-email@example.com
password = dein-passwort

[game]
community = deine-tipprunde      # aus der URL: kicktipp.de/<community>/tippabgabe
```

## Benutzung

```bash
# Spielergebnisse
.venv/bin/python kicktipp_bot.py            # Dry-Run: zeigt nur die Tipps
.venv/bin/python kicktipp_bot.py --submit   # Tipps wirklich abgeben
.venv/bin/python kicktipp_bot.py --submit --override   # auch gesetzte Tipps überschreiben

# Bonusfragen (Picks oben in submit_bonus.py anpassen)
.venv/bin/python submit_bonus.py            # Dry-Run
.venv/bin/python submit_bonus.py --submit
```

## Eigene Tipp-Logik

Die Vorhersage steckt in `predict(match)` in `kicktipp_bot.py` (Standard: simple
Quoten-Heuristik). Einzelne Spiele lassen sich über das `OVERRIDES`-Dict fest setzen.

## Sicherheit

- Zugangsdaten liegen nur lokal in `kicktipp.ini` (gitignored), nie im Code.
- Der Bot spricht ausschließlich mit `www.kicktipp.de`, kein Tracking, keine Drittserver.

## Credits

Inspiriert von [schwalle/kicktipp-betbot](https://github.com/schwalle/kicktipp-betbot) (MIT) —
unabhängig neu implementiert mit `requests`/`BeautifulSoup` (HTTPS, Config-basiert, inkl. Bonusfragen).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
