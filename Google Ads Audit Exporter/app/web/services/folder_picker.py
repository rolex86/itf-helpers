from __future__ import annotations

from pathlib import Path


def pick_directory(initial_dir: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - depends on runtime GUI availability
        raise RuntimeError(f"Slozkovy dialog neni k dispozici: {exc}") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    dialog_initial_dir = None
    if initial_dir:
        candidate = Path(initial_dir).expanduser()
        if candidate.exists():
            dialog_initial_dir = str(candidate)

    try:
        selected = filedialog.askdirectory(
            initialdir=dialog_initial_dir,
            title="Vyber složku pro exporty Google Ads",
            mustexist=False,
        )
    finally:
        root.destroy()

    normalized = (selected or "").strip()
    return normalized or None
