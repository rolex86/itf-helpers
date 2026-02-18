from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Set, Tuple

import pandas as pd


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_TITLE_BEFORE_FALLBACK_RE = re.compile(
    r"(^|\s)(BcA|Bc|Ing(?:\.?\s*arch)?|JUDr|MUDr|MVDr|MgA|Mgr|PhDr|RNDr|ThDr|ThLic|doc|prof)(?:\.?(?=\s|$)|\.(?=[^\W\d_]))",
    flags=re.IGNORECASE,
)
_TITLE_AFTER_FALLBACK_RE = re.compile(
    r"(^|\s)\.?(CSc|DrSc|Dr|Ph\.?\s*D|Th\.?\s*D|MBA|DiS|ACCA|FCCA)\.?(?=\s|$|[,;])",
    flags=re.IGNORECASE,
)
_TITLE_BEFORE_CANONICAL = {
    "bca": "BcA.",
    "bc": "Bc.",
    "ing": "Ing.",
    "ingarch": "Ing.arch.",
    "judr": "JUDr.",
    "mudr": "MUDr.",
    "mvdr": "MVDr.",
    "mga": "MgA.",
    "mgr": "Mgr.",
    "phdr": "PhDr.",
    "rndr": "RNDr.",
    "thdr": "ThDr.",
    "thlic": "ThLic.",
    "doc": "doc.",
    "prof": "prof.",
}
_TITLE_AFTER_CANONICAL = {
    "csc": "CSc.",
    "dr": "Dr.",
    "drsc": "DrSc.",
    "phd": "Ph.D.",
    "thd": "Th.D.",
    "mba": "MBA",
    "dis": "DiS.",
    "acca": "ACCA",
    "fcca": "FCCA",
}


def normalize_lookup_token(value: str) -> str:
    x = str(value).strip().casefold()
    x = unicodedata.normalize("NFKD", x)
    x = "".join(ch for ch in x if not unicodedata.combining(ch))
    x = re.sub(r"[\.\-_]+", " ", x)
    x = re.sub(r"\s+", " ", x).strip()
    return x


def split_emails(df: pd.DataFrame, separators: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Explode rows by splitting email_raw into multiple emails.
    Returns: (expanded_df, invalid_email_rows_report)
    """
    work = df.copy()
    email_raw = work.get("email_raw", pd.Series([""] * len(work), index=work.index)).fillna("").astype(str)

    normalized_emails = email_raw
    for sep in separators:
        normalized_emails = normalized_emails.str.replace(sep, ",", regex=False)

    email_lists = normalized_emails.apply(lambda x: [p.strip() for p in x.split(",") if p.strip()])
    missing_mask = email_lists.str.len() == 0

    missing_df = work.loc[missing_mask].copy()
    if len(missing_df) > 0:
        missing_df["issue"] = "missing_email"
        missing_df["bad_email"] = ""

    candidate_df = work.loc[~missing_mask].copy()
    candidate_df["__email_list"] = email_lists.loc[~missing_mask]
    candidate_df = candidate_df.explode("__email_list")
    candidate_df["email"] = candidate_df["__email_list"].fillna("").astype(str).str.strip()
    candidate_df = candidate_df.drop(columns=["__email_list"])

    invalid_mask = ~candidate_df["email"].str.match(EMAIL_RE)
    invalid_df = candidate_df.loc[invalid_mask].copy()
    if len(invalid_df) > 0:
        invalid_df["issue"] = "invalid_email"
        invalid_df["bad_email"] = invalid_df["email"]

    expanded_df = candidate_df.loc[~invalid_mask].copy()

    invalid_frames = [frame for frame in [missing_df, invalid_df] if len(frame) > 0]
    if invalid_frames:
        merged_invalid = pd.concat(invalid_frames, ignore_index=True)
    else:
        merged_invalid = pd.DataFrame(columns=list(work.columns) + ["issue", "bad_email"])

    if len(expanded_df) == 0:
        expanded_df = pd.DataFrame(columns=list(work.columns) + ["email"])
    else:
        expanded_df = expanded_df.reset_index(drop=True)

    if len(merged_invalid) > 0:
        merged_invalid = merged_invalid.reset_index(drop=True)

    return expanded_df, merged_invalid


def validate_emails_without_split(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Validate one email per row without splitting multi-address fields.
    Returns: (valid_rows_with_email_col, invalid_rows_report)
    """
    work = df.copy()
    email_series = work.get("email_raw", pd.Series([""] * len(work), index=work.index)).fillna("").astype(str).str.strip()

    missing_mask = email_series == ""
    invalid_mask = (~missing_mask) & (~email_series.str.match(EMAIL_RE))

    valid_df = work.loc[~(missing_mask | invalid_mask)].copy()
    valid_df["email"] = email_series.loc[~(missing_mask | invalid_mask)]

    missing_df = work.loc[missing_mask].copy()
    if len(missing_df) > 0:
        missing_df["issue"] = "missing_email"
        missing_df["bad_email"] = ""

    invalid_df = work.loc[invalid_mask].copy()
    if len(invalid_df) > 0:
        invalid_df["issue"] = "invalid_email"
        invalid_df["bad_email"] = email_series.loc[invalid_mask]

    invalid_parts = [frame for frame in [missing_df, invalid_df] if len(frame) > 0]
    merged_invalid = (
        pd.concat(invalid_parts, ignore_index=True)
        if invalid_parts
        else pd.DataFrame(columns=list(work.columns) + ["issue", "bad_email"])
    )

    if len(valid_df) == 0:
        valid_df = pd.DataFrame(columns=list(work.columns) + ["email"])
    else:
        valid_df = valid_df.reset_index(drop=True)

    if len(merged_invalid) > 0:
        merged_invalid = merged_invalid.reset_index(drop=True)

    return valid_df, merged_invalid


def parse_name_fields(full_name: str, cfg: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """
    Returns: (title_before, first_name, last_name, title_after)
    VBA-like logic:
    - remove punctuation
    - detect titles before/after via regex
    - remaining tokens => first = all but last token, last = last token
    """
    if not full_name:
        return "", "", "", ""

    punct_re = re.compile(cfg["punctuation_strip_regex"])
    s = punct_re.sub("", full_name).strip()
    if not s:
        return "", "", "", ""

    # Normalize common separators so title regex works even when data contains ",Ph.D." (without space).
    full_name_for_match = re.sub(r"[,;]+", " ", full_name)
    full_name_for_match = re.sub(r"\s+", " ", full_name_for_match).strip()

    tb_re = re.compile(cfg["title_before_regex"], flags=re.IGNORECASE)
    ta_re = re.compile(cfg["title_after_regex"], flags=re.IGNORECASE)

    title_before_parts = [str(m.group(2)).strip() for m in tb_re.finditer(full_name_for_match) if str(m.group(2)).strip()]
    title_after_parts = [str(m.group(2)).strip() for m in ta_re.finditer(full_name_for_match) if str(m.group(2)).strip()]

    # Fallback parser for common variants without dots ("Ing", "PhD", "Ph D", ...).
    if not title_before_parts:
        for m in _TITLE_BEFORE_FALLBACK_RE.finditer(full_name_for_match):
            raw = str(m.group(2)).strip()
            key = re.sub(r"[^A-Za-z0-9]+", "", raw).casefold()
            resolved = _TITLE_BEFORE_CANONICAL.get(key, raw)
            if resolved:
                title_before_parts.append(resolved)

    if not title_after_parts:
        for m in _TITLE_AFTER_FALLBACK_RE.finditer(full_name_for_match):
            raw = str(m.group(2)).strip()
            key = re.sub(r"[^A-Za-z0-9]+", "", raw).casefold()
            resolved = _TITLE_AFTER_CANONICAL.get(key, raw)
            if resolved:
                title_after_parts.append(resolved)

    # Keep original order and remove duplicates.
    title_before = " ".join(list(dict.fromkeys([x for x in title_before_parts if x]))).strip()
    title_after = " ".join(list(dict.fromkeys([x for x in title_after_parts if x]))).strip()

    # remove titles from working string (loosely)
    s2 = tb_re.sub(" ", full_name_for_match)
    s2 = ta_re.sub(" ", s2)
    s2 = _TITLE_BEFORE_FALLBACK_RE.sub(" ", s2)
    s2 = _TITLE_AFTER_FALLBACK_RE.sub(" ", s2)
    s2 = punct_re.sub("", s2)
    s2 = re.sub(r"\s+", " ", s2).strip()

    tokens = s2.split(" ") if s2 else []
    if len(tokens) == 0:
        return title_before, "", "", title_after
    if len(tokens) == 1:
        return title_before, tokens[0], "", title_after

    first_name = " ".join(tokens[:-1])
    last_name = tokens[-1]
    return title_before, first_name, last_name, title_after


def apply_name_split(df: pd.DataFrame, name_cfg: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    tb, fn, ln, ta = [], [], [], []
    for v in out.get("full_name", pd.Series([""] * len(out))).astype(str).tolist():
        a, b, c, d = parse_name_fields(v.strip(), name_cfg)
        tb.append(a)
        fn.append(b)
        ln.append(c)
        ta.append(d)
    out["title_before"] = tb
    out["first_name"] = fn
    out["last_name"] = ln
    out["title_after"] = ta
    return out


def parse_program_codes(
    programs_raw: str,
    separators: List[str] | None = None,
    aliases: Dict[str, str] | None = None,
) -> Set[str]:
    if not programs_raw:
        return set()
    separators = separators or [","]
    aliases = aliases or {}

    alias_map = {normalize_lookup_token(k): str(v).strip() for k, v in aliases.items() if str(v).strip()}

    normalized = str(programs_raw)
    for sep in separators:
        if sep:
            normalized = normalized.replace(sep, ",")

    parts = [p.strip() for p in normalized.split(",")]
    parts = [p for p in parts if p]
    resolved = []
    for p in parts:
        key = normalize_lookup_token(p)
        resolved.append(alias_map.get(key, p))
    return set(resolved)


def apply_country_bucket(df: pd.DataFrame, bucket_cfg: Dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    cz_sk = {str(x).strip().upper() for x in bucket_cfg.get("cz_sk", [])}
    de_at_ch = {str(x).strip().upper() for x in bucket_cfg.get("de_at_ch", [])}
    default_other = bucket_cfg.get("other", "EN")
    aliases_cfg = bucket_cfg.get("aliases", {})
    aliases = {normalize_lookup_token(k): str(v).strip().upper() for k, v in aliases_cfg.items() if str(v).strip()}

    countries = (
        out.get("country", pd.Series([""] * len(out), index=out.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    country_codes = countries.str.upper()
    alias_keys = countries.apply(normalize_lookup_token)
    alias_resolved = alias_keys.map(aliases).fillna(country_codes)

    buckets = pd.Series(default_other, index=out.index)
    buckets.loc[alias_resolved.isin(cz_sk)] = "CZ_SK"
    buckets.loc[alias_resolved.isin(de_at_ch)] = "DE_AT_CH"
    out["country_bucket"] = buckets
    return out
