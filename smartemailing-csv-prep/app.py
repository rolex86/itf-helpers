from __future__ import annotations

import hashlib
import io
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
from src.transforms import (
    apply_country_bucket,
    apply_name_split,
    split_emails,
    validate_emails_without_split,
)


st.set_page_config(page_title="SmartEmailing CSV Prep", layout="wide")
st.title("SmartEmailing CSV Prep – generátor importních CSV")


CFG_PATH = Path("config/mappings.yaml")
with CFG_PATH.open("r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

SCHEMA_CACHE_PATH = Path("config/schema_cache.yaml")


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
        meta = {
            "version": data.get("version", 1),
            "saved_at": data.get("saved_at", ""),
            "source_file": data.get("source_file", ""),
            "schema_hash": data.get("schema_hash", schema_hash(columns)),
        }
        return Schema(columns=columns, columns_set=set(columns)), meta
    except Exception:
        return None, meta


def save_cached_schema(path: Path, schema: Schema, source_file: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_file": source_file,
        "schema_hash": schema_hash(schema.columns),
        "columns": schema.columns,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


st.sidebar.header("Nastavení")
do_split_emails = st.sidebar.checkbox("Rozdělit více emailů na více řádků", value=True)
do_split_names = st.sidebar.checkbox("Rozdělit jména (tituly/jméno/příjmení)", value=True)
do_bucket_country = st.sidebar.checkbox("Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)", value=True)
output_encoding = st.sidebar.selectbox(
    "Kódování výstupních CSV",
    options=["utf-8", "utf-8-sig", "cp1250"],
    index=0,
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

st.markdown("### 1) Schéma sloupců (volitelně upload exportu, jinak uložené schéma)")
cached_schema, cached_schema_meta = load_cached_schema(SCHEMA_CACHE_PATH)
use_cached_schema = st.checkbox(
    "Použít uložené schéma (bez nahrávání exportu)",
    value=(cached_schema is not None),
    disabled=(cached_schema is None),
)
if cached_schema is None:
    st.caption("Uložené schéma zatím neexistuje.")
else:
    st.caption(f"Uložené schéma: {len(cached_schema.columns)} sloupců (`{SCHEMA_CACHE_PATH}`)")
    st.caption(
        "Metadata: "
        f"verze={cached_schema_meta.get('version', '?')}, "
        f"uloženo={cached_schema_meta.get('saved_at', '')}, "
        f"zdroj={cached_schema_meta.get('source_file', '')}"
    )
    if st.button("Smazat uložené schéma"):
        try:
            if SCHEMA_CACHE_PATH.exists():
                SCHEMA_CACHE_PATH.unlink()
            st.success("Uložené schéma bylo smazáno.")
            st.rerun()
        except Exception as exc:
            st.error(f"Nepodařilo se smazat uložené schéma: {exc}")

export_file = st.file_uploader(
    "Volitelně nahraj SmartEmailing export CSV (přepíše uložené schéma pro tento běh)",
    type=["csv"],
    accept_multiple_files=False,
)

schema = None
if export_file is not None:
    try:
        rr = read_csv_best_effort(export_file.getvalue())
        export_df = clean_columns(rr.df)
        schema = schema_from_export_df(export_df)
        st.success(
            f"Načteno schéma z uploadu: {len(schema.columns)} sloupců "
            f"(delimiter '{rr.delimiter}', encoding '{rr.encoding}')"
        )
        save_uploaded_schema = st.checkbox("Uložit nahrané schéma jako výchozí pro příště", value=True)
        if save_uploaded_schema:
            try:
                save_cached_schema(SCHEMA_CACHE_PATH, schema, source_file=export_file.name)
                st.caption(f"Schéma uloženo do `{SCHEMA_CACHE_PATH}`.")
            except Exception as exc:
                st.error(f"Nepodařilo se uložit schéma: {exc}")
    except Exception as exc:
        st.error(f"Nepodařilo se načíst exportní CSV pro schéma: {exc}")

if schema is None and use_cached_schema and cached_schema is not None:
    schema = cached_schema
    st.success(f"Použito uložené schéma: {len(schema.columns)} sloupců")

st.markdown("### 2) Nahraj zdrojové CSV soubory (1 nebo více)")
source_files = st.file_uploader("Zdrojové CSV", type=["csv"], accept_multiple_files=True)

if st.button("Vygenerovat importní CSV", type="primary", disabled=(schema is None or not source_files)):
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
            import_df, unknown = build_import_df(expanded, schema, CFG)
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
        final_import_df = pd.DataFrame(columns=schema.columns + ["country_bucket", "__row_order"])

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

    invalid_df = pd.concat([d for d in invalid_all if len(d) > 0], ignore_index=True) if invalid_all else pd.DataFrame()
    unknown_df = pd.concat([u for u in unknown_all if len(u) > 0], ignore_index=True) if unknown_all else pd.DataFrame()
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

    report_df = build_report(invalid_df, unknown_df, duplicates_df, summary_metrics=summary_metrics)
    if file_errors:
        file_error_df = pd.DataFrame(
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
        report_df = pd.concat([report_df, file_error_df], ignore_index=True)

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

    st.markdown("### Report (náhled)")
    st.dataframe(report_df, use_container_width=True)
