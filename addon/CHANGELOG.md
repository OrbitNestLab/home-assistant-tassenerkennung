# App 0.3.2

Ingress-Aufrufe für Status, Aktionen und Kamerabilder werden relativ zum tatsächlichen Ingress-Pfad zusammengesetzt. Das funktioniert auch, wenn Home Assistant die App-URL ohne abschließenden Schrägstrich öffnet.

# 0.1.1

YOLOX S statt Tiny für die bestehende Kameraposition. Positivbild bei Küchenbeleuchtung: Modellwert 0,928 statt 0,531; Mindestschwelle bleibt 0,60. Nachtbilder ohne verlässliche Erkennung bleiben gesperrt. HTTP-Protokoll 1 unverändert; kein HA-Neustart erforderlich.

# 0.1.0

Erster lokaler Auswertungsdienst.
# 0.3.0

Eigenständige Home-Assistant-Seitenleisten-App mit lokalem Livebild, geführtem Einlernassistenten und sicherem Kameraschnellwechsel. Die neue Kamera kann in der App ausgewählt, vorab angesehen, übernommen und anschließend neu ausgerichtet werden. Profile einer alten Kameraperspektive werden nicht ungeprüft weiterverwendet. Der Zugriff auf Home Assistant ist auf Tassenerkennungs-Entitäten und fest definierte Schaltflächen begrenzt. Keine beliebigen Dienste und keine direkten Getränkeaktionen. Das bestehende authentifizierte Erkennungsprotokoll bleibt kompatibel.

