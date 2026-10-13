# Integrationen und Quellen

## Apify als vorgeschaltete Profilquelle

Diese App importiert ein **bestehendes** Dataset. Datenerhebung, Actor-Auswahl, Suchbegriffe und deren Kosten werden vorher in Apify festgelegt. Das trennt Recherchebudget und Textgenerierung. Der Adapter startet keine bezahlten Runs.

Beim offiziellen Instagram Profile Scraper enthält der Input eine Liste von `usernames`. Sein Output kann unter anderem `username`, `fullName`, `biography`, `followersCount`, `isPrivate`, `externalUrl` und `latestPosts` enthalten. Felder sind teilweise optional. Der Normalisierer unterstützt diese Namen und ein eigenes schlankes Format:

```json
{
  "username": "demo_profil",
  "full_name": "Fiktiver Beispielname",
  "bio": "Fotografie mit natürlichem Licht",
  "followers": 1800,
  "is_private": false,
  "language": "de",
  "posts": [{"text": "Portraits bei Tageslicht", "url": "https://www.instagram.com/p/DEMO/"}]
}
```

Unbekannte Followerzahlen bleiben unbekannt. Namen werden nicht aus Handles erfunden. Nicht-Profildaten oder Fehlerzeilen führen zur Ablehnung des gesamten Imports mit Zeilennummern. Korrigiere solche Zeilen und importiere erneut; es gibt keinen stillen Teilimport.

Der Dataset-Adapter verwendet `GET /v2/datasets/{id}/items`, Bearer-Header, `skipHidden=true`, `skipEmpty=false` und explizite begrenzte Pagination. Eine kurze Seite allein beendet den Import nicht. Ein Request läuft höchstens 45 Sekunden; jede Antwort ist größenbegrenzt. Die Oberfläche begrenzt einen Import auf 1.000 Dataset-Einträge. Bei einem HTTP-Fehler bleibt der lokale Bestand unverändert; ein erneuter Import kann bewusst wiederholt werden.

Primärquellen, geprüft am 13.10.2026:

- [Apify Dataset-API](https://docs.apify.com/api/v2/dataset-items-get)
- [Profilactor: Eingabe](https://apify.com/apify/instagram-profile-scraper/input-schema)
- [Profilactor: Ausgabe](https://apify.com/apify/instagram-profile-scraper/output-schema)

Der vorher untersuchte REA-/Content-Analyse-Workflow liefert hilfreiche Integrationsmuster. Dieses Repo verwendet eine neue, kleine standardisierte Importschnittstelle; es enthält keinen angeblich bereits existierenden automatischen Apify-DM-Sender und kopiert keinen fremden Reverse-Engineering-Code.

## OpenAI als optionale Textgenerierung

Aufruf über die Responses API an `https://api.openai.com/v1/responses`, Modell ausdrücklich über `OPENAI_MODEL`. Das Ergebnis muss ein strukturiertes JSON-Objekt mit genau drei Varianten sein. Der Prompt trennt die verbindlichen Aufgabenregeln von importierten Profil-/Agentendaten. Das Modell erhält keine Tools und ruft keine Links ab. `store=false` ist gesetzt; dies ersetzt keine eigene Prüfung der geltenden Anbieter-Datenverarbeitung.

Jede Variante verweist auf vorhandene Beleg-IDs und zitiert einen passenden kurzen Ausschnitt exakt. Danach laufen lokale Format-, Beleg- und Inhaltschecks. Diese Prüfungen sind keine vollständige semantische Verifikation; jede Nachricht braucht menschliche Prüfung. Prompt-Injection-Muster, unbekannte IDs, erfundene Zitate und bestimmte Druck-/Pitchmuster werden abgefangen. Frei formulierte Halluzinationen außerhalb dieser Regeln können dennoch auftreten.

Vor dem produktiven Einsatz mit wenigen eigenen Datensätzen testen. API-Modell, Limits und Budget sind Einstellungen des Betreibers, kein impliziter Bestandteil eines ChatGPT-Abos.

Primärquelle: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Instagram

Metas offizielle Send API verlangt eine vorherige Nachricht des Instagram-Nutzers an das professionelle Konto. Daher kann ein importierter Handle nicht direkt als beliebiger kalter DM-Empfänger verwendet werden. Private Antworten auf Kommentare sind ein eigener eingeschränkter Ablauf; `HUMAN_AGENT` ist für menschlichen Support und kein Weg für automatisierte kalte Kontaktaufnahme.

Quelle: [Metas offizielle Instagram-Postman-Sammlung im fbsamples-Repository](https://github.com/fbsamples/messenger-platform-samples/blob/main/postman/instagram-platform-api.postman_collection.json).

Die Anwendung beinhaltet weder private Instagram-Endpunkte, Cookie-Importe, Browser-Versandbots noch Account-Rotation. Eine spätere offizielle Inbox-Integration benötigt unter anderem Account-Verbindung, passende Berechtigungen, Webhook-Prüfung und Prüfung des jeweiligen Nachrichtenfensters. Sie ist nicht Teil dieser Version.
