# Betrieb und Übergabe

## Pro Person

1. Repository in den gewünschten zulässigen Speicher klonen.
2. Python 3.9+ bereitstellen; keine Paketinstallation nötig.
3. Eigenes absolutes `OUTREACH_DATA_DIR` verwenden. Daten nie zwischen Kunden vermischen.
4. Lokal mit `./start.sh serve` starten, Zugangsschlüssel eingeben und den Agenten einrichten.
5. Mit den fiktiven Beispielen importieren → generieren → prüfen → freigeben testen. Demo-Nachrichten nicht an die synthetischen Handles senden.
6. Erst danach eigene, zulässig erhobene Profildaten importieren und jede Nachricht inhaltlich prüfen.

Eine unabhängige zweite Instanz lässt sich beispielsweise mit anderem Datenverzeichnis und Port starten. Beide Pfade müssen nach den eigenen Speichervorgaben zulässig sein:

```sh
OUTREACH_DATA_DIR=/absoluter/pfad/zweite-person ./start.sh serve --port 8788
```

## Server

Dieses Repository deployt oder bezahlt keinen Server. Auf einem eigenen Linux-Server lässt sich der gleiche Python-Prozess über systemd oder einen bestehenden Prozessmanager betreiben. Die Anwendung ist für eine Person pro Instanz gedacht; der Zugangsschlüssel entspricht Vollzugriff auf deren Bestand.

- Eigener unprivilegierter Betriebssystemnutzer und eigenes Datenverzeichnis pro Instanz.
- `OUTREACH_ACCESS_TOKEN` als ausreichend zufälligen geheimen Wert setzen (mindestens 32 Zeichen); mit dem vorgesehenen Nutzer außerhalb des Repos teilen.
- Anwendung an `127.0.0.1:8787` binden. Für Fernzugriff einen HTTPS-Reverse-Proxy vorschalten, der den ursprünglichen `Host` erhält. Das Backend nicht ohne TLS direkt ins Internet öffnen.
- Zugangsdaten über die Umgebung des Dienstes bereitstellen; niemals in Git oder im Browser-Quelltext.
- Für neue Softwareversionen Tests ausführen, Daten sichern, Dienst stoppen, Code aktualisieren und neu starten.
- Prozess-/Disk-Monitoring und Backups für den jeweiligen Server separat einrichten. `/health` prüft nur, ob HTTP erreichbar ist; fehlgeschlagene Jobs stehen im Aufgabenstatus.

Optionales Dockerfile ist für einen Linux-Server beigefügt. Ein explizit gemountetes Datenverzeichnis ist zwingend. Docker wurde für diese Lieferung nicht auf dem Mac gestartet; sein eigener Speicher muss zur Umgebung passen.

Linux-Beispiel (im eigenen Serverprojekt, nicht ungeprüft auf diesem Mac):

```sh
docker build -t outreach-studio .
sudo install -d -o 10001 -g 10001 /srv/outreach/person-a/tmp
# OUTREACH_ACCESS_TOKEN vorher als geheimen zufälligen Wert setzen.
docker run --rm --name outreach-person-a \
  -p 127.0.0.1:8787:8787 \
  -v /srv/outreach/person-a:/data \
  -e OUTREACH_ACCESS_TOKEN \
  outreach-studio
```

Der Host-Port bleibt auf Loopback. Bei einem Bind-Mount überschreibt der Hostordner die Bildberechtigungen; UID `10001` benötigt dort Schreibrechte. Einen Prozessmanager beziehungsweise eine Neustartrichtlinie für dauerhaften Betrieb ergänzen.

## Datensicherung und Aufbewahrung

Am einfachsten bei gestopptem Dienst `agent.json` und `outreach.sqlite3` zusammen auf ein erlaubtes Sicherungsziel kopieren. Für eine laufende Datenbank die SQLite-Backup-API nutzen, nicht die offene Datei blind kopieren. Rohdateien niemals in das Git-Repository aufnehmen.

Zum Wiederherstellen denselben Softwarestand mit dem gesicherten Datenverzeichnis starten und an einem unkritischen Beispiel prüfen. Die SQLite-Schemaerweiterungen dieser MVP-Version sind additiv; größere spätere Migrationen benötigen einen eigenen Plan.

Diese Version enthält keine automatische Löschfrist und keine Einzeldaten-Löschoberfläche. Der Betreiber legt Aufbewahrung, Zugriffsberechtigte und Löschprozess fest. Eine Sperre verhindert Erstkontakte; sie ist keine Datenlöschung. Importdateien und CSV-Exporte werden durch die bedienende Person verwaltet.

## Wenn etwas nicht funktioniert

| Beobachtung | Nächster Schritt |
|---|---|
| Verbindung abgelehnt | Server läuft? Richtiger Port und aktueller Zugangsschlüssel? |
| Keine geeigneten Profile | Passungsgründe prüfen; Begriffe oder Mindestwerte anpassen |
| Keine Varianten | Genügend verwertbarer öffentlicher Text vorhanden? Privates Profil? |
| KI-Modus fehlt | Beide Variablen `OPENAI_API_KEY` und `OPENAI_MODEL` im Serverprozess setzen |
| KI-Auftrag fehlgeschlagen | Modell-/Schema-Unterstützung, API-Zugang und Jobfehler prüfen; kein stiller Vorlagenersatz |
| Dataset wird abgelehnt | Richtiges Profil-Dataset statt Beitrags- oder Fehlermeldungs-Export? Fehlerzeilen korrigieren |
| Änderungen an Vorlage wirkungslos | Gespeicherte `agent.json` hat Vorrang; Agent über Oberfläche bearbeiten |
| Lexar-Prüfung scheitert | Laufwerk prüfen; keinen Ersatzordner auf der internen SSD anlegen |

Eine normale Antwort ist noch kein Verkaufsinteresse. Antwortquote, qualifizierte Gespräche und daraus entstandene Termine müssen mit echten Ergebnissen gemessen werden. Der interne Passungswert ist nur eine Priorisierungshilfe.
