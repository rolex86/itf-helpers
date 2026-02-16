# M0 API Spike - SmartEmailing v3

Datum: 2026-02-16

## Cíl
- ověřit integrační body pro `contacts/import/custom fields/listy` v API v3
- připravit bezpečnou fallback strategii, protože část API dokumentace je dostupná jako JS SPA

## Zdroje
- Oficiální landing: `https://www.smartemailing.cz/api/`
- Oficiální help článek: `https://help.smartemailing.cz/article/1474-api`
- Veřejný ping endpoint: `https://app.smartemailing.cz/api/v3/ping`
- Doplňková reference (community wrapper, použito jen jako inference):
  - `https://github.com/keltuo/php-smartemailing`
  - `https://github.com/pionl/smart-emailing-v3`

## Ověřeno (high confidence)
- `GET /api/v3/ping`
  - dostupné veřejně a vrací stav API
  - používá se pro test credentials/healthcheck

## Pravděpodobné endpointy (runtime fallback probing)
- Custom fields:
  - `GET /api/v3/customfields`
  - `GET /api/v3/custom-fields`
- Contact lists:
  - `GET /api/v3/contactlists`
  - `GET /api/v3/contact-lists`
- Bulk import:
  - `POST /api/v3/import`
  - `POST /api/v3/imports`
  - `POST /api/v3/import-contacts`

Poznámka: Tyto endpointy jsou v aplikaci implementované jako fallback kandidáti. Pokud účet používá jinou variantu, kandidáty lze doplnit v `config/mappings.yaml`.

## Závěr spike
- Pro produkční bezpečnost je kritické:
  1. `dry-run` před každým importem
  2. canary batch (prvních 50 kontaktů)
  3. hard limity podle režimu (`safe/full`)
  4. staging list jako cílový prostor
- Implementace v kódu:
  - schema sync z API + cache fallback
  - endpoint fallback probing
  - import payload variant probing
