from __future__ import annotations

from dataclasses import dataclass
from typing import Set, List

import pandas as pd


@dataclass(frozen=True)
class Schema:
    columns: List[str]
    columns_set: Set[str]


def schema_from_export_df(export_df: pd.DataFrame) -> Schema:
    cols = [str(c).strip() for c in export_df.columns if str(c).strip()]
    return Schema(columns=cols, columns_set=set(cols))
