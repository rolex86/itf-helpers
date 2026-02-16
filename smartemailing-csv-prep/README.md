# SmartEmailing CSV Prep

Aplikace pro přípravu importních CSV do SmartEmailingu:

- načte export\_\*.csv ze SmartEmailingu a vezme z něj schéma sloupců
- vezme 1+ zdrojových CSV a aplikuje transformace (split emailů, split jmen, programy → sloupce, bucket zemí)
- vygeneruje importní CSV pouze se sloupci, které existují ve schématu (odolné na změny polí v čase)
- umí používat uložené schéma z `config/schema_cache.yaml` (upload exportu je volitelný)
- umí načíst schéma přímo ze SmartEmailing API (ping + custom fields) a uložit ho do `config/schema_cache_api.yaml`
- při API chybě umí automaticky použít CSV fallback schéma
- ukládá metadata schématu (čas, zdroj, hash) a umí cache schématu smazat z UI
- umí zvolit kódování výstupních CSV (`utf-8`, `utf-8-sig`, `cp1250`)
- umí deduplikovat emaily ve finálním exportu (`bez`, `první`, `poslední`)
- report obsahuje i summary metriky kvality dat
- exportuje ZIP: import_CZ_SK.csv, import_DE_AT_CH.csv, import_EN.csv + report.csv

## Spuštění

Požadovaný Python: **3.9 až 3.12** (doporučeno 3.12 x64).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

### SmartEmailing API schéma

V UI můžeš zapnout volbu `Načítat schéma přímo ze SmartEmailing API`.
App používá `username + API key`, umí `ping`, stáhne custom fields a složí z nich cílové schéma.
CSV upload zůstává jako fallback.

### Windows rychlý start

Spusť `start.bat` (nebo zvlášť `setup.bat` a `run.bat`).
Skripty automaticky vyberou kompatibilní Python verzi z `py` launcheru.
Pokud není dostupný Python 3.9-3.12, `setup.bat` zkusí automaticky `py install 3.12-64`.

## Testy

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
