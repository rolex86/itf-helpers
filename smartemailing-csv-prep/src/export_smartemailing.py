from __future__ import annotations

from typing import Dict, Any, Tuple

import pandas as pd

from .schema import Schema
from .transforms import parse_program_codes


def build_import_df(
    df: pd.DataFrame,
    schema: Schema,
    mappings_cfg: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Builds SmartEmailing import DataFrame strictly limited to schema columns.
    Returns: (import_df, unknown_program_codes_report)
    """
    se_cfg = mappings_cfg["smartemailing"]
    field_map: Dict[str, str] = se_cfg.get("field_map", {})
    prog_cfg: Dict[str, Any] = se_cfg.get("programs", {})

    # Prepare empty frame with all schema columns (SmartEmailing expects headers)
    out = pd.DataFrame({c: [""] * len(df) for c in schema.columns})

    # Fill mapped fields if they exist in schema
    for se_col, internal_col in field_map.items():
        if se_col in schema.columns_set and internal_col in df.columns:
            out[se_col] = df[internal_col].fillna("").astype(str)

    unknown = []
    known_cols = schema.columns_set

    # Programs: one column per code (only if that code column exists in schema)
    if prog_cfg.get("mode") == "one_column_per_code":
        fill_value_tpl = prog_cfg.get("fill_value", "{code}")
        combined_enabled = bool(prog_cfg.get("also_fill_combined_field", False))
        combined_col = prog_cfg.get("combined_field_name", "")
        combined_sep = prog_cfg.get("combined_field_separator", ",")

        combined_values = []

        for i, programs_raw in enumerate(df.get("programs_raw", pd.Series([""] * len(df))).astype(str).tolist()):
            row = df.iloc[i]
            codes = sorted(list(parse_program_codes(programs_raw)))
            for code in codes:
                if code in known_cols:
                    out.at[i, code] = fill_value_tpl.format(code=code)
                else:
                    unknown.append(
                        {
                            "row_index": i,
                            "source_row_index": row.get("source_row_index", ""),
                            "source_file": row.get("source_file", ""),
                            "unknown_code": code,
                        }
                    )
            if combined_enabled and combined_col and combined_col in known_cols:
                combined_values.append(combined_sep.join(codes))
            else:
                combined_values.append("")

        if combined_enabled and combined_col and combined_col in known_cols:
            out[combined_col] = combined_values

    unknown_df = (
        pd.DataFrame(unknown)
        if unknown
        else pd.DataFrame(columns=["row_index", "source_row_index", "source_file", "unknown_code"])
    )
    return out, unknown_df


def split_by_bucket(import_df: pd.DataFrame, bucket_series: pd.Series) -> Dict[str, pd.DataFrame]:
    """
    Split final import dataframe into CZ_SK / DE_AT_CH / EN based on bucket_series.
    """
    res: Dict[str, pd.DataFrame] = {}
    for key in ["CZ_SK", "DE_AT_CH", "EN"]:
        mask = (bucket_series == key)
        part = import_df.loc[mask].copy()
        res[key] = part
    return res


def drop_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only columns that have at least one non-empty value (after string trim).
    """
    if df.shape[1] == 0:
        return df.copy()
    if len(df) == 0:
        return pd.DataFrame(index=df.index)

    normalized = df.fillna("").astype(str).apply(lambda s: s.str.strip())
    non_empty_cols = normalized.columns[(normalized != "").any(axis=0)].tolist()
    return df.loc[:, non_empty_cols].copy()


def dataframe_to_csv_bytes(df: pd.DataFrame, sep: str = ";", encoding: str = "utf-8") -> bytes:
    """
    Serialize DataFrame to CSV bytes with explicit encoding.
    """
    csv_text = df.to_csv(index=False, sep=sep)
    try:
        return csv_text.encode(encoding)
    except LookupError as exc:
        raise ValueError(f"Neznámé kódování výstupu: {encoding}") from exc
