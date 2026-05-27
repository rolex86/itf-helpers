# Google Ads Audit Exporter v1

Read-only exporter pro interni audit Google Ads uctu. Nastroj stahuje vybrane reporty pres Google Ads API, uklada raw CSV, metadata a sestavuje jeden `audit_export.xlsx` soubor s vice listy. Ovlada se pres CLI i browser UI nad stejnym export workflow.

## Co umi

- Export pro jeden `customer_id`
- Predvolby obdobi `LAST_30_DAYS`, `LAST_90_DAYS`, `LAST_365_DAYS`
- Vlastni `date_from` / `date_to`
- Raw CSV export, metadata JSON a souhrnny XLSX
- Pokracovani exportu i pri selhani jednotliveho reportu
- Jednoduche auditni flagy pro rychlou orientaci
- Browser rozhrani pro konfiguraci a spousteni bez terminalu

## Bezpecnost

Tato verze je striktne read-only.

- Nepouziva zadne mutate endpointy ani mutate sluzby.
- Neprovadi create, update, remove, pause, enable ani zadne upravy bidu, budgetu, keywordu nebo reklam.
- Veskery kod je postaveny pouze na reportovacich GAQL dotazech pres `GoogleAdsService.search_stream`.

## Pozadavky

- Python 3.10+
- OAuth 2.0 pristup do Google Ads API
- Developer token
- `customer_id` uctu, ktery chcete exportovat

Instalace zavislosti:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Konfigurace

1. Zkopirujte `.env.example` na `.env` a doplnte pristupy.
2. Zkopirujte `config.example.yaml` na `config.yaml` a upravte exportni nastaveni.

### `.env`

```env
GOOGLE_ADS_DEVELOPER_TOKEN=
GOOGLE_ADS_CLIENT_ID=
GOOGLE_ADS_CLIENT_SECRET=
GOOGLE_ADS_REFRESH_TOKEN=
GOOGLE_ADS_LOGIN_CUSTOMER_ID=
```

### `config.yaml`

```yaml
customer_id: "1234567890"

date_range:
  preset: "LAST_90_DAYS"
  date_from: null
  date_to: null

output:
  base_dir: "exports"
  xlsx_filename: "audit_export.xlsx"
  include_raw_csv: true
  include_metadata: true

reports:
  account: true
  campaigns: true
  campaigns_monthly: true
  ad_groups: true
  keywords: true
  search_terms: true
  ads: true
  assets: true
  devices: true
  locations: true
  landing_pages: true
  conversion_actions: true
  pmax_campaigns: true
  pmax_asset_groups: true
  change_history: true

flags:
  min_spend_micros: 100000000
  min_clicks: 50
  target_cpa_micros: null
  target_roas: null
  low_ctr_threshold: 0.01
```

## Spusteni

### Browser UI

Spusteni lokalniho rozhrani:

```bash
python -m app.web.main
```

Nebo na Windows jednoduse dvojklikem:

```text
Spustit Google Ads Audit Exporter.bat
```

Volitelne:

```bash
python -m app.web.main --host 127.0.0.1 --port 5000
```

Pak otevri v prohlizeci:

```text
http://127.0.0.1:5000
```

V rozhrani muzes:

- vyplnit OAuth a Google Ads hodnoty
- ulozit `.env` a `config.yaml`
- zapinat a vypinat reporty
- spustit export
- prohlizet historii exportu a stahnout XLSX

### CLI

Zakladni spusteni:

```bash
python -m app.main --config config.yaml
```

Prepsani `customer_id` a presetu z CLI:

```bash
python -m app.main --customer-id 1234567890 --preset LAST_90_DAYS
```

Vlastni datumovy rozsah:

```bash
python -m app.main --customer-id 1234567890 --date-from 2026-01-01 --date-to 2026-05-27
```

## Vystupy

Po spusteni vznikne exportni slozka:

```text
exports/
  2026-05-27_1234567890/
    audit_export.xlsx
    raw/*.csv
    metadata/*.json
```

Metadata obsahuje:

- `export_config.json`
- `account_info.json`
- `query_log.json`
- `errors.json`
- `export.log`

## Chovani pri chybach

- Pokud selze jednotlivej report, export pokracuje dal.
- Pokud selze autentizace, beh se ukonci.
- Pokud selze XLSX, raw CSV a metadata zustanou ulozene.
- Prioritni report `search_terms` je v pripade chyby vyrazne oznacen v `errors.json` i v `Summary` listu.

## Dulezite poznamky

- `change_history` v Google Ads API vyzaduje datumove okno uvnitr poslednich 30 dni a `LIMIT <= 10000`, proto je tenhle report ve v1 automaticky omezen na poslednich 30 dni bez ohledu na sirsi hlavni exportni rozsah.
- Nektera pole mohou byt v konkretnim uctu nebo typu kampani nedostupna. Exporter se u nepovinnych poli pokusi o fallback a doplni prazdne hodnoty misto padu celeho exportu.
- Lokacni report ve v1 preferuje stabilitu exportu; pokud API nevrati citelne nazvy lokaci, zustanou v reportu identifikatory nebo resource names.

## Struktura projektu

```text
app/
  main.py
  auth/
  config/
  web/
  google_ads/
    queries/
  export/
  audit/
  utils/
exports/
```
