# Geprüfter Stand

13. Oktober 2026, lokales macOS/Python 3.9, Chromium-Browserprüfung. Ausschließlich fiktive Profile und eigene Testdaten.

- 58 automatische Tests bestanden: Profildaten, Auswahl, Entwurfsprüfung, langlebige Jobs, Wiederholungen, Mehrfachklicks, konkurrierende Worker, veraltete Ergebnisse, globale Kontaktsperren, Agentenkonfiguration, API-Adapter und HTTP-Ablauf.
- Browserablauf bestanden: verbinden, eigenen Agenten einrichten, JSON-Datei importieren, Profile sortieren, Entwürfe erstellen, ungültige Bearbeitung ablehnen, freigeben, CSV-Inhalt prüfen, manuellen Versand/Antwort dokumentieren, sperren und trennen.
- Gespeicherte Einstellungen geprüft: mehrere Angebote, mehrere Nischenregeln und Sprachfilter bleiben bei einer reinen Namensänderung erhalten; Sprachwahl lädt die passenden Einstiegsfragen.
- Desktop sowie 390- und 360-Pixel-Ansichten visuell kontrolliert. Kein horizontaler Überlauf; Textmodus auf kleinen Geräten lesbar. Dialoge per Escape schließbar, reduzierte Bewegung geprüft, keine JavaScript-Laufzeitfehler im Test.
- Startskript auf der verifizierten Lexar ausgeführt; Volume-UUID und effektiver Datenpfad werden geprüft. Testdaten, temporäre Verzeichnisse und Screenshots physisch auf Lexar.

Apify/OpenAI wurden mit simulierten HTTP-Antworten getestet. Keine kostenpflichtigen Actor-/KI-Aufrufe, keine echten Profilimporte und keine Instagram-Nachrichten. Docker, öffentlicher Serverbetrieb und ein echtes Mobilgerät wurden nicht getestet. GitHub Actions führt die automatischen Tests nach dem Push separat aus; ein Workflow-File allein ist kein bestätigter erfolgreicher CI-Lauf.
