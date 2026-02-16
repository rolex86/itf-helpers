from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Dict, Any, Optional

import pandas as pd


@dataclass(frozen=True)
class SourceSpec:
    name: str
    detect_columns: set[str]
    mapping: Dict[str, str]


def normalize_column_name(name: str) -> str:
    x = str(name).strip().casefold()
    x = unicodedata.normalize("NFKD", x)
    x = "".join(ch for ch in x if not unicodedata.combining(ch))
    return " ".join(x.split())


def detect_source(df: pd.DataFrame, sources_cfg: dict) -> SourceSpec:
    normalized_to_original: Dict[str, str] = {}
    for col in df.columns:
        normalized_to_original[normalize_column_name(col)] = col
    cols = set(normalized_to_original.keys())

    best: Optional[SourceSpec] = None
    best_score = -1

    for name, spec in sources_cfg.items():
        needed = {normalize_column_name(c) for c in spec.get("detect_columns", [])}
        score = len(needed.intersection(cols))
        # valid only if all detect columns exist
        if needed.issubset(cols) and score > best_score:
            best_score = score
            resolved_mapping: Dict[str, str] = {}
            for internal_name, source_col in spec.get("map", {}).items():
                normalized_source = normalize_column_name(source_col)
                resolved_mapping[internal_name] = normalized_to_original.get(normalized_source, source_col)
            best = SourceSpec(name=name, detect_columns=needed, mapping=resolved_mapping)

    if not best:
        raise ValueError(
            "Neumím detekovat typ zdroje podle sloupců. "
            f"Nalezené sloupce: {list(df.columns)}"
        )
    return best


def normalize_df(df: pd.DataFrame, source: SourceSpec) -> pd.DataFrame:
    """
    Creates internal canonical columns (company, email_raw, full_name, city, district, country, programs_raw, ...)
    Missing fields are created empty.
    """
    out = pd.DataFrame()

    for internal_name, source_col in source.mapping.items():
        if source_col in df.columns:
            out[internal_name] = df[source_col].astype(str)
        else:
            out[internal_name] = ""

    # Ensure standard internal columns exist
    for col in [
        "company", "email_raw", "full_name", "city", "district", "country",
        "programs_raw", "partner_company", "external_id"
    ]:
        if col not in out.columns:
            out[col] = ""

    # Basic cleanup
    for c in out.columns:
        out[c] = out[c].fillna("").astype(str).str.strip()

    return out
