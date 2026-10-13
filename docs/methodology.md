# Erst verstehen, dann gemeinsam weitergehen

Dieses Projekt erzeugt überprüfbare Entwürfe für einen persönlichen Erstkontakt. Es behauptet weder Kaufinteresse noch einen konkreten Bedarf. Ein hoher Score bedeutet nur, dass ein Profil zu konfigurierten Kriterien passt und genügend Text für einen nachvollziehbaren Einstieg vorhanden ist.

Der Einstieg bleibt klein: eine überprüfbare öffentliche Angabe und eine offene, leicht beantwortbare Frage. Eine Antwort eröffnet den nächsten Gesprächsschritt. Erst später können die tatsächlichen Ziele, die persönliche Bedeutung, bisherige Versuche und ein vom Gegenüber selbst beschriebenes Hindernis Thema werden. Eine Lösung oder ein Telefonat braucht einen passenden Anlass. Eine feste Reihenfolge automatischer Nachfragen folgt daraus nicht.

Die Regeln sind eine eigene, allgemeine Adaption des Workflow-Eigentümers. Öffentliche Impulse sind [diagnostisches Verkaufen](https://www.youtube.com/watch?v=1UhvBSQFy6A&t=30) und [Antwort aufgreifen und passend weiterfragen](https://www.youtube.com/watch?v=w7g08dVTwaE&t=120). Die Vorlagen sind keine Originalzitate oder nachgebauten internen Systeme eines Trainers. Private Mentorunterlagen, reale Gesprächsverläufe, Namen und Fallbeispiele sind nicht Bestandteil dieses Repositories.

## Was in die erste Nachricht gehört

- Eine konkrete Angabe aus einer öffentlichen Bio oder einer vorhandenen Post-Beschreibung. Das System kann bestätigen, dass der importierte Text diese Angabe enthält; die Wahrheit einer Selbstaussage prüft es nicht unabhängig.
- Ein kurzer, natürlicher Einstieg. Ein Anzeigename wird nur unverändert aus `full_name` verwendet, sofern er wie ein kurzer Name formatiert ist. Aus dem Handle wird kein Name konstruiert. Auch ein Anzeigename muss vor Versand geprüft werden.
- Genau eine offene Frage. Keine erfundene Begeisterung, persönliche Erfahrung, Betrachtung eines Videos, Umsatzaussage, Problemdiagnose oder Käuferrolle.
- Eine spätere ehrliche Antwort auf Fragen zur eigenen Arbeit und Kontaktabsicht. Angebote in der Konfiguration werden nicht heimlich in den Erstkontakt eingebaut.

Die gespeicherten Angebote und Absenderangaben sind Kontext für die eigene Arbeit. Vor Nutzung eigene wahre Angaben ergänzen. Die Beispiele verwenden vollständig erfundene Profile und `example.com`; sie sind keine Leads und dürfen nicht angeschrieben werden.

## Belege und Sprache

`normalize_profile` akzeptiert eigene JSON-Daten und übliche Apify-Schlüssel wie `fullName`, `biography`, `followersCount`, `latestPosts` und `caption`. Fehlende Followerzahlen bleiben `null`. Eine veröffentlichte URL ist Metadatum und wird vom Nachrichtenmodul nicht abgerufen. Begrenzte Importmetadaten werden in `_source` erhalten. Zugangsdaten gehören nicht in Profildaten.

Das Modul bildet kurze, wörtliche Ausschnitte aus normalisiertem Bio- oder Post-Text. Die Beleg-ID hängt von Text, Quellen-URL und Belegtyp ab. Die vollständigen importierten Texte bleiben im Profil; der Beleg zeigt einen Ausschnitt mit höchstens 100 Zeichen. Aussagen werden nicht aus Bildern oder Videos abgeleitet. Quellen, deren Text wie eine Anweisung an ein Modell aussieht, werden nicht als Nachrichteneinstieg verwendet. Diese Erkennung ist eine zusätzliche Heuristik; Profiltext bleibt grundsätzlich untrusted data, auch wenn kein Muster gefunden wird.

Vorlagen unterstützen Deutsch und Englisch. Eine explizite Profilsprache steuert die Ausgabe. Fehlt sie, wird die Kampagnensprache als **unbestätigte Vorgabe zur Prüfung** verwendet und dies in der Analyse vermerkt. Das System behauptet keine automatische Spracherkennung. Eine vorhandene Sprache außerhalb der Kampagnenauswahl blockiert den Entwurf.

`validate_draft` fordert mindestens ein wörtliches Zitat eines angeführten, verwendbaren Belegs. Alle Zitate müssen zu ihren Beleg-IDs passen; zusätzlich gelten Längen-, Fragen- und Musterprüfungen. Das schützt vor erfundenen Belegzitaten und vielen bekannten Fehlmustern. Es ist keine vollständige semantische Wahrheitsprüfung. Jede Freitextaussage, Namensansprache, Aktualität des Profils und Passung der Frage muss ein Mensch vor Verwendung prüfen.

## Kampagnen und Priorisierung

Die JSON-Vorlagen `coach.json`, `local-services.json` und `general.json` zeigen die Anpassung an verschiedene Themen. Einschlussbegriffe werden als ganze Wörter beziehungsweise Phrasen gesucht; Ausschlussbegriffe haben Vorrang. Die Herkunft der Begriffe sind Bio- und Post-Texte, keine abgeleiteten sensiblen Merkmale. `hook_rules` werden der Reihe nach geprüft und greifen nur, wenn ihr Begriff im **gewählten Beleg** vorkommt. Jede Regel enthält drei verschiedene offene Fragen je unterstützter Sprache. Das sind bearbeitbare Startpunkte, keine Wirksamkeitsgarantie für jede Nische.

Der Score ist transparent: öffentliches Profil 10 Punkte, passende Suchbegriffe bis zu 30 Punkte (ohne Einschlussfilter 15), verwendbare Bio 20, verwendbarer Post 20, Website-URL 5, Followerzahl im Bereich 5, angegebene unterstützte Sprache 5. Bei 100 wird gedeckelt. Ein gesetzter Followerfilter blockiert unbekannte Zahlen. Privatprofile, fehlende verwendbare Belege, Ausschlussbegriffe, fehlende Zielbegriffe, ungeeignete Sprache und ein unterschrittener Mindestscore blockieren unabhängig von anderen Punkten.

`template-v1` ist eine deterministische Vorlage ohne KI-Modellaufruf. Drei Varianten unterscheiden sich durch ihre offene Frage. Das lokale Modul nimmt keine Netzwerkverbindung auf und versendet keine Nachrichten. Ein externer Textanbieter muss gesondert konfiguriert werden und dieselben Prüfungen durchlaufen. Bessere Formulierungen werden anhand tatsächlicher, freiwilliger Antworten bewertet; Antwort-, Kauf- oder Umsatzraten werden nicht vorausgesagt.
