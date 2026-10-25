# Tassenerkennung

Diese lokale Home-Assistant-App berechnet Tassen-/Personenerkennungen und stellt den geführten Becher-Einlernmodus in einer eigenen Seitenleistenansicht bereit. Sie speichert keine Kamerabilder und besitzt keine direkten Kaffee- oder Getränkeaktionen.

Die Oberfläche ist ausschließlich über den authentifizierten Home-Assistant-Ingress erreichbar; es wird kein Host-Port veröffentlicht. Zugriffe der Integration auf die Bildauswertung benötigen weiterhin den lokalen API-Schlüssel. Der Schlüssel bleibt auf dem HA-Server.

Die App nutzt den kurzlebigen Supervisor-Zugriff ausschließlich für eine feste Liste eigener Tassenerkennungs-Entitäten. Sie kann Profilwahl, Profilname, Kameraauswahl und die fest definierten Tassenerkennungs-Knöpfe bedienen. Beliebige Home-Assistant-Dienste, Automationen oder Kaffeemaschinen-Entitäten sind nicht zugelassen. Die App liest keine Kamerazugangsdaten; Vorschau und verarbeitetes Prüfbild stammen aus Home Assistant.

## Kamera schnell wechseln

1. Die neue Kamera zuerst in Home Assistant hinzufügen.
2. In der App unter **Kamera wechseln** auswählen und das Vorschaubild prüfen.
3. **Kamera übernehmen** drücken.
4. Mit eingeschalteter Arbeitsflächenbeleuchtung **Neue Ansicht ausrichten** drücken.
5. Benötigte Becherprofile mit der neuen Perspektive neu einlernen.

Die bisherigen Profile werden vor Änderungen lokal gesichert. Profile einer anderen Kamera oder Perspektive werden nicht ungeprüft weiterverwendet. Dadurch bleiben Kaffeeaktionen gesperrt, bis die neue Ansicht verlässlich bestätigt wurde.

Modell: YOLOX S, Megvii, Apache-2.0 (beiliegender Lizenztext). Erst nach Bestätigung der Bildbereiche und Praxistests darf separat über eine Kaffeeautomatik entschieden werden.
