from __future__ import annotations

import pandas as pd


REPORT_COLUMNS = ["type", "row_index", "detail", "email_raw", "company", "source_file", "source_row_index"]


def _col_or_default(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def find_duplicates_by_email(df: pd.DataFrame) -> pd.DataFrame:
    if "email" not in df.columns:
        return pd.DataFrame(columns=["email", "count", "source_files"])

    work = df.copy()
    work["email"] = work["email"].fillna("").astype(str).str.strip()
    work = work[work["email"] != ""]
    if len(work) == 0:
        return pd.DataFrame(columns=["email", "count", "source_files"])

    counts = work.groupby("email", as_index=False).size().rename(columns={"size": "count"})
    dups = counts[counts["count"] > 1].copy()
    if len(dups) == 0:
        return pd.DataFrame(columns=["email", "count", "source_files"])

    if "source_file" in work.columns:
        source_files = (
            work.groupby("email")["source_file"]
            .apply(
                lambda s: ",".join(
                    sorted({str(x).strip() for x in s.tolist() if str(x).strip()})
                )
            )
            .rename("source_files")
            .reset_index()
        )
        dups = dups.merge(source_files, on="email", how="left")
    else:
        dups["source_files"] = ""

    return dups


def build_report(
    invalid_emails_df: pd.DataFrame,
    unknown_programs_df: pd.DataFrame,
    duplicates_df: pd.DataFrame,
) -> pd.DataFrame:
    parts = []

    if len(invalid_emails_df) > 0:
        source_row = _col_or_default(invalid_emails_df, "source_row_index")
        fallback_row = _col_or_default(invalid_emails_df, "external_id")
        row_index = source_row.where(source_row.astype(str).str.strip() != "", fallback_row)
        detail = (
            _col_or_default(invalid_emails_df, "issue").astype(str).str.strip()
            + " "
            + _col_or_default(invalid_emails_df, "bad_email").astype(str).str.strip()
        ).str.strip()
        parts.append(
            pd.DataFrame(
                {
                    "type": "invalid_email",
                    "row_index": row_index,
                    "detail": detail,
                    "email_raw": _col_or_default(invalid_emails_df, "email_raw"),
                    "company": _col_or_default(invalid_emails_df, "company"),
                    "source_file": _col_or_default(invalid_emails_df, "source_file"),
                    "source_row_index": source_row,
                }
            )
        )

    if len(unknown_programs_df) > 0:
        parts.append(
            pd.DataFrame(
                {
                    "type": "unknown_program_code",
                    "row_index": _col_or_default(unknown_programs_df, "source_row_index").where(
                        _col_or_default(unknown_programs_df, "source_row_index").astype(str).str.strip() != "",
                        _col_or_default(unknown_programs_df, "row_index"),
                    ),
                    "detail": _col_or_default(unknown_programs_df, "unknown_code"),
                    "email_raw": "",
                    "company": "",
                    "source_file": _col_or_default(unknown_programs_df, "source_file"),
                    "source_row_index": _col_or_default(unknown_programs_df, "source_row_index"),
                }
            )
        )

    if len(duplicates_df) > 0:
        source_files = _col_or_default(duplicates_df, "source_files")
        detail = (
            _col_or_default(duplicates_df, "email").astype(str)
            + " (count="
            + _col_or_default(duplicates_df, "count").astype(str)
            + ")"
        )
        detail = detail.where(
            source_files.astype(str).str.strip() == "",
            detail + " [files=" + source_files.astype(str) + "]",
        )
        parts.append(
            pd.DataFrame(
                {
                    "type": "duplicate_email",
                    "row_index": "",
                    "detail": detail,
                    "email_raw": "",
                    "company": "",
                    "source_file": source_files,
                    "source_row_index": "",
                }
            )
        )

    if not parts:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    return pd.concat(parts, ignore_index=True).reindex(columns=REPORT_COLUMNS)
