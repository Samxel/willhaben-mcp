<h1 align="center">willhaben-mcp</h1>

<p align="center"><sub>Aus derselben Reihe: <a href="https://github.com/Samxel/geizhals-mcp">geizhals-mcp</a> · <a href="https://github.com/Samxel/kleinanzeigen-mcp">kleinanzeigen-mcp</a></sub></p>

Ein MCP-Server, mit dem eine KI [willhaben.at](https://www.willhaben.at)
durchsuchen und die vollständigen Details von Anzeigen abrufen kann. Er deckt
den Marktplatz, den Bereich Auto & Motor und Immobilien ab, nutzt dafür die
nachgebaute API der willhaben-App und gibt der KI die wichtigen Felder zurück.
<p align="center">
  <a href="https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz"><img alt="Marktplatz listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.marktplatz&label=Marktplatz&color=green&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/gebrauchtwagen/auto"><img alt="Auto & Motor listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.autos&label=Auto%20%26%20Motor&color=ff7300&suffix=%20listings&cacheSeconds=3600"></a>
  <a href="https://www.willhaben.at/iad/immobilien"><img alt="Immobilien listings" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fcoverage.json&query=%24.immobilien&label=Immobilien&color=1e6fff&suffix=%20listings&cacheSeconds=3600"></a>
</p>
<p align="center">
  <a href="https://github.com/Samxel/willhaben-mcp/graphs/traffic"><img alt="Clones" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FSamxel%2Fwillhaben-mcp%2Fcoverage%2Fclones.json&query=%24.total_clones&label=Clones&color=6e7781&suffix=%20total&cacheSeconds=3600"></a>
</p>

<p align="center">
  <a href="#highlights">Highlights</a> ·
  <a href="#einrichtung">Einrichtung</a> ·
  <a href="#in-claude-einbinden-und-andere-mcp-clients">In Claude einbinden</a> ·
  <a href="#selbst-starten">Starten</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#hinweise">Hinweise</a> ·
  <a href="#tests">Tests</a> ·
  <a href="#haftungsausschluss">Haftungsausschluss</a> ·
  <a href="#lizenz">Lizenz</a>
</p>

<p align="center"><sub><a href="README.md">Read this in English</a></sub></p>

## Highlights

- **Anzeigen auch optisch prüfen**  
  Lädt die echten Fotos einer Anzeige herunter, damit eine KI mit Bildverständnis Zustand, Gebrauchsspuren, Schäden, Vollständigkeit und andere Details selbst sieht, statt sich nur auf die Beschreibung des Verkäufers zu verlassen. Standardmäßig klein, auf Wunsch scharf genug, um ein Etikett zu lesen.

- **Alle Filter von willhaben**  
  Suche mit allen verfügbaren Filtern in Marktplatz, Immobilien und Auto & Motor, darunter Kategorien, Marken, Zustand, Größe, Farbe, Preis, Bundesland und Bezirk (bis hinunter zur Wiener Postleitzahl), Fahrzeugdaten, Ausstattung und vieles mehr.

- **Vom Fund bis zur genauen Prüfung**  
  Passende Anzeigen finden und dann vollständige Details, die ganze Beschreibung, Merkmale, genaue Standorte und alle Fotos für eine genauere Analyse abrufen, für eine Anzeige oder bis zu zehn auf einmal.

## Einrichtung

Drei Wege, einer reicht. Bei keinem muss etwas im Hintergrund laufen: Der
Client startet den Server, wenn er ihn braucht.

**Claude Desktop, ohne Terminal.** `willhaben-mcp.mcpb` aus dem
[neuesten Release](https://github.com/Samxel/willhaben-mcp/releases/latest)
herunterladen und doppelklicken. Claude Desktop zeigt einen
Installationsdialog und richtet die Abhängigkeiten selbst ein. Fertig, den Rest
dieses Abschnitts kannst du überspringen.

**Mit [uv](https://docs.astral.sh/uv/), ohne Klonen.** Ein Befehl startet die
aktuelle Version direkt von GitHub:

```bash
uvx --from git+https://github.com/Samxel/willhaben-mcp willhaben-mcp --stdio
```

**Aus einem Klon.** Braucht Python 3.10+.

```bash
git clone https://github.com/Samxel/willhaben-mcp
cd willhaben-mcp
pip install -r requirements.txt
python main.py --stdio
```

`uv run main.py --stdio` geht auch ohne `pip install`: `main.py` bringt die
Liste seiner Abhängigkeiten selbst mit.

## In Claude einbinden (und andere MCP-Clients)

### Claude Code

Ein Befehl, keine Konfigurationsdatei:

```bash
claude mcp add willhaben -- uvx --from git+https://github.com/Samxel/willhaben-mcp willhaben-mcp --stdio
```

Aus einem Klon zeigst du stattdessen auf `main.py`:

```bash
claude mcp add willhaben -- python /pfad/zu/willhaben-mcp/main.py --stdio
```

Mit `--scope user` steht der Server in jedem Projekt zur Verfügung, nicht nur
im aktuellen. `claude mcp list` zeigt, ob die Verbindung steht, `claude mcp
remove willhaben` entfernt ihn wieder.

### Claude Desktop

Am einfachsten ist die `.mcpb` aus der [Einrichtung](#einrichtung). Ohne sie
öffnest du **Settings > Developer > Edit Config**, trägst `willhaben` in
`claude_desktop_config.json` ein und startest Claude Desktop neu:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Samxel/willhaben-mcp", "willhaben-mcp", "--stdio"]
    }
  }
}
```

Aus einem Klon nimmst du `"command": "python"` und `"args":
["C:\\pfad\\zu\\willhaben-mcp\\main.py", "--stdio"]` (der volle Pfad zu
`main.py`, unter macOS/Linux mit normalen Schrägstrichen).

Eigene Connectors (**Settings > Connectors**) verbinden sich aus der Cloud von
Anthropic und erreichen deshalb keinen Server, der nur auf deinem Rechner
läuft. Willst du die Tools auch in claude.ai und den Mobil-Apps nutzen, machst
du den HTTP-Server (siehe [Selbst starten](#selbst-starten)) über cloudflared,
ngrok oder einen Rechner mit öffentlichem Hostnamen erreichbar und trägst die
HTTPS-URL unter **Settings > Connectors > Add custom connector** ein. Dann kann
aber jeder, der die URL kennt, den Server nutzen, also stell eine
Authentifizierung davor.

Alle Tools sind als nur lesend markiert, Clients, die das beachten, fragen also
nicht vor jedem Aufruf nach. In Claude Desktop kannst du außerdem unter
**Settings > Connectors > willhaben** das Dropdown rechts bei den Tools auf
**Always allow** stellen.

### Cursor, Codex, VS Code und andere Clients mit JSON-Konfiguration

Derselbe Eintrag wie bei Claude Desktop, in der MCP-Konfiguration des Clients:

```json
{
  "mcpServers": {
    "willhaben": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Samxel/willhaben-mcp", "willhaben-mcp", "--stdio"]
    }
  }
}
```

Clients, die sich stattdessen mit einer URL verbinden, bekommen `"type":
"http"` und `"url": "http://127.0.0.1:8010/mcp"`, mit dem Server wie unten
gestartet.

## Selbst starten

Ohne `--stdio` startet der Server über Streamable HTTP und zeigt an, wo er
lauscht:

```bash
python main.py
```

```
Starting willhaben-mcp server on http://127.0.0.1:8010/mcp
```

Diese URL trägst du im Client ein, zum Beispiel mit `claude mcp add
--transport http willhaben http://127.0.0.1:8010/mcp`. Host, Port und Pfad
stehen oben in `willhaben/server.py`. Mit pip oder uv installiert, machen der
Befehl `willhaben-mcp` und `python -m willhaben` dasselbe wie `python main.py`.

## Tools

Die drei Suchen teilen sich die Parameter für Region und Blättern: `area` ist
ein österreichisches Bundesland (`wien`, `niederoesterreich`, …), und
`district` grenzt auf Bezirke ein, per Name (`"Graz"`, `"Innsbruck-Land"`,
`"Neubau"`) oder Wiener Postleitzahl (`"1070"`); dazu `sort_by`, `rows`
(höchstens 50), `offset` und `next_offset` / `has_more` zum Weiterblättern.
Ergebnisse kommen als kompaktes JSON, Felder ohne Wert fallen weg. Suchtreffer
haben keinen eigenen Link: Die Anzeigenseite ist
`https://www.willhaben.at/iad/object?adId={id}`. Das steht einmal in den
Server-Anweisungen, statt in jedem Treffer wiederholt zu werden.

`exclude`, `require` und `hide_reserved` funktionieren in allen drei Suchen
gleich. Sie laufen hier und blättern weiter, bis `rows` Treffer gefunden oder
250 Anzeigen durchgesehen sind:
- `exclude` verwirft Titel, die eines dieser Wörter enthalten, etwa `["pro",
  "max", "mini"]`, um ein Basismodell von seinen eigenen Ausstattungsvarianten
  freizuhalten, oder `["motorschaden", "export"]` bei Autos. Es trifft ein
  ganzes Wort oder das Ende eines deutschen Kompositums (`"kabel"` erwischt
  `"Ladekabel"`), aber nie einen bloßen Wortanfang, `"pro"` verschont also
  jeden `"Prozessor"`.
- `require` behält Treffer, in denen jeder Begriff vorkommt, wobei eine Zahl
  mit Einheit in jeder Schreibweise erkannt wird: `["128gb"]`, `["128 GB"]`
  und `["128 gb"]` sind ein und derselbe Filter und finden jeweils alle drei
  Schreibweisen. Beim Marktplatz werden Titel und Beschreibung geprüft;
  Auto- und Immobilientreffer haben keine Beschreibung, dort zählt der Titel.
- `hide_reserved` verwirft reservierte und verkaufte Anzeigen.

<details>
<summary><h3>Marktplatz</h3></summary>

**`search_willhaben(keyword, ...)`**

Durchsucht den Marktplatz. `keyword` ist optional, wenn du eine `category`
angibst.

Filter:
- `category` (ID oder Name; ein Name wird in jeder Wortreihenfolge erkannt,
  und bei einem Fehlgriff kommen die ähnlichsten Kategorien als Vorschlag)
- `condition` (neu, gebraucht, generalüberholt, …)
- `clothing_size` und `shoe_size`
- `color`, `pattern`
- `brand`
- Region, Verkäufertyp, Preisspanne, PayLivery (eine Zahlungsart, kein
  Versand), letzte 48 Stunden
- `title_only` (jedes Wort aus `keyword` muss im **Titel** stehen, nicht nur
  irgendwo im Anzeigentext), dazu `exclude`, `require` und `hide_reserved` von
  oben
- **Spezifikationen gehören in `require`.** Sie stattdessen in `keyword` zu
  schreiben, ist der eine Fehler, der eine Suche unbemerkt kaputt macht.
  Verkäufer schreiben dieselbe Speichergröße auf neun verschiedene Arten, und
  jede fünfte Anzeige lässt sie ganz aus dem Titel weg. `keyword="iPhone 13 128
  GB", title_only=True` findet deshalb fast nichts, während `keyword="iPhone
  13", require=["128gb"]` findet, was gemeint war
- Zubehör löst keiner dieser Filter, weil eine Hülle genau den Namen des
  Handys trägt. Wörter wie `"hülle"` oder `"akku"` auszuschließen, geht nach
  hinten los, weil sie auch in Titeln echter Angebote stehen (`"Akku 100%"`).
  Besser eine Untergrenze mit `price_from` bei rund 15–20 % des echten
  Produktpreises setzen (`get_model_price_range` von Geizhals liefert diese
  Zahl)
- `handover` (`"versand"` / `"abholung"`). Der Versand steht gar nicht in der
  Suchantwort von willhaben, das kostet also eine Detailanfrage pro
  verbleibendem Kandidaten, höchstens 40, ausgewiesen als `detail_lookups`
- Sortierung und Blättern

Gibt eine gekürzte Trefferliste zurück, jeweils mit einem numerischen
`price_amount`. Ein `status` („reserved“ / „sold“), der aus dem Titel gelesen
wird, steht nur bei Anzeigen, die nicht aktiv sind, weil willhaben selbst eine
Anzeige immer als aktiv meldet. `rows` ist standardmäßig 4.

**`list_categories(query, parent_id, limit=50)`**

Findet Kategorie-IDs für den Filter `category`. Jeder Eintrag bringt seinen
vollen Pfad mit („Baby / Kind > Kindermode > Mädchenmode > Kleider / Röcke“),
damit ähnliche Namen auseinanderzuhalten sind.
- `query`: den ganzen Baum nach Namen durchsuchen
- `parent_id`: eine Ebene tiefer gehen (ohne beides kommen die obersten
  Kategorien)

Der ganze Baum (rund 3500 Kategorien) liegt dem Server in
`willhaben/data/marktplatz/categories.json` bei.

**`search_brands(category, term, limit=40)`**

Marken hängen von der Kategorie ab, und es gibt über 1000 pro Kategorie,
deshalb funktioniert das wie eine Autovervollständigung. Gib eine Kategorie und
einen Suchbegriff an, um passende Marken-IDs zu bekommen, und reiche eine ID an
`search_willhaben(brand=...)` weiter.

Hinweis: Zustand, Größen, Farbe, Muster und Marke hängen von der Kategorie ab.
In einer breiten Kategorie können sie null Treffer liefern, geh also vorher in
eine bestimmte Unterkategorie.

</details>

<details>
<summary><h3>Auto & Motor</h3></summary>

**`search_autos(make, model, ...)`**

Durchsucht Gebrauchtwagen. Alle Filter sind optional.
- `make` (ID oder Name) und `model` (ID oder Name, z. B. „3er-Reihe“)
- `car_type`, `fuel`, `transmission`, `wheel_drive`
- `condition` (Gebrauchtwagen, Neuwagen, Oldtimer, …), `color`, `dealer`
- `equipment` (z. B. Sitzheizung, Anhängerkupplung)
- Bereiche: `price_from/to`, `year_from/to`, `mileage_from/to`,
  `power_from/to` (**kW**)
- `warranty`, `condition_report` (Pickerl §57a), Region, letzte 48 Stunden,
  Sortierung, Blättern
- `exclude`, `require` und `hide_reserved` von oben, z. B.
  `exclude=["motorschaden", "export", "bastler"]`

Aufzählungsfilter nehmen die Bezeichnung oder ID von willhaben, und ein
vertauschter Bereich wird abgelehnt, statt still ignoriert zu werden. Die
Ergebnisse nennen Marke und Modell und enthalten Baujahr, Kilometerstand,
Kraftstoff und Getriebe. Die Leistung kommt als `power_kw` und `power_ps`,
weil willhaben kW speichert, Anzeigen und Käufer aber in PS reden.

willhaben setzt bei einer Markensuche eine bezahlte Händleranzeige an die
Spitze, egal was die Filter sagen (ein Auto um 48.000 € in einer Suche „bis
15.000 €“). Treffer außerhalb der angefragten Bereiche fallen weg und werden in
`dropped_off_filter` gezählt. Die Filter- und Markendaten liegen in
`willhaben/data/auto-motor/filters.json`.

**`list_car_makes(query, limit=60)`**

Listet Marken-IDs für `search_autos`. Das optionale `query` filtert nach
Namen.

**`list_car_models(make)`**

Listet die Modelle einer Marke (live abgerufen, weil Modelle zur Marke
gehören).

</details>

<details>
<summary><h3>Immobilien</h3></summary>

**`search_immobilien(property_type, ...)`**

Durchsucht Immobilien. Zuerst einen `property_type` wählen (Kauf oder Miete
und die Art der Immobilie); alles andere ist optional.
- `property_type`: ID oder Name, z. B. „Wohnung mieten“, „Haus kaufen“,
  „Grundstücke“, „Gewerbeimmobilie mieten“ oder „Alle Immobilien“ (Standard)
- `object_type` (z. B. „Einfamilienhaus“, „Dachgeschosswohnung“), `rooms`
  („1“–„5“, „6-9“, „10+“)
- `features` (Garage, Keller, Einbauküche, …), `outdoor` (Balkon, Terrasse,
  Garten, …)
- Bereiche: `price_from/to`, `area_from/to` (Wohnfläche in m²), `plot_from/to`
  (Grundfläche in m²)
- `postcode`: genaue Postleitzahlen wie `"1070"` oder `"6020"`, an jedem
  Treffer geprüft. Eine Wiener Postleitzahl grenzt die Suche zusätzlich schon
  bei willhaben auf ihren Bezirk ein; eine außerhalb Wiens kombinierst du am
  besten mit `area` oder `district`
- Region und Bezirk, `exclude`, `require` und `hide_reserved` von oben (z. B.
  `exclude=["befristet"]`), letzte 48 Stunden, Sortierung, Blättern

Filter, die zum gewählten Typ nicht passen, ignoriert willhaben; ein
vertauschter Bereich wird hier abgelehnt, statt still wegzufallen. Die
Ergebnisse bringen den numerischen `price_amount` und `price_per_m2` neben
`living_area_m2`, `plot_area_m2`, `rooms`, `floor`, `district` und `address`.
Die Typ- und Filterdaten liegen in `willhaben/data/immobilien/filters.json`.

**`list_immobilien_types()`**

Listet die Immobilienarten für `search_immobilien`.

</details>

<details>
<summary><h3>Gemeinsam</h3></summary>

**`get_ad_detail(ad_id | ids)`**

Alles zu einer Anzeige: die volle Beschreibung, einzeln aufgeführte Merkmale,
Kategoriepfad, genauer Standort und wie viele Fotos sie hat. Funktioniert in
allen Bereichen. Nimm dafür eine ID aus einer Suche. Die App-API kürzt die
Beschreibung bei rund 600 Zeichen; sieht sie abgeschnitten aus, wird der
vollständige Text von der Webseite der Anzeige geholt. Statt `ad_id` gehen
auch bis zu 10 `ids`, um mehrere in einem Aufruf zu vergleichen; sie kommen als
`{count, ads, failed}` zurück, und eine Anzeige, die nicht mehr existiert,
landet in `failed`, statt den Rest scheitern zu lassen.

Nur hier sagt willhaben auch, wie ein Artikel übergeben wird: `handover`
(„Selbstabholung“, „Versand“) mit `ships` / `pickup_only` daneben. PayLivery in
der Suche ist eine Zahlungsart und sagt nichts über den Versand.

**`get_ad_images(ad_id, max_images=4, detail="low")`**

Lädt die Fotos einer Anzeige auf dem Server herunter (höchstens 10) und gibt
sie als echte Bilder zurück, damit ein Client mit Bildverständnis die Fotos
sieht statt nur URLs. `detail="low"` (300×400, rund 160 Tokens pro Foto)
reicht für Zustand und Vollständigkeit; `detail="high"` (900×1200, rund 1400
Tokens) liest Kleingedrucktes wie ein Etikett, eine Seriennummer oder einen
Bildschirm.

**`get_ad_seller(ad_id)`**

Wer verkauft: Name, privat oder Händler, Bewertung und Antwortzeit (bei
privaten Verkäufern), Mitglied seit bzw. angelegt am und Standort. Nützlich für
eine Prüfung auf Vertrauen und Plausibilität, die die Anzeige selbst nicht
beantwortet.

</details>

## Hinweise

- Die Detail-API spricht nur HTTP/2 und braucht ein
  `x-wh-application-token`. Dieses Token stellt willhaben (30 Tage gültig)
  gegen eine signierte Anfrage aus. Der Server holt bei Bedarf ein aktuelles
  Token und erneuert es bei einem 401 automatisch. Es gibt kein Token, das man
  von Hand eintragen müsste. Nur wenn willhaben den Signaturschlüssel wechselt,
  funktioniert das gespeicherte `WH_TOKEN_REQUEST` nicht mehr; der Kommentar
  darüber in `willhaben/api.py` erklärt, wie man aus der App ein neues
  mitschneidet.
- Erzeugte Daten liegen unter `willhaben/data/`, ein Ordner pro Bereich:
  `marktplatz/categories.json` (der Baum mit rund 3500 Kategorien),
  `auto-motor/filters.json` (Autofilter, Optionen und Marken),
  `immobilien/filters.json` (Immobilienarten, Filter und Optionen) und
  `regions/districts.json` (die 116 Bezirke mit ihren Gebiets-IDs, neu erzeugt
  von `scripts/build_districts.py`). Der Server lädt sie beim Start. Neu
  abrufen, wenn willhaben etwas ändert.
- `willhaben/server.py` enthält die MCP-Tools; `main.py` startet den Server
  nur aus einem Klon heraus. Daneben im Paket `willhaben`: der API-Zugang
  (`api.py`), der Titelabgleich, die Marktplatzfilter, die Bereiche Autos und
  Immobilien, die Bezirke und die Ergebniszusammenfassungen.
- Ergebnisse gehen als kompaktes JSON ohne leere Felder raus, die
  Tool-Schemas nennen jede Auswahl nur einmal und lassen die Titel und
  `null`-Zweige weg, die pydantic sonst einfügt, und was alle Tools gemeinsam
  haben, steht einmal in den Server-Anweisungen statt in jeder
  Tool-Beschreibung. All das hält klein, was das Modell lesen muss.
- Releases: `version` in `pyproject.toml` und `manifest.json` erhöhen, dann
  einen Tag wie `v1.0.1` pushen. Der Release-Workflow lässt die Tests laufen,
  baut Wheel, sdist und das `.mcpb`-Bundle und hängt sie an ein GitHub-Release.
- Die Badges lesen `coverage.json` und `clones.json` aus dem Branch
  `coverage`. Eine tägliche GitHub Action aktualisiert sie dort, damit die
  Zahlen nicht jeden Tag einen Commit in `main` erzeugen.
- Die API ist in [`search_api.md`](search_api.md) dokumentiert (auf Englisch).
- Das hier nutzt die interne API von willhaben, keine offizielle. Geh
  pfleglich damit um.

## Tests

```bash
pip install -r requirements.txt pytest
pytest
```

Die Tests laufen offline gegen ein nachgebautes willhaben und gehen durch einen
echten MCP-Client, stdio-Transport eingeschlossen. `WILLHABEN_LIVE=1 pytest`
fügt Kurztests gegen die echte API hinzu. Die CI lässt die Offline-Tests bei
jedem Push laufen und die Live-Tests einmal am Tag; dabei fällt auf, wenn
willhaben die App-API ändert oder den Signaturschlüssel für das Token wechselt.

## Haftungsausschluss

Das ist ein unabhängiges, inoffizielles Projekt und steht in keiner Verbindung
zu willhaben, wird von willhaben weder unterstützt noch empfohlen. „willhaben“
und alle zugehörigen Marken gehören ihren jeweiligen Inhabern.

Es wird nur zu Lern- und Forschungszwecken veröffentlicht. Es spricht mit der
internen API von willhaben, die nicht für die Öffentlichkeit gedacht ist und
sich jederzeit ändern oder wegfallen kann. Du bist dafür verantwortlich, wie du
es nutzt: Halte dich an die Nutzungsbedingungen von willhaben, die
Robots-Regeln und geltendes Recht, überlaste die Server nicht und verarbeite
keine personenbezogenen Daten aus Anzeigen. Keinerlei Gewährleistung, Nutzung
auf eigenes Risiko.

Wenn du Rechteinhaber bist und Bedenken zu diesem Repository hast, eröffne
bitte ein Issue, ich melde mich umgehend.

## Lizenz

[`MIT LICENSE`](LICENSE). Bereitgestellt „wie besehen“, ohne Gewährleistung.

<p align="center"><sub><a href="README.md">Read this in English</a></sub></p>
