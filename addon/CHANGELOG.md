# App 0.4.1

Maschine und Auslauf werden automatisch lokalisiert, perspektivisch stabilisiert und als vergrößerter kanonischer Prüfbereich verarbeitet. Die Oberfläche zeigt beobachtbare Hintergrundaufträge für Einrichtung, Kamerawechsel und Einlernen statt langer blockierender Browseranfragen. Der Kameraschnellwechsel führt durch Vorschau, automatische Ausrichtung und anschließendes Neueinlernen der benötigten Becherprofile.

# App 0.4.0

Neue Profil-v3-Auswertung mit Leerflächenabzug, Form-/Farbvergleich und Abstand zum zweitbesten Profil. Quell-, Tracking- und Prüfbild sind getrennt abrufbar; Maschinenfront und Auslauf werden unabhängig geprüft.

# App 0.3.3

Behebt den nachgewiesenen Ausfall aller App-Aktionen hinter Home Assistant Ingress: POST-Anfragen werden gepuffert statt als Datenstrom weitergeleitet, damit der JSON-Handler eine feste Nachrichtenlänge erhält. Anfragen ohne Längenfeld werden ausdrücklich als Übertragungsfehler gemeldet. Aktionsfehler werden nicht mehr pauschal als fehlende Home-Assistant-Verbindung dargestellt.

# App 0.3.2

Ingress-Aufrufe für Status, Aktionen und Kamerabilder werden relativ zum tatsächlichen Ingress-Pfad zusammengesetzt. Das funktioniert auch, wenn Home Assistant die App-URL ohne abschließenden Schrägstrich öffnet.

# 0.1.1

YOLOX S statt Tiny für die bestehende Kameraposition. Positivbild bei Küchenbeleuchtung: Modellwert 0,928 statt 0,531; Mindestschwelle bleibt 0,60. Nachtbilder ohne verlässliche Erkennung bleiben gesperrt. HTTP-Protokoll 1 unverändert; kein HA-Neustart erforderlich.

# 0.1.0

Erster lokaler Auswertungsdienst.
# 0.3.0

Eigenständige Home-Assistant-Seitenleisten-App mit lokalem Livebild, geführtem Einlernassistenten und sicherem Kameraschnellwechsel. Die neue Kamera kann in der App ausgewählt, vorab angesehen, übernommen und anschließend neu ausgerichtet werden. Profile einer alten Kameraperspektive werden nicht ungeprüft weiterverwendet. Der Zugriff auf Home Assistant ist auf Tassenerkennungs-Entitäten und fest definierte Schaltflächen begrenzt. Keine beliebigen Dienste und keine direkten Getränkeaktionen. Das bestehende authentifizierte Erkennungsprotokoll bleibt kompatibel.

