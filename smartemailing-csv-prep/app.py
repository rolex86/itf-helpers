from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from src.export_smartemailing import (
    build_import_df,
    dataframe_to_csv_bytes,
    deduplicate_import_df,
    drop_empty_columns,
    split_by_bucket,
)
from src.io_utils import clean_columns, read_csv_best_effort
from src.normalize import detect_source, normalize_df
from src.reporting import build_report, find_duplicates_from_stats
from src.schema import Schema, schema_from_export_df
from src.jobs import append_job_history, load_job_history, summarize_job_alerts
from src.smartemailing_api import (
    DEFAULT_BASE_URL,
    SmartEmailingApiClient,
    SmartEmailingApiError,
    SmartEmailingCredentials,
    build_api_contacts_from_import_df,
    combine_schema_columns,
)
from src.transforms import (
    apply_country_bucket,
    apply_name_split,
    split_emails,
    validate_emails_without_split,
)


st.set_page_config(page_title="SmartEmailing CSV Prep", layout="wide")
st.title("SmartEmailing CSV Prep – generátor importních CSV")
st.markdown(
    """
    <style>
    div[data-testid="stFileUploaderDropzoneInstructions"] {
        position: relative;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"] > div:first-child,
    div[data-testid="stFileUploaderDropzoneInstructions"] p,
    div[data-testid="stFileUploaderDropzoneInstructions"] span {
        font-size: 0 !important;
        line-height: 0 !important;
    }
    div[data-testid="stFileUploaderDropzoneInstructions"]::before {
        content: "Přetáhněte soubor sem";
        font-size: 0.95rem;
        font-weight: 600;
        line-height: 1.4;
        display: block;
        margin-bottom: 0.25rem;
    }
    div[data-testid="stFileUploaderDropzone"] button,
    div[data-testid="stFileUploaderDropzone"] button * {
        font-size: 0 !important;
    }
    div[data-testid="stFileUploaderDropzone"] button::after {
        content: "Procházet soubory";
        font-size: 0.875rem;
        line-height: 1.4;
        display: inline-block;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


CFG_PATH = Path("config/mappings.yaml")
with CFG_PATH.open("r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

SCHEMA_CACHE_PATH = Path("config/schema_cache.yaml")
API_SCHEMA_CACHE_PATH = Path("config/schema_cache_api.yaml")
API_CREDENTIALS_PATH = Path("config/se_api_credentials.local")


def schema_hash(columns: list[str]) -> str:
    payload = "\n".join(columns).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cached_schema(path: Path) -> tuple[Schema | None, dict[str, Any]]:
    meta: dict[str, Any] = {}
    if not path.exists():
        return None, meta
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        columns = [str(c).strip() for c in data.get("columns", []) if str(c).strip()]
        if not columns:
            return None, meta
        meta = {k: v for k, v in data.items() if k != "columns"}
        if "schema_hash" not in meta:
            meta["schema_hash"] = schema_hash(columns)
        if "version" not in meta:
            meta["version"] = 1
        return Schema(columns=columns, columns_set=set(columns)), meta
    except Exception:
        return None, meta


def save_cached_schema(
    path: Path,
    schema: Schema,
    source_file: str,
    source_kind: str = "csv_upload",
    extra_meta: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "source_kind": source_kind,
        "schema_hash": schema_hash(schema.columns),
        "columns": schema.columns,
    }
    if extra_meta:
        payload.update(extra_meta)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def load_saved_api_credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"username": "", "api_key": "", "base_url": DEFAULT_BASE_URL}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {
            "username": str(data.get("username", "")).strip(),
            "api_key": str(data.get("api_key", "")).strip(),
            "base_url": str(data.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL,
        }
    except Exception:
        return {"username": "", "api_key": "", "base_url": DEFAULT_BASE_URL}


def save_api_credentials(path: Path, username: str, api_key: str, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "username": str(username).strip(),
        "api_key": str(api_key).strip(),
        "base_url": str(base_url).strip() or DEFAULT_BASE_URL,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def clear_api_credentials(path: Path) -> None:
    if path.exists():
        path.unlink()


def build_system_schema_columns(cfg: dict[str, Any]) -> list[str]:
    se_cfg = cfg.get("smartemailing", {})
    field_map = se_cfg.get("field_map", {})
    base = [str(col).strip() for col in field_map.keys() if str(col).strip()]

    programs_cfg = se_cfg.get("programs", {})
    combined_field = str(programs_cfg.get("combined_field_name", "")).strip()
    if programs_cfg.get("also_fill_combined_field") and combined_field:
        base.append(combined_field)

    return base


def fetch_schema_from_api(username: str, api_key: str, base_url: str) -> tuple[Schema, dict[str, Any]]:
    creds = SmartEmailingCredentials(
        username=str(username).strip(),
        api_key=str(api_key).strip(),
        base_url=str(base_url).strip() or DEFAULT_BASE_URL,
    )
    if not creds.username or not creds.api_key:
        raise ValueError("Vyplň SmartEmailing uživatelské jméno i API klíč.")

    client = SmartEmailingApiClient(creds)
    ping = client.ping()
    api_cfg = CFG.get("smartemailing", {}).get("api", {})
    custom_fields_endpoint_candidates = [
        str(x).strip()
        for x in api_cfg.get("custom_fields_endpoint_candidates", [])
        if str(x).strip()
    ]
    custom_fields_search_endpoint_candidates = [
        str(x).strip()
        for x in api_cfg.get(
            "custom_fields_search_endpoint_candidates",
            [],
        )
        if str(x).strip()
    ]
    custom_fields = client.fetch_custom_field_names(
        endpoint_candidates=custom_fields_endpoint_candidates,
        search_endpoint_candidates=custom_fields_search_endpoint_candidates,
    )
    min_custom_fields = 1
    try:
        min_custom_fields = int(api_cfg.get("required_min_custom_fields", 1))
    except Exception:
        min_custom_fields = 1
    if min_custom_fields > 0 and len(custom_fields) < min_custom_fields:
        raise SmartEmailingApiError(
            f"API vrátilo jen {len(custom_fields)} vlastních polí, minimum je {min_custom_fields}."
        )
    columns = combine_schema_columns(build_system_schema_columns(CFG), custom_fields)
    if not columns:
        raise SmartEmailingApiError("SmartEmailing API nevrátilo žádné použitelné názvy polí.")

    schema = Schema(columns=columns, columns_set=set(columns))
    meta = {
        "source_kind": "smartemailing_api",
        "api_base_url": client.base_url,
        "ping_status": str(ping.get("status", "")) if isinstance(ping, dict) else "",
        "custom_field_count": len(custom_fields),
    }
    return schema, meta


def to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def api_issues_to_report_df(issues: list[dict[str, Any]]) -> pd.DataFrame:
    if not issues:
        return pd.DataFrame(
            columns=["type", "row_index", "detail", "email_raw", "company", "source_file", "source_row_index"]
        )

    return pd.DataFrame(
        {
            "type": "api_payload_issue",
            "row_index": [str(x.get("row_index", "")) for x in issues],
            "detail": [f"{x.get('issue', '')}: {x.get('detail', '')}".strip(": ") for x in issues],
            "email_raw": "",
            "company": "",
            "source_file": "",
            "source_row_index": "",
        }
    )


st.sidebar.header("Nastavení")
do_split_emails = st.sidebar.checkbox("Rozdělit více emailů na více řádků", value=True)
do_split_names = st.sidebar.checkbox("Rozdělit jména (tituly/jméno/příjmení)", value=True)
do_bucket_country = st.sidebar.checkbox("Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)", value=True)
if "output_encoding" not in st.session_state:
    st.session_state["output_encoding"] = "cp1250"
output_encoding = st.sidebar.selectbox(
    "Kódování výstupních CSV",
    options=["cp1250", "utf-8", "utf-8-sig"],
    key="output_encoding",
)
dedup_label = st.sidebar.selectbox(
    "Deduplikace emailů ve výstupu",
    options=["Bez deduplikace", "Ponechat první výskyt", "Ponechat poslední výskyt"],
    index=0,
)
dedup_keep = {
    "Bez deduplikace": "none",
    "Ponechat první výskyt": "first",
    "Ponechat poslední výskyt": "last",
}[dedup_label]

saved_api_credentials = load_saved_api_credentials(API_CREDENTIALS_PATH)
remember_api_credentials = st.sidebar.checkbox(
    "Pamatovat SE API údaje lokálně",
    value=bool(saved_api_credentials.get("username", "") or saved_api_credentials.get("api_key", "")),
)
st.sidebar.caption(f"Lokální soubor: `{API_CREDENTIALS_PATH}` (bez šifrování)")
if st.sidebar.button("Smazat uložené SE API údaje"):
    try:
        clear_api_credentials(API_CREDENTIALS_PATH)
        st.sidebar.success("Uložené SE API údaje byly smazány.")
        st.rerun()
    except Exception as exc:
        st.sidebar.error(f"Nepodařilo se smazat uložené údaje: {exc}")

st.markdown("### 1) Nahraj zdrojové CSV soubory (1 nebo více)")
source_files = st.file_uploader("Zdrojové CSV", type=["csv"], accept_multiple_files=True)

st.markdown("### 2) Schéma sloupců (CSV záloha)")
cached_schema, cached_schema_meta = load_cached_schema(SCHEMA_CACHE_PATH)
use_cached_schema = st.checkbox(
    "Použít uložené schéma z CSV zálohy (bez nahrávání exportu)",
    value=(cached_schema is not None),
    disabled=(cached_schema is None),
    help="Použije se jen jako záloha. Pokud je aktivní API schéma, má přednost.",
)
export_file = None
with st.expander("Detaily CSV záložního schématu", expanded=False):
    if cached_schema is None:
        st.caption("Uložené CSV schéma zatím neexistuje.")
    else:
        st.caption(f"Uložené CSV schéma: {len(cached_schema.columns)} sloupců (`{SCHEMA_CACHE_PATH}`)")
        st.caption(
            "Metadata: "
            f"verze={cached_schema_meta.get('version', '?')}, "
            f"uloženo={cached_schema_meta.get('saved_at', '')}, "
            f"zdroj={cached_schema_meta.get('source_file', '')}"
        )
        if st.button("Smazat uložené CSV schéma"):
            try:
                if SCHEMA_CACHE_PATH.exists():
                    SCHEMA_CACHE_PATH.unlink()
                st.success("Uložené CSV schéma bylo smazáno.")
                st.rerun()
            except Exception as exc:
                st.error(f"Nepodařilo se smazat uložené CSV schéma: {exc}")

    export_file = st.file_uploader(
        "Volitelně nahraj SmartEmailing export CSV (záložní schéma pro tento běh)",
        type=["csv"],
        accept_multiple_files=False,
    )

csv_schema = None
if export_file is not None:
    try:
        rr = read_csv_best_effort(export_file.getvalue())
        export_df = clean_columns(rr.df)
        csv_schema = schema_from_export_df(export_df)
        st.success(
            f"Načteno CSV schéma z nahraného souboru: {len(csv_schema.columns)} sloupců "
            f"(oddělovač '{rr.delimiter}', kódování '{rr.encoding}')"
        )
        save_uploaded_schema = st.checkbox("Uložit nahrané CSV schéma jako výchozí pro příště", value=True)
        if save_uploaded_schema:
            try:
                save_cached_schema(
                    SCHEMA_CACHE_PATH,
                    csv_schema,
                    source_file=export_file.name,
                    source_kind="csv_upload",
                )
                st.caption(f"CSV schéma uloženo do `{SCHEMA_CACHE_PATH}`.")
            except Exception as exc:
                st.error(f"Nepodařilo se uložit CSV schéma: {exc}")
    except Exception as exc:
        st.error(f"Nepodařilo se načíst exportní CSV pro záložní schéma: {exc}")

if csv_schema is None and use_cached_schema and cached_schema is not None:
    csv_schema = cached_schema
    st.success(f"Použito uložené CSV záložní schéma: {len(csv_schema.columns)} sloupců")

schema = csv_schema
schema_origin = "csv_fallback" if csv_schema is not None else "none"

schema_api_cfg = CFG.get("smartemailing", {}).get("api", {})
required_min_custom_fields = to_int(schema_api_cfg.get("required_min_custom_fields", 1), 1)
custom_fields_endpoint_candidates = [
    str(x).strip()
    for x in schema_api_cfg.get("custom_fields_endpoint_candidates", ["/api/v3/customfields", "/api/v3/custom-fields"])
    if str(x).strip()
]
custom_fields_search_endpoint_candidates = [
    str(x).strip()
    for x in schema_api_cfg.get(
        "custom_fields_search_endpoint_candidates",
        [],
    )
    if str(x).strip()
]

st.markdown("### 2b) Schéma ze SmartEmailing API (doporučeno)")
cached_api_schema, cached_api_schema_meta = load_cached_schema(API_SCHEMA_CACHE_PATH)
use_api_schema = st.checkbox(
    "Načítat schéma přímo ze SmartEmailing API",
    value=True,
)
api_username = ""
api_key = ""
api_base_url = str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL
refresh_api_schema_on_generate = True
use_api_cache_on_error = True
api_schema_for_run = None
api_schema_fetch_attempted = False
api_schema_fetch_error = ""
api_schema_from_cache_preview = False

if use_api_schema:
    with st.expander("Detaily API schématu", expanded=False):
        api_username = st.text_input(
            "SmartEmailing uživatelské jméno",
            value=str(saved_api_credentials.get("username", "")).strip(),
            key="schema_api_username",
        )
        api_key = st.text_input(
            "SmartEmailing API klíč",
            value=str(saved_api_credentials.get("api_key", "")).strip(),
            type="password",
            key="schema_api_key",
        )
        api_base_url = st.text_input(
            "API základní URL",
            value=str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL,
            key="schema_api_base_url",
        )
        save_schema_api_credentials = st.button("Uložit API údaje na disk", key="save_api_credentials_schema")
        refresh_api_schema_on_generate = st.checkbox("Před exportem načíst schéma z API znovu", value=True)
        use_api_cache_on_error = st.checkbox("Při chybě API použít schéma z API mezipaměti", value=True)

        if save_schema_api_credentials:
            if str(api_username).strip() and str(api_key).strip():
                try:
                    save_api_credentials(API_CREDENTIALS_PATH, api_username, api_key, api_base_url)
                    st.success(f"API údaje byly uloženy do `{API_CREDENTIALS_PATH}`.")
                except Exception as exc:
                    st.error(f"Nepodařilo se uložit API údaje: {exc}")
            else:
                st.error("Pro uložení vyplň uživatelské jméno i API klíč.")

        if remember_api_credentials and str(api_username).strip() and str(api_key).strip():
            try:
                save_api_credentials(API_CREDENTIALS_PATH, api_username, api_key, api_base_url)
            except Exception as exc:
                st.warning(f"Nepodařilo se uložit API údaje lokálně: {exc}")

        if cached_api_schema is None:
            st.caption("Uložená API mezipaměť schématu zatím neexistuje.")
        else:
            st.caption(f"Uložené API schéma: {len(cached_api_schema.columns)} sloupců (`{API_SCHEMA_CACHE_PATH}`)")
            st.caption(
                "Metadata: "
                f"verze={cached_api_schema_meta.get('version', '?')}, "
                f"uloženo={cached_api_schema_meta.get('saved_at', '')}, "
                f"zdroj={cached_api_schema_meta.get('source_file', '')}, "
                f"vlastní_pole={cached_api_schema_meta.get('custom_field_count', '?')}"
            )

        c1, c2, c3 = st.columns(3)
        with c1:
            test_ping = st.button("Test API (ping)")
        with c2:
            fetch_api_schema_now = st.button("Načíst schéma z API teď")
        with c3:
            clear_api_cache = st.button("Smazat API mezipaměť schématu")
        probe_custom_fields_now = st.button("Diagnostika endpointů vlastních polí")

        if clear_api_cache:
            try:
                if API_SCHEMA_CACHE_PATH.exists():
                    API_SCHEMA_CACHE_PATH.unlink()
                st.success("API mezipaměť schématu byla smazána.")
                st.rerun()
            except Exception as exc:
                st.error(f"Nepodařilo se smazat API mezipaměť schématu: {exc}")

        if test_ping:
            try:
                ping_client = SmartEmailingApiClient(
                    SmartEmailingCredentials(
                        username=str(api_username).strip(),
                        api_key=str(api_key).strip(),
                        base_url=str(api_base_url).strip() or DEFAULT_BASE_URL,
                    )
                )
                ping_data = ping_client.ping()
                st.success(f"API ping OK: {ping_data}")
            except Exception as exc:
                st.error(f"API ping selhal: {exc}")

        if fetch_api_schema_now:
            api_schema_fetch_attempted = True
            try:
                api_schema_for_run, api_meta = fetch_schema_from_api(api_username, api_key, api_base_url)
                save_cached_schema(
                    API_SCHEMA_CACHE_PATH,
                    api_schema_for_run,
                    source_file="smartemailing_api",
                    source_kind="api_live",
                    extra_meta=api_meta,
                )
                st.success(
                    f"Načteno API schéma: {len(api_schema_for_run.columns)} sloupců "
                    f"(vlastní pole: {api_meta.get('custom_field_count', 0)})"
                )
            except Exception as exc:
                api_schema_fetch_error = str(exc)
                st.error(f"Nepodařilo se načíst schéma z API: {exc}")

        if probe_custom_fields_now:
            try:
                probe_client = SmartEmailingApiClient(
                    SmartEmailingCredentials(
                        username=str(api_username).strip(),
                        api_key=str(api_key).strip(),
                        base_url=str(api_base_url).strip() or DEFAULT_BASE_URL,
                    )
                )
                probe_rows = probe_client.probe_custom_fields_endpoints(
                    endpoint_candidates=custom_fields_endpoint_candidates,
                    search_endpoint_candidates=custom_fields_search_endpoint_candidates,
                )
                st.markdown("#### Diagnostika endpointů vlastních polí")
                st.dataframe(pd.DataFrame(probe_rows), use_container_width=True)
            except Exception as exc:
                st.error(f"Nepodařilo se provést diagnostiku endpointů vlastních polí: {exc}")

        # Pro běžný náhled použij naposledy načtené API schéma (pokud je validní).
        # Při spuštění se stejně načte čerstvé, pokud je zapnuté refresh_api_schema_on_generate.
        if api_schema_for_run is None and cached_api_schema is not None:
            cached_custom_fields = to_int(cached_api_schema_meta.get("custom_field_count", 0), 0)
            if cached_custom_fields >= required_min_custom_fields:
                api_schema_for_run = cached_api_schema
                api_schema_from_cache_preview = True
                st.caption(f"Použito uložené API schéma z mezipaměti: {len(api_schema_for_run.columns)} sloupců")
            elif api_schema_fetch_attempted:
                st.warning(
                    "API mezipaměť má příliš málo vlastních polí "
                    f"({cached_custom_fields} < {required_min_custom_fields}), ignoruji ji."
                )

if api_schema_for_run is not None:
    schema = api_schema_for_run
    schema_origin = "smartemailing_api_cache" if api_schema_from_cache_preview else "smartemailing_api"
elif use_api_schema and schema is not None:
    if api_schema_fetch_attempted and api_schema_fetch_error:
        st.warning(
            "Schéma z API se nepodařilo načíst, pokračuje se s CSV záložním schématem. "
            f"Detail: {api_schema_fetch_error}"
        )
    elif not refresh_api_schema_on_generate:
        st.info(
            "Aktivní je CSV záložní schéma. Pro přepnutí na API schéma klikni na "
            "`Načíst schéma z API teď`."
        )

if schema is not None:
    schema_origin_label = {
        "csv_fallback": "CSV záloha",
        "smartemailing_api": "SmartEmailing API",
        "smartemailing_api_cache": "SmartEmailing API (mezipaměť)",
        "none": "bez schématu",
    }.get(schema_origin, str(schema_origin))
    st.caption(f"Aktivní schéma: {len(schema.columns)} sloupců (`{schema_origin_label}`)")
else:
    st.warning("Není načtené žádné schéma. Nahraj export CSV nebo zapni načítání schématu z API.")

st.markdown("### 3) Režim běhu")
execution_mode_label = st.radio(
    "Vyber akci po transformaci dat",
    options=[
        "CSV export (záloha)",
        "API kontrolní běh (jen validace + náhled)",
        "API bezpečný import (staging + testovací dávka)",
        "API plný import (schvalování + testovací dávka)",
    ],
    index=0,
)
execution_mode = {
    "CSV export (záloha)": "csv_fallback",
    "API kontrolní běh (jen validace + náhled)": "api_dry_run",
    "API bezpečný import (staging + testovací dávka)": "api_safe_import",
    "API plný import (schvalování + testovací dávka)": "api_full_import",
}[execution_mode_label]

api_cfg = CFG.get("smartemailing", {}).get("api", {})
api_mode_enabled = execution_mode != "csv_fallback"
import_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("import_endpoint_candidates", ["/api/v3/import", "/api/v3/imports", "/api/v3/import-contacts"])
    if str(x).strip()
]
custom_fields_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("custom_fields_endpoint_candidates", ["/api/v3/customfields", "/api/v3/custom-fields"])
    if str(x).strip()
]
custom_fields_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get(
        "custom_fields_search_endpoint_candidates",
        [],
    )
    if str(x).strip()
]
contact_lists_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contact_lists_endpoint_candidates", ["/api/v3/contactlists", "/api/v3/contact-lists"])
    if str(x).strip()
]
contact_lists_search_endpoint_candidates = [
    str(x).strip()
    for x in api_cfg.get("contact_lists_search_endpoint_candidates", ["/api/v3/contactlists/search", "/api/v3/contact-lists/search"])
    if str(x).strip()
]
strict_custom_fields = bool(api_cfg.get("strict_custom_fields", True))
list_status = str(api_cfg.get("list_status", "confirmed")).strip() or "confirmed"

api_import_username = ""
api_import_key = ""
api_import_base_url = DEFAULT_BASE_URL
staging_list_value = ""
staging_tag = ""
api_canary_size = to_int(api_cfg.get("canary_size", 50), 50)
api_batch_size = to_int(api_cfg.get("batch_size", 500), 500)
api_max_contacts_safe = to_int(api_cfg.get("max_contacts_safe", 2000), 2000)
api_max_contacts_full = to_int(api_cfg.get("max_contacts_full", 10000), 10000)
safe_confirm = False
safe_phrase_input = ""
full_confirm = False
full_phrase_input = ""
full_operator = ""
full_approver = ""
full_second_approval_input = ""

if api_mode_enabled:
    st.markdown("### 4) Import do SmartEmailingu přes API")
    if "api_contact_lists_cache" not in st.session_state:
        st.session_state.api_contact_lists_cache = []
    if "full_import_approval_code" not in st.session_state:
        st.session_state.full_import_approval_code = hashlib.sha1(
            datetime.now(timezone.utc).isoformat().encode("utf-8")
        ).hexdigest()[:8].upper()

    api_import_username = st.text_input(
        "API uživatelské jméno (pro import)",
        value=(
            str(api_username).strip()
            if str(api_username).strip()
            else str(saved_api_credentials.get("username", "")).strip()
        ),
        key="import_api_username",
    )
    api_import_key = st.text_input(
        "API klíč (pro import)",
        value=(
            str(api_key).strip()
            if str(api_key).strip()
            else str(saved_api_credentials.get("api_key", "")).strip()
        ),
        type="password",
        key="import_api_key",
    )
    api_import_base_url = st.text_input(
        "API základní URL (pro import)",
        value=(
            str(api_base_url).strip()
            if str(api_base_url).strip()
            else str(saved_api_credentials.get("base_url", DEFAULT_BASE_URL)).strip() or DEFAULT_BASE_URL
        ),
        key="import_api_base_url",
    )
    save_import_api_credentials = st.button("Uložit API údaje na disk", key="save_api_credentials_import")

    if save_import_api_credentials:
        if str(api_import_username).strip() and str(api_import_key).strip():
            try:
                save_api_credentials(API_CREDENTIALS_PATH, api_import_username, api_import_key, api_import_base_url)
                st.success(f"API údaje byly uloženy do `{API_CREDENTIALS_PATH}`.")
            except Exception as exc:
                st.error(f"Nepodařilo se uložit API údaje: {exc}")
        else:
            st.error("Pro uložení vyplň uživatelské jméno i API klíč.")

    if remember_api_credentials and str(api_import_username).strip() and str(api_import_key).strip():
        try:
            save_api_credentials(API_CREDENTIALS_PATH, api_import_username, api_import_key, api_import_base_url)
        except Exception as exc:
            st.warning(f"Nepodařilo se uložit API údaje lokálně: {exc}")

    if "api_contact_lists_cache_meta" not in st.session_state:
        st.session_state.api_contact_lists_cache_meta = {}
    if "staging_list_manual" not in st.session_state:
        st.session_state.staging_list_manual = ""
    if "staging_list_select" not in st.session_state:
        st.session_state.staging_list_select = "(ručně)"

    auto_load_lists = st.checkbox(
        "Načítat listy z API automaticky po vyplnění přihlášení",
        value=True,
        key="auto_load_api_lists",
    )
    load_lists = st.button("Obnovit listy z API")

    credentials_ready_for_lists = bool(str(api_import_username).strip()) and bool(str(api_import_key).strip())
    lists_fingerprint = hashlib.sha256(
        (
            f"{str(api_import_username).strip()}|"
            f"{str(api_import_key).strip()}|"
            f"{str(api_import_base_url).strip() or DEFAULT_BASE_URL}"
        ).encode("utf-8")
    ).hexdigest()
    cached_fingerprint = str(st.session_state.get("api_contact_lists_cache_meta", {}).get("fingerprint", ""))
    should_auto_load = auto_load_lists and credentials_ready_for_lists and cached_fingerprint != lists_fingerprint

    if (load_lists or should_auto_load) and credentials_ready_for_lists:
        try:
            list_client = SmartEmailingApiClient(
                SmartEmailingCredentials(
                    username=str(api_import_username).strip(),
                    api_key=str(api_import_key).strip(),
                    base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                )
            )
            fetched_lists = list_client.fetch_contact_lists(
                endpoint_candidates=contact_lists_endpoint_candidates,
                search_endpoint_candidates=contact_lists_search_endpoint_candidates,
            )
            st.session_state.api_contact_lists_cache = fetched_lists
            st.session_state.api_contact_lists_cache_meta = {
                "fingerprint": lists_fingerprint,
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }
            if load_lists:
                st.success(f"Načteno listů: {len(fetched_lists)}")
        except Exception as exc:
            st.error(f"Nepodařilo se načíst listy: {exc}")

    lists_cache = st.session_state.get("api_contact_lists_cache", [])
    if lists_cache:
        label_to_list: dict[str, dict[str, str]] = {}
        labels = []
        for item in lists_cache:
            list_id = str(item.get("id", "")).strip()
            list_name = str(item.get("name", "")).strip() or "(bez názvu)"
            label = f"{list_name} (id={list_id})"
            labels.append(label)
            label_to_list[label] = {"id": list_id, "name": list_name}

        list_options = ["(ručně)"] + labels
        if st.session_state.get("staging_list_select") not in list_options:
            st.session_state["staging_list_select"] = "(ručně)"
        selected_list_label = st.selectbox(
            "Vyber staging seznam ze SmartEmailingu",
            options=list_options,
            key="staging_list_select",
        )
        if selected_list_label != "(ručně)":
            selected = label_to_list.get(selected_list_label, {"id": "", "name": ""})
            selected_id = str(selected.get("id", "")).strip()
            selected_name = str(selected.get("name", "")).strip()
            if selected_id:
                st.session_state["staging_list_manual"] = selected_id
                st.caption(f"Vybraný staging seznam: `{selected_name}` (id `{selected_id}`)")
    else:
        st.caption("Listy zatím nejsou načtené. Vyplň API údaje a klikni na `Obnovit listy z API`.")

    staging_list_value = st.text_input(
        "Staging seznam ID nebo název (bezpečný/plný režim)",
        key="staging_list_manual",
        help="Doporučeno: použít staging seznam, ne produkční seznam.",
    )
    staging_tag = st.text_input(
        "Staging štítek (volitelný)",
        value="ITF_IMPORT_STAGING",
        help="Některé účty podporují tagy v importních datech.",
    )

    api_canary_size = int(
        st.number_input(
            "Velikost testovací dávky (první dávka)",
            min_value=0,
            value=max(0, api_canary_size),
            step=10,
        )
    )
    api_batch_size = int(
        st.number_input(
            "Velikost dávky",
            min_value=1,
            value=max(1, api_batch_size),
            step=100,
        )
    )

    if execution_mode == "api_safe_import":
        safe_phrase_required = str(
            api_cfg.get("required_confirmation_phrase_safe", "SAFE IMPORT DO SMARTEMAILINGU")
        )
        safe_confirm = st.checkbox(
            "Rozumím dopadu: bezpečný import běží jen jako přidání/aktualizace, bez mazání.",
            value=False,
        )
        safe_phrase_input = st.text_input(f"Opiš potvrzovací frázi: {safe_phrase_required}", value="")

    if execution_mode == "api_full_import":
        full_phrase_required = str(
            api_cfg.get("required_confirmation_phrase_full", "FULL IMPORT DO SMARTEMAILINGU")
        )
        full_confirm = st.checkbox(
            "Rozumím dopadu: plný import může přepsat více polí dle pravidel.",
            value=False,
        )
        full_phrase_input = st.text_input(f"Opiš potvrzovací frázi: {full_phrase_required}", value="")
        full_operator = st.text_input("Operátor (kdo import spouští)", value="")
        full_approver = st.text_input("Schvalovatel (4 oči)", value="")
        approval_code = st.session_state.full_import_approval_code
        full_second_approval_input = st.text_input(
            f"Schvalovací kód (4 oči): {approval_code}",
            value="",
        )
        api_max_contacts_full = int(
            st.number_input(
                "Limit kontaktů pro plný import",
                min_value=1,
                value=max(1, api_max_contacts_full),
                step=100,
            )
        )

can_load_schema_during_generate = use_api_schema and bool(str(api_username).strip()) and bool(str(api_key).strip())
api_credentials_ready = bool(str(api_import_username).strip()) and bool(str(api_import_key).strip())
generate_disabled = (not source_files) or (schema is None and not can_load_schema_during_generate) or (
    api_mode_enabled and not api_credentials_ready
)

if api_mode_enabled and not api_credentials_ready:
    st.warning("Pro API režim vyplň API uživatelské jméno + API klíč.")

if st.button("Spustit zpracování", type="primary", disabled=generate_disabled):
    active_schema = schema
    run_schema_origin = schema_origin
    if use_api_schema and refresh_api_schema_on_generate:
        try:
            active_schema, api_meta = fetch_schema_from_api(api_username, api_key, api_base_url)
            run_schema_origin = "smartemailing_api"
            save_cached_schema(
                API_SCHEMA_CACHE_PATH,
                active_schema,
                source_file="smartemailing_api",
                source_kind="api_live",
                extra_meta=api_meta,
            )
            st.info(
                f"Před exportem načteno čerstvé API schéma: {len(active_schema.columns)} sloupců "
                f"(vlastní pole: {api_meta.get('custom_field_count', 0)})"
            )
        except Exception as exc:
            if use_api_cache_on_error and cached_api_schema is not None:
                cached_custom_fields = to_int(cached_api_schema_meta.get("custom_field_count", 0), 0)
                if cached_custom_fields >= required_min_custom_fields:
                    active_schema = cached_api_schema
                    run_schema_origin = "smartemailing_api_cache"
                    st.warning(f"Online obnovení API schématu selhalo ({exc}), používám API mezipaměť.")
                else:
                    st.warning(
                        "Online obnovení API schématu selhalo "
                        f"({exc}) a API mezipaměť má jen {cached_custom_fields} vlastních polí, "
                        "používám aktuální záložní schéma."
                    )
            elif active_schema is not None:
                st.warning(f"Online obnovení API schématu selhalo ({exc}), používám aktuální záložní schéma.")
            else:
                st.error(f"Online obnovení API schématu selhalo ({exc}) a není dostupné záložní schéma.")
                st.stop()

    if active_schema is None:
        st.error("Nelze pokračovat bez schématu sloupců.")
        st.stop()

    all_import_rows = []
    invalid_all = []
    unknown_all = []
    file_errors = []
    processed_files = 0
    source_rows_total = 0
    rows_after_email_processing = 0
    email_counts: dict[str, int] = {}
    email_source_files: dict[str, set[str]] = {}
    row_order_counter = 0

    for sf in source_files:
        try:
            rr = read_csv_best_effort(sf.getvalue())
            df = clean_columns(rr.df)

            # detect source and normalize
            source = detect_source(df, CFG["sources"])
            norm = normalize_df(df, source)
            norm["source_file"] = sf.name
            norm["source_row_index"] = pd.Series(range(1, len(norm) + 1), index=norm.index)
            source_rows_total += len(norm)

            # partner -> notes (optional)
            partner_company = (
                norm.get("partner_company", pd.Series([""] * len(norm), index=norm.index))
                .fillna("")
                .astype(str)
                .str.strip()
            )
            norm["notes"] = ""
            norm.loc[partner_company != "", "notes"] = "Partner: " + partner_company[partner_company != ""]

            # transforms
            if do_split_emails:
                expanded, invalid = split_emails(norm, CFG["transforms"]["email"]["split_separators"])
            else:
                expanded, invalid = validate_emails_without_split(norm)
            invalid_all.append(invalid)
            rows_after_email_processing += len(expanded)

            if do_split_names:
                expanded = apply_name_split(expanded, CFG["transforms"]["name"])
            else:
                expanded["title_before"] = ""
                expanded["first_name"] = ""
                expanded["last_name"] = ""
                expanded["title_after"] = ""

            # country bucket
            if do_bucket_country:
                expanded = apply_country_bucket(expanded, CFG["transforms"]["country_bucket"])
            else:
                expanded["country_bucket"] = "EN"

            # incremental duplicate stats (memory-friendly)
            emails = expanded.get("email", pd.Series([""] * len(expanded), index=expanded.index)).fillna("").astype(str).str.strip()
            emails = emails[emails != ""]
            if len(emails) > 0:
                vc = emails.value_counts()
                for email, count in vc.items():
                    email_counts[email] = email_counts.get(email, 0) + int(count)
                    if email not in email_source_files:
                        email_source_files[email] = set()
                    email_source_files[email].add(sf.name)

            # build SmartEmailing import
            import_df, unknown = build_import_df(expanded, active_schema, CFG)
            unknown_all.append(unknown)
            import_df["country_bucket"] = expanded.get("country_bucket", pd.Series(["EN"] * len(expanded), index=expanded.index)).tolist()
            import_df["__row_order"] = pd.Series(range(row_order_counter, row_order_counter + len(import_df)), index=import_df.index)
            row_order_counter += len(import_df)
            all_import_rows.append(import_df)

            processed_files += 1
            st.info(f"{sf.name}: detekováno jako **{source.name}**, řádků po transformacích: {len(expanded)}")
        except Exception as exc:
            file_errors.append({"source_file": sf.name, "error": str(exc)})
            st.error(f"{sf.name}: nepodařilo se zpracovat ({exc})")
            continue

    if all_import_rows:
        final_import_df = pd.concat(all_import_rows, ignore_index=True)
    else:
        final_import_df = pd.DataFrame(columns=active_schema.columns + ["country_bucket", "__row_order"])

    field_map = CFG.get("smartemailing", {}).get("field_map", {})
    email_export_column = next((se_col for se_col, internal_col in field_map.items() if internal_col == "email"), None)
    dedup_removed_rows = 0
    if dedup_keep != "none":
        if email_export_column and email_export_column in final_import_df.columns:
            final_import_df, dedup_removed_rows = deduplicate_import_df(final_import_df, email_export_column, dedup_keep)
        else:
            st.warning("Deduplikace je zapnutá, ale ve schématu nebyl nalezen emailový sloupec pro deduplikaci.")

    if len(final_import_df) > 0:
        bucket_series = final_import_df.get(
            "country_bucket",
            pd.Series(["EN"] * len(final_import_df), index=final_import_df.index),
        )
        import_only_df = final_import_df.drop(columns=["country_bucket"], errors="ignore")
        final_parts = split_by_bucket(import_only_df, bucket_series)
    else:
        final_parts = {"CZ_SK": pd.DataFrame(), "DE_AT_CH": pd.DataFrame(), "EN": pd.DataFrame()}

    invalid_frames = [d for d in invalid_all if len(d) > 0]
    invalid_df = pd.concat(invalid_frames, ignore_index=True) if invalid_frames else pd.DataFrame()
    unknown_frames = [u for u in unknown_all if len(u) > 0]
    unknown_df = pd.concat(unknown_frames, ignore_index=True) if unknown_frames else pd.DataFrame()
    duplicates_df = find_duplicates_from_stats(email_counts, email_source_files)
    duplicate_extra_rows = int((duplicates_df["count"] - 1).clip(lower=0).sum()) if len(duplicates_df) > 0 else 0

    summary_metrics = {
        "source_files_total": len(source_files),
        "source_files_processed": processed_files,
        "source_files_failed": len(file_errors),
        "input_rows_total": source_rows_total,
        "rows_after_email_processing": rows_after_email_processing,
        "invalid_email_rows": len(invalid_df),
        "unknown_program_codes": len(unknown_df),
        "duplicate_email_keys": len(duplicates_df),
        "duplicate_extra_rows": duplicate_extra_rows,
        "dedup_mode": dedup_keep,
        "dedup_removed_rows": dedup_removed_rows,
        "output_rows_CZ_SK": len(final_parts["CZ_SK"]),
        "output_rows_DE_AT_CH": len(final_parts["DE_AT_CH"]),
        "output_rows_EN": len(final_parts["EN"]),
        "output_rows_total": len(final_parts["CZ_SK"]) + len(final_parts["DE_AT_CH"]) + len(final_parts["EN"]),
    }
    summary_metrics["execution_mode"] = execution_mode

    api_contacts: list[dict[str, Any]] = []
    api_contacts_preview: list[dict[str, Any]] = []
    api_batch_results: list[dict[str, Any]] = []
    api_issues: list[dict[str, Any]] = []
    api_error = ""
    api_status = "not_requested"
    api_resolved_list_id = ""
    api_ping = {}
    extra_report_frames: list[pd.DataFrame] = []

    if api_mode_enabled:
        try:
            client = SmartEmailingApiClient(
                SmartEmailingCredentials(
                    username=str(api_import_username).strip(),
                    api_key=str(api_import_key).strip(),
                    base_url=str(api_import_base_url).strip() or DEFAULT_BASE_URL,
                )
            )
            api_ping = client.ping()
            custom_fields = client.fetch_custom_fields(
                endpoint_candidates=custom_fields_endpoint_candidates,
                search_endpoint_candidates=custom_fields_search_endpoint_candidates,
            )
            min_custom_fields = 1
            try:
                min_custom_fields = int(api_cfg.get("required_min_custom_fields", 1))
            except Exception:
                min_custom_fields = 1
            if min_custom_fields > 0 and len(custom_fields) < min_custom_fields:
                raise SmartEmailingApiError(
                    f"API vrátilo jen {len(custom_fields)} vlastních polí, minimum je {min_custom_fields}."
                )

            api_resolved_list_id = (
                client.resolve_contact_list_id(
                    staging_list_value,
                    endpoint_candidates=contact_lists_endpoint_candidates,
                    search_endpoint_candidates=contact_lists_search_endpoint_candidates,
                )
                if str(staging_list_value).strip()
                else ""
            )

            import_for_api = final_import_df.drop(columns=["country_bucket", "__row_order"], errors="ignore")
            api_contacts, api_issues = build_api_contacts_from_import_df(
                import_df=import_for_api,
                api_system_field_map=api_cfg.get("system_field_map", {}),
                custom_fields=custom_fields,
                list_id=api_resolved_list_id,
                list_status=list_status,
                tag=str(staging_tag).strip(),
                strict_custom_fields=strict_custom_fields,
            )

            api_contacts_preview = api_contacts[:50]
            summary_metrics["api_ping_status"] = str(api_ping.get("status", "")) if isinstance(api_ping, dict) else ""
            summary_metrics["api_custom_fields"] = len(custom_fields)
            summary_metrics["api_contacts_prepared"] = len(api_contacts)
            summary_metrics["api_payload_issues"] = len(api_issues)
            summary_metrics["api_staging_list_id"] = api_resolved_list_id
            summary_metrics["api_staging_tag"] = str(staging_tag).strip()

            if api_issues:
                extra_report_frames.append(api_issues_to_report_df(api_issues))

            if execution_mode == "api_dry_run":
                api_status = "dry_run_ok"
            else:
                max_contacts_limit = api_max_contacts_safe if execution_mode == "api_safe_import" else api_max_contacts_full
                block_reason = ""
                if len(api_contacts) == 0:
                    block_reason = "API import je blokovaný: žádné kontakty k odeslání."
                elif len(api_contacts) > max_contacts_limit:
                    block_reason = (
                        f"API import je blokovaný: počet kontaktů ({len(api_contacts)}) "
                        f"překračuje limit režimu ({max_contacts_limit})."
                    )
                elif execution_mode == "api_safe_import":
                    safe_phrase_required = str(
                        api_cfg.get("required_confirmation_phrase_safe", "SAFE IMPORT DO SMARTEMAILINGU")
                    )
                    if not safe_confirm:
                        block_reason = "Bezpečný import je blokovaný: chybí potvrzení dopadu."
                    elif safe_phrase_input.strip() != safe_phrase_required:
                        block_reason = "Bezpečný import je blokovaný: špatná potvrzovací fráze."
                    elif not (api_resolved_list_id or str(staging_tag).strip()):
                        block_reason = "Bezpečný import je blokovaný: vyplň staging seznam nebo staging štítek."
                elif execution_mode == "api_full_import":
                    full_phrase_required = str(
                        api_cfg.get("required_confirmation_phrase_full", "FULL IMPORT DO SMARTEMAILINGU")
                    )
                    approval_code = str(st.session_state.get("full_import_approval_code", "")).strip()
                    if not full_confirm:
                        block_reason = "Plný import je blokovaný: chybí potvrzení dopadu."
                    elif full_phrase_input.strip() != full_phrase_required:
                        block_reason = "Plný import je blokovaný: špatná potvrzovací fráze."
                    elif not full_operator.strip() or not full_approver.strip():
                        block_reason = "Plný import je blokovaný: vyplň operátora i schvalovatele."
                    elif full_operator.strip().casefold() == full_approver.strip().casefold():
                        block_reason = "Plný import je blokovaný: operátor a schvalovatel musí být různé osoby (4 oči)."
                    elif full_second_approval_input.strip() != approval_code:
                        block_reason = "Plný import je blokovaný: neplatný schvalovací kód (4 oči)."
                    elif not (api_resolved_list_id or str(staging_tag).strip()):
                        block_reason = "Plný import je blokovaný: vyplň staging seznam nebo staging štítek."

                if block_reason:
                    api_status = "blocked"
                    extra_report_frames.append(
                        pd.DataFrame(
                            {
                                "type": "api_blocked",
                                "row_index": "",
                                "detail": block_reason,
                                "email_raw": "",
                                "company": "",
                                "source_file": "",
                                "source_row_index": "",
                            },
                            index=[0],
                        )
                    )
                    st.error(block_reason)
                else:
                    batch_results = client.import_contacts_canary(
                        contacts=api_contacts,
                        canary_size=api_canary_size,
                        batch_size=api_batch_size,
                        update_existing=True,
                        skip_invalid_contacts=True,
                        endpoint_candidates=import_endpoint_candidates,
                    )
                    api_batch_results = [
                        {
                            "endpoint": x.endpoint,
                            "payload_variant": x.payload_variant,
                            "sent_contacts": x.sent_contacts,
                            "batch_index": x.batch_index,
                            "canary": x.canary,
                            "started_at": x.started_at,
                            "finished_at": x.finished_at,
                            "response": x.response,
                        }
                        for x in batch_results
                    ]
                    api_status = "import_ok"
                    summary_metrics["api_batches"] = len(api_batch_results)
                    summary_metrics["api_contacts_sent"] = int(sum(x["sent_contacts"] for x in api_batch_results))

        except Exception as exc:
            api_status = "failed"
            api_error = str(exc)
            summary_metrics["api_error"] = api_error
            extra_report_frames.append(
                pd.DataFrame(
                    {
                        "type": "api_error",
                        "row_index": "",
                        "detail": api_error,
                        "email_raw": "",
                        "company": "",
                        "source_file": "",
                        "source_row_index": "",
                    },
                    index=[0],
                )
            )
            st.error(f"API část selhala: {exc}")

    summary_metrics["api_status"] = api_status

    report_df = build_report(invalid_df, unknown_df, duplicates_df, summary_metrics=summary_metrics)
    if file_errors:
        extra_report_frames.append(
            pd.DataFrame(
                {
                    "type": "source_file_error",
                    "row_index": "",
                    "detail": [x["error"] for x in file_errors],
                    "email_raw": "",
                    "company": "",
                    "source_file": [x["source_file"] for x in file_errors],
                    "source_row_index": "",
                }
            )
        )
    for frame in extra_report_frames:
        report_df = pd.concat([report_df, frame], ignore_index=True)

    if processed_files == 0:
        st.warning("Nepodařilo se úspěšně zpracovat žádný zdrojový soubor. Zkontroluj report.")

    # build ZIP
    zip_buf = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for k, part_df in final_parts.items():
                out_name = f"import_{k}.csv"
                export_df = drop_empty_columns(part_df.drop(columns=["__row_order"], errors="ignore"))
                csv_bytes = dataframe_to_csv_bytes(export_df, sep=";", encoding=output_encoding)
                zf.writestr(out_name, csv_bytes)

            zf.writestr("report.csv", dataframe_to_csv_bytes(report_df, sep=";", encoding=output_encoding))
            if api_mode_enabled:
                zf.writestr(
                    "api_contacts_preview.json",
                    json.dumps(api_contacts_preview, ensure_ascii=False, indent=2).encode("utf-8"),
                )
                zf.writestr(
                    "api_batch_results.json",
                    json.dumps(api_batch_results, ensure_ascii=False, indent=2).encode("utf-8"),
                )
    except UnicodeEncodeError as exc:
        st.error(f"Vybrané kódování '{output_encoding}' neumí některé znaky v datech: {exc}")
        st.stop()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.success("Hotovo. Stáhni ZIP se soubory pro import.")
    st.download_button(
        "Stáhnout ZIP",
        data=zip_buf.getvalue(),
        file_name="smartemailing_import.zip",
        mime="application/zip",
    )
    st.markdown("### Přehled (náhled)")
    st.dataframe(report_df, use_container_width=True)

    if api_mode_enabled:
        st.markdown("### API výstup")
        api_summary = {
            "stav": api_status,
            "pripravenych_kontaktu": len(api_contacts),
            "odeslanych_kontaktu": int(sum(x.get("sent_contacts", 0) for x in api_batch_results)) if api_batch_results else 0,
            "davky": len(api_batch_results),
            "staging_list_id": api_resolved_list_id,
            "staging_tag": str(staging_tag).strip(),
            "api_chyba": api_error,
        }
        st.json(api_summary)
        st.markdown("#### Výsledky testovací dávky a dalších dávek")
        st.dataframe(pd.DataFrame(api_batch_results), use_container_width=True)
        st.markdown("#### Náhled kontrolního běhu (prvních 50 kontaktů)")
        st.json(api_contacts_preview)

    try:
        append_job_history(
            {
                "mode": execution_mode,
                "status": api_status if api_mode_enabled else "csv_export_ok",
                "schema_origin": run_schema_origin,
                "source_files_total": len(source_files),
                "source_files_processed": processed_files,
                "source_files_failed": len(file_errors),
                "output_rows_total": summary_metrics.get("output_rows_total", 0),
                "api_contacts_prepared": len(api_contacts),
                "api_contacts_sent": int(sum(x.get("sent_contacts", 0) for x in api_batch_results))
                if api_batch_results
                else 0,
                "api_batches": len(api_batch_results),
                "api_canary_size": api_canary_size if api_mode_enabled else 0,
                "api_batch_size": api_batch_size if api_mode_enabled else 0,
                "api_staging_list_id": api_resolved_list_id,
                "api_staging_tag": str(staging_tag).strip(),
                "error": api_error,
            }
        )
    except Exception as exc:
        st.warning(f"Nepodařilo se uložit historii běhů: {exc}")

st.markdown("### Historie běhů")
history_rows = load_job_history(limit=50)
history_alerts = summarize_job_alerts(history_rows)
if history_alerts["recent_failures"] >= 3:
    st.error("Upozornění: posledních 10 běhů obsahuje 3+ selhání.")
elif history_alerts["failure_rate"] >= 0.3 and history_alerts["total"] >= 5:
    st.warning("Upozornění: míra selhání v historii je >= 30 %. Zkontroluj API konfiguraci a data.")
else:
    st.caption("Historie běhů bez kritického alertu.")

if history_rows:
    st.dataframe(pd.DataFrame(history_rows), use_container_width=True)
else:
    st.caption("Historie běhů je zatím prázdná.")
