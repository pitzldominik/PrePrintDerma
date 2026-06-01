# Preprint-Suche Dermatologie

Automatisierte Suche nach dermatologischen Preprints auf bioRxiv und medRxiv.  
Keine Abhängigkeiten zur Laufzeit — kein API-Key, kein Proxy, kein Backend.

## Funktionsweise

```
GitHub Action (täglich 05:00 UTC)
  └─ fetch_preprints.py
       ├─ bioRxiv API  → Dermato* / Skin / Venerol*
       └─ medRxiv API  → Subject: Dermatology
            └─ data.json (18 Monate, dedupliziert)

GitHub Pages
  └─ index.html  liest data.json lokal
       ├─ Filterung im Browser (Zeitraum, Freitext, Quelle)
       └─ Excel-Export (.xlsx)
```

## Setup

### 1. Repository erstellen

```bash
# Option A: GitHub UI
# Neues Repository anlegen, alle Dateien hochladen

# Option B: git
git init preprint-derma
cd preprint-derma
# Alle Dateien hineinkopieren
git add .
git commit -m "init"
git remote add origin https://github.com/DEIN-NAME/preprint-derma.git
git push -u origin main
```

### 2. GitHub Pages aktivieren

Repository → **Settings** → **Pages**  
→ Source: `Deploy from a branch` → Branch: `main` → Folder: `/ (root)` → **Save**

Nach ~1 Minute erreichbar unter:  
`https://DEIN-NAME.github.io/preprint-derma`

### 3. Ersten Datenabruf starten

Repository → **Actions** → **Fetch Preprints** → **Run workflow** → **Run workflow**

Der Lauf dauert ca. 2–5 Minuten. Danach ist `data.json` befüllt und die Seite zeigt Ergebnisse.

Ab dann läuft die Action automatisch täglich um 05:00 UTC.

## Dateistruktur

```
preprint-derma/
├── index.html                        # Frontend (GitHub Pages)
├── fetch_preprints.py                # Daten-Skript (GitHub Action)
├── data.json                         # Automatisch generiert, nicht manuell bearbeiten
├── .github/
│   └── workflows/
│       └── fetch-preprints.yml       # Action-Definition
└── README.md
```

## Ausgabe-Felder

| Feld | Beschreibung |
|------|-------------|
| Datum | Erscheinungsdatum des Preprints |
| Quelle | bioRxiv oder medRxiv |
| Titel | Vollständiger Titel (klickbar) |
| Autoren | Alle Autoren, semikolon-getrennt |
| Korr. Autor | Letzter Autor (typischerweise korrespondierend) |
| E-Mail | E-Mail wenn in API-Daten vorhanden |
| Link | DOI-Link zum Artikel |

## Filter

- **Zeitraum**: 1 bis 18 Monate (data.json enthält immer 18 Monate)
- **Textsuche**: Mehrere Wörter = AND-Logik, durchsucht Titel + Abstract
- **Quelle**: bioRxiv, medRxiv oder beide

## Lizenz

MIT
