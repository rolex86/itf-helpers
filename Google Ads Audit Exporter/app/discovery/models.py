from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True)
class DiscoveryResult:
    datasets: dict[str, pd.DataFrame] = field(default_factory=dict)
    csv_paths: dict[str, Path] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    notes: dict[str, list[str]] = field(default_factory=dict)
