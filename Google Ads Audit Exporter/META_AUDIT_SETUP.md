# Meta Audit Connector

Tento modul je navržený jako oddělený flow vedle Google audit části, aby se stávající Google exporter nerozbil a aby bylo možné Meta audit rozvíjet samostatně.

## Co je připravené

- oddělené Meta integrační moduly v `app/integrations/meta/`
- oddělené webové stránky pro:
  - připojení Meta účtů
  - discovery Business / ad account / pixel / katalog dat
  - mapování Meta aktiv na existující exportní kontexty
  - spuštění selected / all enabled Meta exportu
- multi-context logika navázaná na existující `account_contexts`
- read-only exportní flow bez write operací

## Doporučené prostředí

Do `.env` doplň:

```env
META_APP_ID=
META_APP_SECRET=
META_API_VERSION=v25.0
META_DEFAULT_BUSINESS_ID=
META_DEFAULT_AD_ACCOUNT_IDS=
META_USER_AGENT=ITFutureMetaAudit/1.0
```

Pro první read-only verzi používej token se scope:

```text
ads_read
read_insights
business_management
catalog_management
```

Citlivé Meta údaje se nově neukládají do `app_state/meta_connections.json`.
Lokálně se ukládají do:

```text
.env.meta.local
```

Konfigurační JSON tak zůstává bez plaintext tokenu a bez plaintext app secretu.

## Stránky v aplikaci

Po spuštění webové appky používej tyto cesty:

```text
/meta/connections
/meta/discovery
/meta/mapping
/meta/audit
```

## Doporučený testovací postup

1. Otevři `/meta/connections` a ulož jedno Meta připojení.
2. Na stejné stránce spusť test připojení.
3. Otevři `/meta/discovery` a spusť discovery pro `connection_key`.
4. Otevři `/meta/mapping` a přiřaď Meta data ke konkrétním `account_contexts`.
5. Otevři `/meta/audit` a spusť:
   - selected context export
   - nebo all enabled contexts export
6. Zkontroluj výstupy v:

```text
exports/meta/{context_key}/{timestamp}/
```

## Očekávané výstupy

Podle dostupných oprávnění a assetů se ukládají zejména:

```text
business_assets.json
ad_accounts.json
campaigns.json
adsets.json
ads.json
creatives.json
insights_campaign_daily.csv
insights_adset_daily.csv
insights_ad_daily.csv
pixels.json
custom_conversions.json
catalogs.json
product_sets.json
product_feeds.json
feed_uploads.json
gtm_meta_tags.json
audit_findings.json
audit_report.md
```

## Architektura bez god objectů

Meta část je schválně rozdělená do menších modulů:

- `client.py`: Graph API klient
- `auth.py`: validace connection konfigurace a scopes
- `connections.py`: lokální správa uložených připojení
- `discovery.py`: načtení business / account / pixel / catalog struktury
- `sync.py`: export dat pro jeden kontext
- `normalizers.py`: převody raw dat na tabulkový formát
- `validators.py`: validační pravidla pro mapování
- `gtm_crosscheck.py`: základní GTM porovnání pro Meta pixel
- `audit_rules.py`: auditní pravidla a findings
- `exporters.py`: zápis exportního balíčku

## Aktuální záměr modulu

První verze je read-only audit. Nic se neposílá zpět do Meta Ads, katalogů ani pixelů.

Write režim, repair plan a automatické opravy mají zůstat až jako další samostatná fáze.
