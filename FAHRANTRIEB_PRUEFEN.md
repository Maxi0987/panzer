# Getrennte Ansteuerung der Fahrzeugseiten prüfen

## Analyse vom 05.11.2026

### Aktueller Stand nach dem Fahrzeugtest

Nach der Korrektur von BIN1/BIN2 reagieren beide Motorgruppen, jedoch vertauscht:
Right bewegt links, Left bewegt rechts. Damit ergibt sich für dieses Fahrzeug
**Brücke A = links, Brücke B = rechts**. Die Motorgruppen wurden jetzt im Sketch
entsprechend zugeordnet: links GPIO 19/18, rechts GPIO 17/16. Die elektrischen
Verbindungen GPIO 19 = AIN1, 18 = AIN2, 17 = BIN1, 16 = BIN2 bleiben erhalten.
Nach dem nächsten Fahrzeugtest war nur die rechte Fahrtrichtung noch verkehrt.
Deshalb gilt jetzt `INVERT_LEFT = true`, `INVERT_RIGHT = false`.
Diese Korrektur wirkt auch auf die Personenverfolgung.

Die GUI sendet weiterhin `links rechts\n`. Die Korrektur im Sketch gilt dadurch
für Einzelsteuerung, gemeinsame Fahrt, Drehmanöver und Personenverfolgung.
Firmwaretests prüfen die neue physische Zuordnung auch für die Lenkbefehle
`50 150`, `100 100` und `150 50` auf beiden unterstützten Core-Versionen.
Nach erneutem Upload zuerst Left +/- und Right +/- prüfen, danach Auto-Follow.

### Vorheriger Diagnosestand

Im ursprünglich angehängten Sketch waren die linken Richtungspins gegenüber der
vom Nutzer angegebenen Verdrahtung vertauscht. Der Projekt-Sketch wurde korrigiert:
GPIO 19 = AIN1, GPIO 18 = AIN2, GPIO 17 = BIN1, GPIO 16 = BIN2.
Auch die serielle Startmeldung und die Firmwaretests verwenden diese Zuordnung.
Der korrigierte Sketch muss erneut auf den ESP32 hochgeladen werden.
Die Richtungsinvertierung `INVERT_LEFT`/`INVERT_RIGHT` bleibt zunächst erhalten;
die tatsächliche Vorwärtsrichtung muss am Fahrzeug geprüft werden.

Ein Vertauschen von BIN1/BIN2 ändert nur die Drehrichtung der linken Brücke.
Es erklärt allein weder die bisher komplett ausbleibende linke Bewegung noch das
Mitlaufen beider Seiten auf Right +/-. Bleibt dieses Verhalten nach dem Upload
bestehen, müssen die realen GPIO-Signale und die Motor-Ausgangspaare geprüft werden.

Die GUI sendet
`links rechts\n`; der ESP32 verarbeitet genau diese beiden Einzelgeschwindigkeiten.
Im damaligen Sketch verwendete links GPIO 16/17, rechts GPIO 19/18.
In diesem Code lässt sich weder
ein Vermischen der beiden Geschwindigkeiten noch ein gemeinsamer PWM-Kanal
nachweisen. Bestätigte Hardware: ESP32 mit MP6508; die tatsächliche Verbindung
zwischen GPIOs und Treibereingängen wurde inzwischen vom Nutzer angegeben
(siehe Tabelle weiter unten); am Fahrzeug nachgemessen wurde sie nicht.
Arduino-ESP32 Core 3 verwendet bei `ledcWrite` eine GPIO-Nummer,
Core 2 eine Kanalnummer; der vorhandene Sketch berücksichtigt das bereits.

Ein bestätigter GUI-Fehler war: Nach unterschiedlich schnellen Einzelbefehlen
erhöhten/verringerten die Tasten „Both Forward/Reverse“ beide Werte separat.
Der Unterschied blieb deshalb erhalten. Jetzt setzen diese Tasten beide Seiten
auf denselben Sollwert: vorwärts höherer bisheriger Wert +20, rückwärts
niedrigerer bisheriger Wert -20, jeweils begrenzt auf -255 bis 255. Die bisherigen
20er-Schritte und die schrittweise Richtungsänderung bleiben bestehen.
Gleiche Sollwerte garantieren keine exakt gleiche mechanische Drehzahl.

Das erklärt **nicht** den gesamten beschriebenen Fehler „rechts bewegt alles,
links nichts“. Die Fahrbefehle waren bereits unabhängig. Ohne die tatsächliche
Verdrahtung und einen Test am Fahrzeug wäre eine Änderung der GPIO-Zuordnung
geraten.

### Neue Einzeltests in der GUI

Frühere Rückmeldung vom Fahrzeug vor den Zuordnungskorrekturen:

- Right +: alle Räder vorwärts.
- Right -: alle Räder rückwärts.
- Left + und Left -: keine Bewegung.

Damit ist das unerwünschte Mitlaufen auch bei den absoluten Einzelbefehlen
vorhanden und nicht nur bei zuvor gespeicherten Geschwindigkeiten. Eine plausible
Ursache ist, dass beide Motorgruppen am selben Brückenausgang hängen oder beide
Brücken dieselben rechten Eingangssignale erhalten. Das ist noch kein Beweis:
Die empfangenen Befehle und die realen GPIO-Signale wurden nicht gemessen.
Ein Ausfall allein der linken GPIOs erklärt das Mitlaufen beider Seiten beim
rechten Befehl nicht vollständig.

Als nächstes im seriellen Monitor bei 9600 Baud die Ausgabe für Left +
(`Drive LEFT=120 RIGHT=0`) und Right + (`Drive LEFT=0 RIGHT=120`) prüfen.
Danach bei ausgeschalteter Versorgung die vier Verbindungen vom ESP32 zu den
MP6508-Eingängen und die beiden Ausgangspaare zu den Motorgruppen nachverfolgen.
Die inzwischen angegebene linke GPIO-Zuordnung ist im Sketch korrigiert. Falls
das Verhalten nach dem Upload bestehen bleibt, helfen ein Foto mit lesbaren
Anschlussbezeichnungen oder ein Schaltplan bei der weiteren Prüfung. Bis die
Motorgruppen unabhängig reagieren, kann Auto-Follow nicht korrekt lenken.

Die Tasten im Bereich „Motor test“ setzen absolute Werte, unabhängig von den
vorherigen Fahrbefehlen. Fahrzeug für diesen Test so aufstellen, dass die Räder
frei drehen können. Jeder Test schaltet Auto-Follow aus und endet nach einer
Sekunde mit `0 0`. Full Stop und neue Fahrbefehle heben den alten Test-Timer auf.

| Taste | TCP-Befehl | Erwartete Bewegung |
| --- | --- | --- |
| Left + | `120 0` | Nur links vorwärts |
| Left - | `-120 0` | Nur links rückwärts |
| Right + | `0 120` | Nur rechts vorwärts |
| Right - | `0 -120` | Nur rechts rückwärts |

Im Python-Terminal steht `Sent: ...`, im ESP32-Monitor `Drive LEFT=... RIGHT=...`.
Beide Ausgaben vergleichen. Wenn die Befehle übereinstimmen, anschließend die
GPIO-Signale anhand der Tabelle weiter unten prüfen. PWM 120 ist ein Testwert,
keine garantierte Anlaufschwelle. Die Abschaltung nach einer Sekunde erfolgt
durch die GUI und setzt eine funktionierende Verbindung voraus.

### Personenverfolgung

Die Verfolgung wurde inzwischen auf proportionale Lenkung mit geregelter
Annäherung, Geschwindigkeitsrampe und Erkennung veralteter Bilder umgestellt.
Die aktuelle Funktion und Einstellungen stehen in
[PERSONENVERFOLGUNG.md](PERSONENVERFOLGUNG.md). Die bisherigen festen Werte
`50 150`, `100 100`, `150 50` sind weiterhin Beispiele für gültige Fahrbefehle,
jedoch keine festen Reglerausgaben mehr. Der ESP32 benötigt jetzt mindestens
alle 1200 ms einen gültigen Befehl; die aktualisierte GUI erneuert ihn etwa alle
200 ms. Daher GUI und Sketch gemeinsam aktualisieren.

## Bereits vorhandener Firmwarestand

Der Sketch in `drivy_socket_client_customSteuerung` wurde auf vier explizite
LEDC-PWM-Kanäle umgestellt. Er unterstützt Arduino-ESP32 Core 2.x und 3.x;
eine vorhandene Projektnotiz nennt eine frühere Kompilierung mit Core 3.2.0
für `DOIT ESP32 DEVKIT V1`. Bei der aktuellen Prüfung wurde der Sketch mit
simulierten Core-2-/Core-3-Schnittstellen kompiliert und ausgeführt; eine erneute
Board-Kompilierung oder ein Upload wurde nicht durchgeführt.
Ausgeschaltete LEDs verwenden keine PWM-Kanäle. Bei einer erkannten
Verbindungstrennung oder einem ungültigen Fahrbefehl werden die Motorausgänge
auf null gesetzt. Nach einer Trennung versucht der ESP32 erneut zu verbinden.

Die tatsächliche Ursache am Fahrzeug ist damit noch nicht bewiesen. Der Nutzer
hat den MP6508 inzwischen bestätigt und die Eingangspinzuordnung angegeben;
die Verdrahtung der Motorausgänge ist noch ungeprüft.
Der bisherige Code enthält bereits getrennte linke und rechte Sollwerte;
`analogWrite()` allein belegt keinen Fehler. Die expliziten Kanäle machen die
Zuordnung überprüfbar und vermeiden Abhängigkeiten von automatischer Zuweisung.

## Erwartete Verbindung beim bestätigten MP6508

Die folgende Zuordnung berücksichtigt den Fahrzeugtest: **Brücke A = links,
Brücke B = rechts**. Entscheidend sind zusammengehörige Ein- und Ausgangspaare.

| ESP32 | Funktion | MP6508-Eingang bei dieser Zuordnung |
| --- | --- | --- |
| GPIO 19 | Links, Richtung 1, PWM-Kanal 2 | AIN1 |
| GPIO 18 | Links, Richtung 2, PWM-Kanal 3 | AIN2 |
| GPIO 17 | Rechts, Richtung 1, PWM-Kanal 0 | BIN1 |
| GPIO 16 | Rechts, Richtung 2, PWM-Kanal 1 | BIN2 |
| GPIO 4 | Treiber aktivieren | nSLEEP |
| GND | Gemeinsamer Bezug | Treiber-GND |

Die linke Motorgruppe gehört an **AOUT1 und AOUT2**, die rechte an **BOUT1 und
BOUT2**. Motorleitungen verschiedener Brücken dürfen nicht zu einem Motorpaar
zusammengefasst sein. Eingänge und Ausgänge immer anhand der tatsächlichen
Beschriftung bzw. des Schaltplans zuordnen, nicht anhand der räumlichen Reihenfolge
der Klemmen. Zum Nachverfolgen der Leitungen die Versorgung ausschalten.

Das gemeldete Verhalten „links bewegt nichts, rechts bewegt beide Seiten,
bei gemeinsamer Fahrt läuft nur rechts“ gibt Anlass, zuerst die Paarung der
Ein- und Ausgänge zu prüfen. Das ist eine Diagnosehypothese. Auch
Versorgungseinbrüche oder die Strombegrenzung des Treibers kommen infrage.

## Test am Fahrzeug

1. Sketch in der Arduino-IDE öffnen, `DOIT ESP32 DEVKIT V1` wählen und neu
   hochladen. Den seriellen Monitor auf **9600 Baud** stellen.
2. GUI starten, Tracking/Auto-Follow ausgeschaltet lassen und **Full Stop** drücken.
3. Rechts auf Vorwärts klicken, bis rechts ungefähr 120 angezeigt wird.
   Links muss 0 bleiben. Bei kleinen PWM-Werten wie 20 kann ein Motor noch stehen.
4. Links schrittweise auf denselben Wert erhöhen. Rechts muss in der GUI und im
   seriellen Monitor unverändert bleiben; bei gleichen positiven Werten sollen
   beide Seiten fahren.
5. Links stoppen: Rechts soll unverändert weiterlaufen. Anschließend beide stoppen
   und den Test mit vertauschten Seiten wiederholen.

Die Befehle laufen **von der GUI über TCP** zum ESP32. Der serielle Monitor dient
zur Ausgabe und ist kein zusätzlicher Fahrbefehl-Eingang.

Beispiele für die Ausgabe:

```text
Drive LEFT=0 RIGHT=120
Drive LEFT=20 RIGHT=120
Drive LEFT=40 RIGHT=120
Drive LEFT=120 RIGHT=120
```

Mit `INVERT_LEFT = true` und `INVERT_RIGHT = false` erwartet der Code folgende
PWM-Tastwerte an den ESP32-Pins:

| Befehl `links rechts` | GPIO 19 | GPIO 18 | GPIO 16 | GPIO 17 |
| --- | ---: | ---: | ---: | ---: |
| `0 120` | 0 | 0 | 0 | 120 |
| `120 120` | 0 | 120 | 0 | 120 |
| `120 0` | 0 | 120 | 0 | 0 |
| `0 0` | 0 | 0 | 0 | 0 |

Die Werte sind PWM-Tastwerte von 0 bis 255, keine Spannungen. Wenn die
Serienausgabe und die gemessenen GPIO-Signale stimmen, die Motoren sich aber
gegenseitig beeinflussen, liegen die nächsten Prüfpunkte bei Ein-/Ausgangspaaren,
Motorverdrahtung und Versorgung. Die PWM-Simulation kann diese Hardware nicht
prüfen. Dreht lediglich eine Seite verkehrt herum, lässt sich ihre Polarität über
`INVERT_LEFT` oder `INVERT_RIGHT` getrennt ändern.

## Softwaretests

Im Projektordner ausführen:

```powershell
python -m unittest discover -s tests -v
```

Die Firmwaretests verwenden den echten Sketch mit simulierten GPIO-/PWM-Funktionen
und einem lokalen `g++`. Sie prüfen unter anderem, dass das Ändern einer Seite den
PWM-Tastwert der anderen Seite über den gesamten Bereich -255 bis 255 beibehält.
Die GUI-Tests prüfen dieselbe Trennung bei den tatsächlich erzeugten TCP-Befehlen.

Herstellerquellen:

- [Espressif: LEDC-PWM und Kanalzuordnung](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html)
- [Espressif: API-Änderungen von Core 2.x auf 3.x](https://docs.espressif.com/projects/arduino-esp32/en/latest/migration_guides/2.x_to_3.0.html)
- [MPS: MP6508-Datenblatt, insbesondere Dual-DC-Anwendung und PWM-Ansteuerung](https://www.monolithicpower.com/en/documentview/productdocument/index/version/2/document_type/Datasheet/lang/EN/sku/MP6508)
