# blockedfps | Stats

Python-Discord-Bot für `/stats [platform] [username]` mit Components V2, K/D, aktuellem Rang und Rangbild im Profilkopf. Für Server, Bot-DMs und – nach Installation zum eigenen Discord-Konto – auch andere DMs und Gruppenchats.

**Standardmäßig nutzt der Bot R6 Arenyze. Trage deinen Discord-Bot-Token und deinen Arenyze-API-Key ein.** Die direkte Anbindung verwendet das dokumentierte V2-Profilformat für Rang und K/D. Ein normaler Tracker.gg-Key ist dafür nicht geeignet. [Arenyze-API-Dokumentation](https://r6.arenyze.com/api-docs)

**Die letzten fünf echten Matches sind mit dieser Arenyze-Anbindung nicht verfügbar.** Im Matchbereich erscheint deshalb ein ausdrücklicher Hinweis. Die Dokumentation beschreibt keinen Abruf der neuesten Matches eines beliebigen Spielers; Rangpunkte-Verlauf und Replay-Uploads ersetzen diese Liste nicht. Das Layout für fünf Matches ist im Demo-Modus und mit einer passenden eigenen HTTP-Quelle vorhanden. [Arenyze-API-Dokumentation](https://r6.arenyze.com/api-docs)

Die Arenyze-Verarbeitung ist anhand des dokumentierten Formats und lokaler Testdaten implementiert. Ein Live-Test mit deinem Schlüssel oder Discord-Token wurde nicht durchgeführt. Details zu Schlüssel und Datenquellen stehen in [API_OPTIONS.md](docs/API_OPTIONS.md).

## Enthalten

- `/stats` mit den Plattformen `pc`, `playstation` und `xbox` sowie frei eingegebenem Benutzernamen.
- Components-V2-Layout mit schwarzer Akzentleiste, Rang-Thumbnail, Trennern und Footer `blockedfps | Stats`.
- Klare Textgliederung mit `>`, `*kursiv*` und `**fett**`.
- Direkte Arenyze-Anbindung sowie optionale Modi für Demo, eigene HTTP-Datenquelle oder deaktivierte Datenabfrage.
- Konfigurierbare Antwortsichtbarkeit, Cache, HTTP-Zeitlimit und Abfragelimit.
- Startdateien für Windows, deutsche Einrichtung und lokale Tests.

**Zur Farbe:** Discord erlaubt bei nativen V2-Containern eine schwarze Akzentleiste (`#000000`). Den Hintergrund bestimmt das Discord-Theme des Betrachters. Ein stets vollständig schwarzer Container lässt sich über diese API nicht erzwingen. Das Rangbild erscheint als Thumbnail neben dem Spielerprofil; der Bot-Avatar bleibt davon unabhängig. [Discord-Komponentenreferenz](https://docs.discord.com/developers/components/reference)

## Schnellstart unter Windows

1. ZIP vollständig in einen eigenen Ordner entpacken.
2. Python **3.11 oder neuer** installieren; den Python-Launcher bzw. Python im PATH aktivieren.
3. `setup.bat` ausführen. Dadurch werden die lokale Python-Umgebung und die Abhängigkeiten eingerichtet.
4. `.env.example` als `.env` kopieren, falls `setup.bat` die Datei noch nicht angelegt hat.
5. In `.env` deinen `DISCORD_TOKEN` und `ARENYZE_API_KEY` einsetzen. `DATA_MODE=arenyze` belassen.
6. Im [Discord Developer Portal](https://discord.com/developers/applications) eine eigene Anwendung für diesen Bot nach [DISCORD_SETUP.md](docs/DISCORD_SETUP.md) einrichten und zu deinem Konto hinzufügen.
7. `start.bat` ausführen und in Discord `/stats platform:pc username:blockedfps` ausprobieren.

Um nur das vollständige Layout mit fünf Beispielmatches auszuprobieren, kannst du stattdessen `DATA_MODE=demo` setzen. Dann ist kein Arenyze-Key nötig; die deutlich markierten Beispieldaten sagen nichts über den eingegebenen Spieler aus.

## Manuelle Installation

Im entpackten Projektordner unter Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Jetzt .env bearbeiten: DISCORD_TOKEN und ARENYZE_API_KEY eintragen.
.\.venv\Scripts\python.exe bot.py
```

Falls `.env` schon existiert, den Kopierschritt überspringen. Unter Linux oder macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Jetzt .env bearbeiten: DISCORD_TOKEN und ARENYZE_API_KEY eintragen.
.venv/bin/python bot.py
```

Der Rechner oder Server, auf dem der Prozess läuft, muss für die Nutzung online bleiben. Einladungen allein hosten den Bot nicht.

## Konfiguration

Alle Einstellungen stehen in `.env`. Änderungen werden nach einem Neustart wirksam.

| Einstellung | Standard | Bedeutung |
| --- | --- | --- |
| `DISCORD_TOKEN` | leer | Bot-Token aus dem Developer Portal; für den Discord-Betrieb erforderlich. |
| `DATA_MODE` | `arenyze` | `arenyze` nutzt die direkte Anbindung; `demo` zeigt Beispiele; `http` nutzt eine eigene Quelle; `disabled` zeigt einen Hinweis. |
| `ARENYZE_API_KEY` | leer | Dein Arenyze-Key; beim Start im Modus `arenyze` erforderlich. |
| `STATS_API_URL` | leer | Nur für `http`: HTTPS-URL-Vorlage mit `{platform}` und `{username}`; in diesem Modus erforderlich. |
| `STATS_API_KEY` | leer | Nur für `http`: Schlüssel der eigenen Quelle, falls sie einen verlangt. |
| `STATS_API_KEY_HEADER` | `Authorization` | Nur für `http`: Name des Authentifizierungs-Headers. |
| `STATS_API_KEY_PREFIX` | `Bearer ` | Nur für `http`: Präfix einschließlich Leerzeichen. In `.env` als `"Bearer "` angeben; ohne Präfix `""` verwenden. |
| `STATS_PRIVATE` | `false` | `true` zeigt Antworten nur der aufrufenden Person; Discord kann dies je nach Berechtigungen ebenfalls erzwingen. |
| `CACHE_TTL_SECONDS` | `60` | Dauer der Zwischenspeicherung von Statistiken in Sekunden. |
| `API_TIMEOUT_SECONDS` | `15` | Zeitlimit einer Anfrage an die Datenquelle in Sekunden. |
| `API_REQUESTS_PER_MINUTE` | `30` | Lokales Abfragelimit für die Datenquelle; deren eigene Limits gelten zusätzlich. |

Token und API-Schlüssel gehören nur in deine lokale `.env`. Teile diese Datei nicht mit anderen und lade sie nicht in ein öffentliches Repository hoch.

### Optionale eigene Datenquelle aktivieren

1. Eine R6-Datenquelle bereitstellen, die du nutzen darfst und die das [JSON-Datenformat](docs/API_SCHEMA.md) liefert.
2. `DATA_MODE=http` setzen.
3. Die URL in `STATS_API_URL` und gegebenenfalls Schlüssel, Header und Präfix eintragen.
4. Den Bot neu starten und einen bekannten Spieler abfragen.

Dieser zusätzliche Modus ist für Arenyze nicht erforderlich. Eine beliebige API-URL genügt hier nicht: Liefert dein Anbieter ein anderes Datenformat, muss eine Übersetzung in das dokumentierte Format ergänzt werden. Eine passende Quelle kann auch echte Matchlisten liefern.

## Einladungslinks und Prüfungen

`APP_ID` durch die Application ID aus dem Developer Portal ersetzen:

```sh
python bot.py --links APP_ID
```

Die Ausgabe enthält die Links zur Installation zum eigenen Konto und zum Server. Details stehen in der [Discord-Einrichtung](docs/DISCORD_SETUP.md).

Lokale Prüfung ohne Bot-Token, ohne API-Key und ohne Netzwerkzugriff:

```sh
python bot.py --check
```

Tests aus dem Projektordner ausführen:

```sh
python -m unittest discover -s tests -v
```

Die Abhängigkeiten müssen zuvor installiert sein. In der lokalen Umgebung unter Windows statt `python` bei Bedarf `.\.venv\Scripts\python.exe` verwenden; unter Linux/macOS `.venv/bin/python`.

Die Tests prüfen lokale Fixtures und nachgebildete Antworten. Sie ersetzen keinen Test mit deinem Discord-Token oder deinem Arenyze-Key; ein Live-Betrieb wurde mit deinen Zugangsdaten nicht bestätigt.

## Häufige Fragen

**Der Slash-Command fehlt.** Prüfe beide Installationsarten, die globale Registrierung und die Installation zum eigenen Konto. Die vollständigen Schritte stehen in [DISCORD_SETUP.md](docs/DISCORD_SETUP.md). Nach Änderungen den Bot neu starten und Discord gegebenenfalls neu laden.

**Der Command ist nur auf einem Server verfügbar.** Für andere Server und private Chats die Anwendung zusätzlich zum eigenen Konto installieren. Eine reine Server-Installation stellt sie nicht automatisch in allen deinen privaten Chats bereit.

**Die Antwort ist nur für mich sichtbar.** Prüfe `STATS_PRIVATE`. Außerdem können Discord-Berechtigungen bei externen Apps private Antworten erzwingen.

**Mein Tracker-Key funktioniert nicht.** Dieser Bot verwendet standardmäßig einen Arenyze-Key in `ARENYZE_API_KEY`. Die Unterschiede sind in [API_OPTIONS.md](docs/API_OPTIONS.md) erklärt.

**Die letzten fünf Matches fehlen.** Das ist die oben beschriebene Grenze der Arenyze-Anbindung. Der Hinweis im Matchbereich ist beabsichtigt. Eine eigene HTTP-Quelle kann Matchdaten im [Datenformat](docs/API_SCHEMA.md) liefern.

**Der Command eines anderen Bots ist verschwunden.** Dieses Projekt synchronisiert seine globalen Commands vollständig. Verwende eine eigene Discord-Anwendung dafür; bei Wiederverwendung einer Application ID können zuvor registrierte globale Commands ersetzt werden.
