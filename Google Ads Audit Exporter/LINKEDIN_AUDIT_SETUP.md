````markdown
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
- LinkedIn Marketing API je versioned API a hodnotu `LINKEDIN_API_VERSION` je potřeba průběžně aktualizovat podle aktuálně podporované verze
- default v aplikaci je aktuálně nastavený na `202606`

## Jak vytvořit LinkedIn Developer App

1. Vytvoř LinkedIn Developer App.
2. Požádej o Marketing Developer Platform / Advertising API přístup.
3. V Development tieru přidej konkrétní LinkedIn ad account do developer appky, jinak discovery nemusí účet najít.
4. Nastav Redirect URI na:

```text
http://localhost:5000/linkedin/oauth/callback
```
````

5. Ujisti se, že appka má schválené aspoň:

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
LINKEDIN_API_VERSION=202606
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

Manual token se uloží do:

```text
.env.linkedin.local
```

Connection metadata se uloží do:

```text
app_state/linkedin_connections.json
```

Do JSONu se nesmí uložit:

```text
access_token
refresh_token
client_secret
authorization code
```

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

Pokud LinkedIn vrátí refresh token, aplikace ho uloží lokálně a může ho použít pro obnovení access tokenu.

Pokud refresh token není dostupný nebo refresh selže, connection se má označit jako:

```text
needs_reauth
```

## Discovery

Na `/linkedin/discovery`:

1. vyber connection,
2. spusť discovery,
3. zkontroluj počty:
   - ad accounts
   - ad account users
   - campaign groups
   - campaigns
   - creatives
   - conversions
   - campaign conversions
   - Insight Tags
   - Insight Tag domains
   - lead forms

Discovery snapshoty se ukládají do:

```text
app_state/linkedin_discovery/
```

Discovery má být best-effort. Pokud některý endpoint vrátí 401/403 nebo jinou chybu, nemá spadnout celý běh. Má se uložit partial výsledek a warning.

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
   - Lead Sync enabled
   - Web scan enabled

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
connection_summary.json
mapping_used.json
ad_accounts.json
ad_account_users.json
organizations.json
campaign_groups.json
campaigns.json
creatives.json
creative_content.json
conversions.json
campaign_conversions.json
insight_tags.json
insight_tag_domains.json
lead_forms.json
lead_form_questions.json
lead_form_responses.csv
lead_form_responses.json
lead_notifications.json
insights_account_daily.csv
insights_campaign_daily.csv
insights_creative_daily.csv
insights_account_all.csv
insights_campaign_all.csv
insights_creative_all.csv
professional_demographics_account.csv
professional_demographics_campaign.csv
professional_demographics_creative.csv
gtm_linkedin_crosscheck.json
landing_page_scan.json
web_insight_tag_scan.json
utm_audit.csv
audit_findings.json
audit_report.md
```

Raw payloady jsou v:

```text
raw/
```

pokud je zapnuté:

```env
LINKEDIN_EXPORT_RAW=true
```

## Reporting

Reporting používá LinkedIn `adAnalytics`.

Performance reporting používá:

```text
q=analytics
```

a musí posílat:

```text
accounts=List(urn:li:sponsoredAccount:{id})
fields=...
```

Bez explicitního `fields` může LinkedIn vracet jen omezenou sadu metrik.

Exportované úrovně:

```text
ACCOUNT DAILY
CAMPAIGN DAILY
CREATIVE DAILY
ACCOUNT ALL
CAMPAIGN ALL
CREATIVE ALL
```

Professional demographics používají:

```text
q=statistics
```

a pivots:

```text
MEMBER_COMPANY_SIZE
MEMBER_INDUSTRY
MEMBER_SENIORITY
MEMBER_JOB_FUNCTION
MEMBER_JOB_TITLE
MEMBER_COUNTRY_V2
MEMBER_REGION_V2
```

Výstupy:

```text
professional_demographics_account.csv
professional_demographics_campaign.csv
professional_demographics_creative.csv
```

Professional demographics mohou být prázdné kvůli nízkému objemu dat, anonymizačním limitům, zpoždění nebo chybějícím oprávněním.

## Lead Sync

Lead responses mohou obsahovat PII.

Proto:

- neukazujeme je do UI
- nelogujeme jména/e-maily/telefony
- `audit_report.md` neobsahuje konkrétní osobní údaje
- raw lead responses ukládáme jen do exportu a pouze při zapnutém raw exportu

Lead Sync vyžaduje scope:

```text
r_marketing_leadgen_automation
```

Pokud scope není dostupný, export má pokračovat bez lead responses a uložit warning.

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

Kontroluje se hlavně:

- jestli context s LinkedIn účtem má v GTM Insight Tag
- jestli lead/conversion kampaně mají `lintrk('track')`
- jestli `conversion_id` odpovídá mappingu
- jestli tag není omylem v jiném brand/domain contextu

## Web scan

První verze dělá jednoduchý HTML scan:

- HTTP status
- redirect
- finální doména
- Insight Tag
- `_linkedin_partner_id`
- `lintrk`
- UTM parametry

Neřeší plný JS rendering.

## UTM audit

Kontroluje se:

- chybí `utm_source`
- chybí `utm_medium`
- chybí `utm_campaign`
- `utm_source` není očekávaná hodnota
- `utm_medium` není očekávaná hodnota
- landing page doména neodpovídá contextu
- redirect vede na jinou doménu

## Známá omezení

- tokeny expirují
- refresh token nemusí být vždy k dispozici
- Lead Sync může vyžadovat extra scopes a role
- některé LinkedIn reporting endpointy mohou vracet partial / empty data
- professional demographics nemusí být dostupné nebo mohou být agregované
- LinkedIn API nepodporuje u `adAnalytics` klasické stránkování
- delší `adAnalytics` dotazy mohou vyžadovat query tunneling
- LinkedIn API verzi je potřeba průběžně aktualizovat

```

```
