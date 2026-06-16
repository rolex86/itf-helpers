# LinkedIn Audit Connector

Tento modul je samostatná LinkedIn vrstva vedle Google a Meta části. Nespouští se dohromady s nimi a má vlastní connections, discovery, mapping i exportní workflow.

## Co modul dělá

- LinkedIn connections přes manual token nebo OAuth
- discovery ad accountů, kampaní, kreativ, conversions a lead form assetů
- mapování LinkedIn aktiv na existující lokální contexty
- export per-context a all-enabled-contexts
- reporting přes `adAnalytics`
- Lead Sync best-effort export
- GTM cross-check pro Insight Tag a `lintrk`
- jednoduchý landing page a UTM audit
- audit findings a markdown report

## Co je důležité vědět

- default je read-only režim
- write akce musí zůstat vypnuté přes `LINKEDIN_ENABLE_WRITE_ACTIONS=false`
- tokeny a client secret nejsou v JSONu
- citlivé údaje jdou do `.env.linkedin.local`
- LinkedIn scope availability se může lišit mezi appkami a účty

## Jak vytvořit LinkedIn Developer App

1. Vytvoř LinkedIn Developer App.
2. Požádej o Marketing Developer Platform / Advertising API přístup.
3. Nastav Redirect URI na:

```text
http://localhost:5000/linkedin/oauth/callback
```

4. Ujisti se, že appka má schválené aspoň:

```text
r_ads
r_ads_reporting
```

Pro širší audit jsou vhodné navíc:

```text
r_marketing_leadgen_automation
r_organization_lookup
rw_organization_admin
```

## `.env`

Do `.env` doplň:

```env
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:5000/linkedin/oauth/callback
LINKEDIN_API_VERSION=202605
LINKEDIN_USER_AGENT=ITFutureLinkedInAudit/1.0
LINKEDIN_ENABLE_WRITE_ACTIONS=false
LINKEDIN_DEFAULT_DATE_RANGE_DAYS=90
LINKEDIN_REQUEST_TIMEOUT_SECONDS=60
LINKEDIN_MAX_RETRIES=3
LINKEDIN_EXPORT_RAW=true
LINKEDIN_ENABLE_WEB_SCAN=true
LINKEDIN_ENABLE_LEAD_SYNC=true
LINKEDIN_ENABLE_CONVERSIONS_API_AUDIT=true
```

## Manual token fallback

Na `/linkedin/connections` můžeš:

1. vytvořit connection,
2. vložit access token ručně,
3. uložit connection,
4. spustit test connection.

Tohle je nejrychlejší způsob pro první interní ověření.

## OAuth flow

Na `/linkedin/connections` klikni na:

```text
Authorize LinkedIn
```

Flow:

1. appka vytvoří `state`,
2. přesměruje tě na LinkedIn,
3. callback doběhne na `/linkedin/oauth/callback`,
4. tokeny se uloží do `.env.linkedin.local`,
5. connection metadata zůstanou v `app_state/linkedin_connections.json`.

## Discovery

Na `/linkedin/discovery`:

1. vyber connection,
2. spusť discovery,
3. zkontroluj počty:
   - ad accounts
   - campaigns
   - creatives
   - conversions
   - lead forms

Discovery snapshoty se ukládají do:

```text
app_state/linkedin_discovery/
```

## Mapping

Na `/linkedin/mapping`:

1. vyber nebo zkontroluj lokální contexty,
2. zapni LinkedIn pro konkrétní context,
3. vyplň:
   - connection key
   - ad account IDs
   - organization IDs
   - expected domains
   - expected Insight Tag IDs
   - expected conversion IDs
   - expected lead form IDs
   - expected UTM source / medium
   - expected conversion type

Mapping se ukládá do:

```text
app_state/linkedin_mapping.json
```

## Export

Na `/linkedin/audit` můžeš:

- spustit selected context export
- spustit all enabled contexts export
- zapnout/vypnout:
  - raw payloady
  - reporting
  - professional demographics
  - Lead Sync
  - web scan
  - GTM cross-check

Výstup jde do:

```text
exports/linkedin/{context_key}/{timestamp}/
```

## Jak číst výstupy

Nejdůležitější soubory:

```text
manifest.json
campaigns.json
creatives.json
conversions.json
insight_tags.json
lead_forms.json
lead_form_responses.csv
insights_campaign_daily.csv
gtm_linkedin_crosscheck.json
landing_page_scan.json
utm_audit.csv
audit_findings.json
audit_report.md
```

## Lead Sync

Lead responses mohou obsahovat PII.

Proto:

- neukazujeme je do UI
- nelogujeme jména/e-maily/telefony
- `audit_report.md` neobsahuje konkrétní osobní údaje

## GTM cross-check

Hledá se:

```text
linkedin
lintrk
_linkedin_partner_id
LinkedIn Insight Tag
conversion_id
window.lintrk
```

## Web scan

První verze dělá jednoduchý HTML scan:

- Insight Tag
- `lintrk`
- redirect
- HTTP status
- UTM parametry

Neřeší plný JS rendering.

## Známá omezení

- tokeny expirují
- refresh token nemusí být vždy k dispozici
- Lead Sync může vyžadovat extra scopes a role
- některé LinkedIn reporting endpointy mohou vracet partial / empty data
- professional demographics nemusí být dostupné nebo mohou být agregované
- LinkedIn API verzi je potřeba průběžně aktualizovat

