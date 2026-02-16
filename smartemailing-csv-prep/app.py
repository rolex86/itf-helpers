from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from src.io_utils import read_csv_best_effort, clean_columns
from src.schema import Schema, schema_from_export_df
from src.normalize import detect_source, normalize_df
from src.transforms import split_emails, apply_name_split, apply_country_bucket
from src.export_smartemailing import build_import_df, split_by_bucket, drop_empty_columns, dataframe_to_csv_bytes
from src.reporting import find_duplicates_by_email, build_report


st.set_page_config(page_title="SmartEmailing CSV Prep", layout="wide")

st.title("SmartEmailing CSV Prep – generátor importních CSV")

# Load config
CFG_PATH = Path("config/mappings.yaml")
with CFG_PATH.open("r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

SCHEMA_CACHE_PATH = Path("config/schema_cache.yaml")


def load_cached_schema(path: Path) -> Schema | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        columns = [str(c).strip() for c in data.get("columns", []) if str(c).strip()]
        if not columns:
            return None
        return Schema(columns=columns, columns_set=set(columns))
    except Exception:
        return None


def save_cached_schema(path: Path, schema: Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"columns": schema.columns}, f, allow_unicode=True, sort_keys=False)

st.sidebar.header("Nastavení")
do_split_emails = st.sidebar.checkbox("Rozdělit více emailů na více řádků", value=True)
do_split_names = st.sidebar.checkbox("Rozdělit jména (tituly/jméno/příjmení)", value=True)
do_bucket_country = st.sidebar.checkbox("Rozdělit výstup podle země (CZ_SK / DE_AT_CH / EN)", value=True)
output_encoding_label = st.sidebar.selectbox(
    "Kódování výstupních CSV",
    options=["utf-8", "utf-8-sig", "cp120"],
    index=2,
)
output_encoding = "cp1250" if output_encoding_label == "cp120" else output_encoding_label

st.markdown("### 1) Schéma sloupců (volitelně upload exportu, jinak uložené schéma)")
cached_schema = load_cached_schema(SCHEMA_CACHE_PATH)
use_cached_schema = st.checkbox(
    "Použít uložené schéma (bez nahrávání exportu)",
    value=(cached_schema is not None),
    disabled=(cached_schema is None),
)
if cached_schema is None:
    st.caption("Uložené schéma zatím neexistuje.")
else:
    st.caption(f"Uložené schéma: {len(cached_schema.columns)} sloupců (`{SCHEMA_CACHE_PATH}`)")

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
        st.success(f"Načteno schéma z uploadu: {len(schema.columns)} sloupců (delimiter '{rr.delimiter}', encoding '{rr.encoding}')")
        save_uploaded_schema = st.checkbox("Uložit nahrané schéma jako výchozí pro příště", value=True)
        if save_uploaded_schema:
            try:
                save_cached_schema(SCHEMA_CACHE_PATH, schema)
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
    all_import_parts = {"CZ_SK": [], "DE_AT_CH": [], "EN": []}
    invalid_all = []
    unknown_all = []
    expanded_all = []
    file_errors = []
    processed_files = 0

    for sf in source_files:
        try:
            rr = read_csv_best_effort(sf.getvalue())
            df = clean_columns(rr.df)

            # detect source and normalize
            source = detect_source(df, CFG["sources"])
            norm = normalize_df(df, source)
            norm["source_file"] = sf.name
            norm["source_row_index"] = pd.Series(range(1, len(norm) + 1), index=norm.index)

            # partner -> notes (optional)
            partner_company = norm.get("partner_company", pd.Series([""] * len(norm), index=norm.index)).fillna("").astype(str).str.strip()
            norm["notes"] = ""
            norm.loc[partner_company != "", "notes"] = "Partner: " + partner_company[partner_company != ""]

            # transforms
            if do_split_emails:
                expanded, invalid = split_emails(norm, CFG["transforms"]["email"]["split_separators"])
            else:
                expanded = norm.copy()
                expanded["email"] = expanded.get("email_raw", "").fillna("").astype(str).str.strip()
                invalid = pd.DataFrame(columns=list(norm.columns) + ["issue", "bad_email"])
            invalid_all.append(invalid)

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

            expanded_all.append(expanded)

            # build SmartEmailing import
            import_df, unknown = build_import_df(expanded, schema, CFG)
            unknown_all.append(unknown)

            parts = split_by_bucket(import_df, expanded["country_bucket"])
            for k, part_df in parts.items():
                if len(part_df) > 0:
                    all_import_parts[k].append(part_df)

            processed_files += 1
            st.info(f"{sf.name}: detekováno jako **{source.name}**, řádků po transformacích: {len(expanded)}")
        except Exception as exc:
            file_errors.append({"source_file": sf.name, "error": str(exc)})
            st.error(f"{sf.name}: nepodařilo se zpracovat ({exc})")
            continue

    # concat parts
    final_parts = {}
    for k, dfs in all_import_parts.items():
        if dfs:
            final_parts[k] = pd.concat(dfs, ignore_index=True)
        else:
            final_parts[k] = pd.DataFrame(columns=schema.columns)

    invalid_df = pd.concat([d for d in invalid_all if len(d) > 0], ignore_index=True) if invalid_all else pd.DataFrame()
    unknown_df = pd.concat([u for u in unknown_all if len(u) > 0], ignore_index=True) if unknown_all else pd.DataFrame()
    expanded_df = pd.concat(expanded_all, ignore_index=True) if expanded_all else pd.DataFrame()
    duplicates_df = find_duplicates_by_email(expanded_df)

    report_df = build_report(invalid_df, unknown_df, duplicates_df)
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
                export_df = drop_empty_columns(part_df)
                csv_bytes = dataframe_to_csv_bytes(export_df, sep=";", encoding=output_encoding)
                zf.writestr(out_name, csv_bytes)

            zf.writestr("report.csv", dataframe_to_csv_bytes(report_df, sep=";", encoding=output_encoding))
    except UnicodeEncodeError as exc:
        st.error(f"Vybrané kódování '{output_encoding_label}' neumí některé znaky v datech: {exc}")
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
