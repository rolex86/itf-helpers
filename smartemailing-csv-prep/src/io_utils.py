from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd


@dataclass
class ReadResult:
    df: pd.DataFrame
    delimiter: str
    encoding: str


def _try_read_csv(content: bytes, sep: str, encoding: str) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(io.BytesIO(content), sep=sep, encoding=encoding, dtype=str, keep_default_na=False)
    except Exception:
        return None


def _candidate_score(df: pd.DataFrame, sep: str) -> tuple[int, int, int]:
    cols = [str(c).strip() for c in df.columns]
    non_empty_cols = sum(1 for c in cols if c)
    unnamed_cols = sum(1 for c in cols if c.lower().startswith("unnamed"))

    other_sep = "," if sep == ";" else ";"
    suspicious_single_col = 0
    if len(cols) == 1:
        header = cols[0]
        first_value = str(df.iloc[0, 0]) if len(df) > 0 else ""
        if other_sep in header or other_sep in first_value:
            # Typical signal of wrong delimiter: everything collapsed into one column.
            suspicious_single_col = 1

    return (non_empty_cols, -unnamed_cols, -suspicious_single_col)


def read_csv_best_effort(content: bytes) -> ReadResult:
    """
    Tries common CSV variants and selects the best parse candidate.
    Prefers UTF-8 variants first, then cp1250 fallback.
    """
    candidates: list[Tuple[str, str]] = [
        (";", "utf-8-sig"),
        (";", "utf-8"),
        (";", "cp1250"),
        (",", "utf-8-sig"),
        (",", "utf-8"),
        (",", "cp1250"),
    ]

    best: Optional[ReadResult] = None
    best_score: Optional[tuple[int, int, int]] = None

    for sep, enc in candidates:
        df = _try_read_csv(content, sep=sep, encoding=enc)
        if df is None or df.shape[1] < 1:
            continue

        score = _candidate_score(df, sep)
        if best is None or (best_score is not None and score > best_score):
            best = ReadResult(df=df, delimiter=sep, encoding=enc)
            best_score = score

    if best is not None:
        return best

    raise ValueError("Nepodařilo se načíst CSV (zkus jiné kódování / oddělovač).")


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    # Drop empty/unnamed columns
    drop_cols = [c for c in df.columns if c.lower().startswith("unnamed")]
    if drop_cols:
        df = df.drop(columns=drop_cols, errors="ignore")
    return df
