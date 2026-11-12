# Personenverfolgung

## Bedienung

1. Den aktualisierten ESP32-Sketch hochladen und die aktualisierte GUI starten.
   Beide gehören zusammen: Die GUI erneuert Fahrbefehle regelmäßig, der ESP32
   stoppt bei ausbleibenden Befehlen.
2. Kamera-IP eintragen und „Start Tracking“ drücken. Das startet Kamera und
   Personenerkennung; das Fahrzeug fährt dadurch noch nicht automatisch.
3. Im Kamerabild auf den grünen Rahmen der gewünschten Person klicken.
   Das Ziel wird nach dem nächsten frischen Bild gelb mit „SELECTED“ markiert.
   Die Auswahl berücksichtigt die Skalierung und die Ränder des Kamerabildes.
4. „Auto Follow“ drücken. Die automatische Fahrt folgt der angeklickten Person.
   Ohne Auswahl bleibt das Fahrzeug stehen. Space beziehungsweise „Full Stop“
   beendet die automatische Fahrt. Manuelle Fahrbefehle übernehmen ebenfalls
   die Kontrolle.
5. Für eine andere Zielperson deren Rahmen anklicken. Beim Wechsel stoppt das
   Fahrzeug zunächst und wartet auf ein frisches Bild für die neue Auswahl.
   Geht die gewählte Person verloren oder ist die Zuordnung uneindeutig,
   stoppt es und die gewünschte Person muss erneut angeklickt werden.
6. „Follow Person“ führt eine auf 0,5 Sekunden begrenzte Folgebewegung aus.
   Dazu muss die ausgewählte Person aktuell erkannt sein.

Die manuellen 20er-Schritte und die zuletzt am Fahrzeug korrigierte
Seiten-/Drehrichtungszuordnung bleiben erhalten.

## Vom Kamerabild zum Fahrbefehl

Die Kamera liefert JPEG-Einzelbilder über `http://<Kamera-IP>/capture`. Ein
Hintergrundthread lädt und dekodiert das Bild. Das lokale Modell `yolo11n.pt`
erkennt ausschließlich die Klasse `person`, mit mindestens 0,5 Konfidenz.
Das ist die Bewertungsgrenze des Modells, keine garantierte Trefferquote.

Alle erkannten Personen werden grün eingerahmt. Die Zielwahl erfolgt ausschließlich
durch einen Klick auf einen dieser Rahmen. Klicks außerhalb der Rahmen oder auf
ein veraltetes Bild ändern die Auswahl nicht. Bei überlappenden Rahmen wird der
kleinere Rahmen unter dem Klick ausgewählt.

In aufeinanderfolgenden Bildern wird die ausgewählte Person anhand der bisherigen
Rahmenposition und Größe zugeordnet. Bei mehreren ähnlich gut passenden Kandidaten
wird kein Fahrziel ausgegeben. Die bisherige Speicherung und der Vergleich von
Kleidungsfarben wurden entfernt; es wird kein Personenprofil angelegt.

Bei fehlender oder uneindeutiger Erkennung stoppt das Fahrzeug und verwirft die
Auswahl. Für die weitere Verfolgung ist ein erneuter Klick nötig. Ein Wechsel von
Auto-Follow behält eine vorhandene Auswahl bei; ein Tracking- oder Kameraneustart
löscht sie. Änderungen der Bildauflösung skalieren die ausgewählte Position mit.

Die Zuordnung anhand von Position und Größe ist keine Identitätserkennung.
Insbesondere beim Kreuzen oder Überdecken von Personen kann eine falsche
Zuordnung auftreten.

Der Regler berechnet die seitliche Abweichung relativ zur halben Bildbreite.
Im mittleren Bereich lenkt er nicht. Außerhalb wächst die Lenkkorrektur stetig
mit der Abweichung: Person rechts bedeutet links schneller als rechts, Person
links entsprechend umgekehrt. Bei großen Abweichungen wird die Vorwärtsfahrt
reduziert. Auto-Follow verwendet ausschließlich nichtnegative Geschwindigkeiten,
fährt also nicht selbstständig rückwärts. Die Sollwerte werden sanft angefahren;
Zielverlust und Näherstopp setzen beide Seiten sofort auf null.

Die Rahmenhöhe geteilt durch die Bildhöhe dient als grobe Näheabschätzung.
Ab 60 Prozent Bildhöhe nimmt die Vorwärtsgeschwindigkeit ab. Bei 90 Prozent
stoppen beide Seiten. Nach diesem Stopp fährt das Fahrzeug erst wieder an, wenn
die Rahmenhöhe unter 80 Prozent fällt. Dieser Unterschied verhindert häufiges
Ein-/Ausschalten direkt an der Stoppgrenze. Die Größe hängt auch von Körpergröße,
Kamerawinkel und Verdeckung ab: Daraus ergibt sich kein Abstand in Metern.

Die GUI sendet die Werte als `links rechts\n` über TCP an den ESP32. Dieser
steuert die am Fahrzeug ermittelten Seiten an:

| Seite | MP6508-Brücke | GPIOs | Invertierung |
| --- | --- | --- | --- |
| Links | A | 19 = AIN1, 18 = AIN2 | `INVERT_LEFT = true` |
| Rechts | B | 17 = BIN1, 16 = BIN2 | `INVERT_RIGHT = false` |

## Stillstand bei fehlenden Daten

- Fehlt die gewählte Person in einem gültigen Bild, stoppt die Folgefahrt sofort.
  Auto-Follow bleibt aktiv und kann mit einem gültigen Ziel wieder anfahren.
- Bilder, deren Anforderung mehr als eine Sekunde zurückliegt, dürfen keine
  Bewegung auslösen. Das Alter schließt HTTP-Aufruf und YOLO-Berechnung ein.
- Bleiben neue Bilder aus, setzt die GUI den Folgefahrbefehl nach Ablauf dieser
  Frist auf null. Kamerafehler schalten die automatische Fahrt aus.
- Die GUI erneuert den aktuellen Fahrbefehl ungefähr alle 200 ms. Das gilt auch
  für manuelle Fahrt, damit deren bisheriges Verhalten erhalten bleibt.
- Empfängt der ESP32 1200 ms lang keinen vollständigen gültigen Befehl, setzt er
  die Motorausgänge auf null, auch wenn TCP die Verbindung noch als aktiv meldet.
  Bei wiederkehrender Verbindung startet die GUI mit null statt dem alten Befehl.

Diese Mechanismen erfassen fehlende oder verspätete Daten. Ein Kameraserver, der
ständig dasselbe alte JPEG als neue Antwort liefert, wird damit nicht erkannt.
Außerdem gibt es keinen Hindernissensor und keine Wegplanung. Reale Anlaufwerte,
Bremsweg und Kameralatenz müssen am Fahrzeug geprüft werden.

## Einstellungen im Python-Konstruktor

| Variable | Standard | Wirkung |
| --- | ---: | --- |
| `detection_confidence` | 0.5 | Mindestbewertung der Personenerkennung |
| `tracking_threshold` | 50 | Halbe Lenk-Totzone bei 640 Pixel Bildbreite; skaliert mit der Auflösung |
| `follow_speed` | 220 | Maximale Vorwärtskomponente bei entferntem, mittigem Ziel |
| `follow_turn_gain` | 90 | Stärke der seitlichen Lenkkorrektur |
| `follow_max_pwm` | 255 | Obergrenze je Motorseite in Auto-Follow |
| `follow_acceleration` | 750 | Maximale Änderung je Sekunde, in PWM-Einheiten |
| `slow_person_height_ratio` | 0.60 | Ab dieser Rahmenhöhe langsamer annähern |
| `stop_person_height_ratio` | 0.90 | Ab dieser Rahmenhöhe vollständig stoppen |
| `resume_person_height_ratio` | 0.80 | Nach einem Näherstopp darunter wieder anfahren |
| `frame_timeout` | 1.0 s | Maximal zulässiges Alter vom Start der Bildanforderung an |
| `target_match_margin` | 0.15 | Mindestabstand der Zuordnungsbewertungen; bei ähnlichen Kandidaten stoppen und neu auswählen |

Die Nähewerte müssen `0 < slow < resume < stop <= 1` erfüllen.
Falls HTTP und Erkennung dauerhaft länger als `frame_timeout` benötigen, bleibt
das Fahrzeug absichtlich stehen. Dann zunächst die Latenz messen und Bildgröße,
Rechnerleistung oder Kameraverbindung prüfen. Neue Werte benötigen einen
Neustart der GUI. `COMMAND_TIMEOUT_MS` wird im ESP32-Sketch eingestellt.

## Prüfung

`python -m unittest discover -s tests -v` prüft die Klickauswahl samt Bildskalierung,
Zielwechsel, ungültige Klicks, Zielverlust, Personenzuordnung, Lenkung,
Auflösungsskalierung, Geschwindigkeitsrampe, Näherstopp, Zielverlust, alte Bilder,
manuelle Übernahme, regelmäßige Fahrbefehle und den ESP32-Timeout. GPIO/PWM werden
für Arduino-ESP32 Core 2 und 3 simuliert. Das ersetzt keinen Fahrversuch.

YOLO-Schnittstelle: [Ultralytics Predict-Dokumentation](https://docs.ultralytics.com/modes/predict/).
