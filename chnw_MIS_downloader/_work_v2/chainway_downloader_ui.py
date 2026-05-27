#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import chainway_cert_downloader_v13 as downloader

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "chainway_downloader_config_v1.json"
RUNTIME_DIR = BASE_DIR / "ui_runtime"
LOG_FILE = RUNTIME_DIR / "downloader.log"
HOST = "127.0.0.1"
PORT = 8765


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").replace("\r", "").split("\n") if line.strip()]


def split_tokens(value: str) -> list[str]:
    raw = value or ""
    for sep in [",", ";"]:
        raw = raw.replace(sep, "\n")
    return split_lines(raw)


def parse_group_options_text(value: str) -> list[dict[str, str]]:
    options = []
    for line in split_lines(value):
        if "|" not in line:
            continue
        label, option_value = line.split("|", 1)
        label = label.strip()
        option_value = option_value.strip()
        if label and option_value:
            options.append({"label": label, "value": option_value})
    return options


def format_group_options_text(options: list[dict[str, str]]) -> str:
    return "\n".join(f"{item['label']}|{item['value']}" for item in options)


def parse_bool(form: dict[str, list[str]], key: str) -> bool:
    return key in form


def parse_int(form: dict[str, list[str]], key: str, fallback: int) -> int:
    try:
        return int((form.get(key) or [str(fallback)])[0])
    except Exception:
        return fallback


def parse_float(form: dict[str, list[str]], key: str, fallback: float) -> float:
    try:
        return float((form.get(key) or [str(fallback)])[0])
    except Exception:
        return fallback


def load_ui_config() -> dict:
    return downloader.load_config(CONFIG_PATH)


def save_ui_config(config: dict) -> None:
    downloader.save_config(CONFIG_PATH, config)


def resolve_status_file(config: dict) -> Path:
    return downloader.resolve_config_path(CONFIG_PATH.resolve().parent, config["paths"]["status_file"])


def load_runtime_status() -> dict:
    config = load_ui_config()
    status_file = resolve_status_file(config)
    if not status_file.exists():
        return {}
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def runtime_payload() -> dict:
    runtime = load_runtime_status()
    payload = dict(runtime)
    payload["current_downloaded_human"] = fmt_bytes(runtime.get("current_downloaded_bytes", 0))
    payload["current_total_human"] = fmt_bytes(runtime.get("current_total_bytes", 0))
    payload["current_speed_human"] = fmt_bytes(runtime.get("current_speed_bps", 0))
    total = float(runtime.get("current_total_bytes", 0) or 0)
    downloaded = float(runtime.get("current_downloaded_bytes", 0) or 0)
    payload["current_percent"] = round((downloaded / total) * 100, 2) if total > 0 else 0
    return payload


def load_state_summary() -> dict:
    config = load_ui_config()
    state_path = downloader.resolve_config_path(CONFIG_PATH.resolve().parent, config["paths"]["state_file"])
    legacy_state_path = downloader.resolve_config_path(CONFIG_PATH.resolve().parent, config["paths"]["legacy_state_file"])
    chosen = state_path if state_path.exists() else legacy_state_path
    summary = {
        "path": str(chosen if chosen else state_path),
        "downloaded_count": 0,
        "seen_count": 0,
        "match_count": 0,
        "error_count": 0,
    }
    if not chosen.exists():
        return summary
    try:
        data = json.loads(chosen.read_text(encoding="utf-8"))
    except Exception:
        return summary
    summary["downloaded_count"] = len(data.get("downloaded_keys", []))
    summary["seen_count"] = len(data.get("seen_keys", []))
    summary["match_count"] = len(data.get("matches", []))
    summary["error_count"] = len(data.get("errors", []))
    return summary


def fmt_bytes(value: int | float | None) -> str:
    try:
        return downloader.human_bytes(float(value or 0))
    except Exception:
        return "0 B"


def config_from_form(form: dict[str, list[str]]) -> dict:
    config = load_ui_config()
    config["login"]["username"] = (form.get("username") or [""])[0].strip()
    config["login"]["password"] = (form.get("password") or [""])[0]

    config["search"]["terms"] = split_lines((form.get("terms") or [""])[0])
    config["search"]["selected_group_values"] = [item.strip() for item in form.get("selected_group_values", []) if item.strip()]

    custom_group_options = parse_group_options_text((form.get("group_options_text") or [""])[0])
    if custom_group_options:
        config["search"]["group_options"] = custom_group_options

    config["search"]["group_keywords"] = split_tokens((form.get("group_keywords") or [""])[0])
    config["search"]["path_fragments"] = split_lines((form.get("path_fragments") or [""])[0])
    config["search"]["page_size"] = parse_int(form, "page_size", config["search"]["page_size"])

    config["filters"]["allowed_extensions"] = split_tokens((form.get("allowed_extensions") or [""])[0])
    config["filters"]["blocked_extensions"] = split_tokens((form.get("blocked_extensions") or [""])[0])

    config["paths"]["download_dir"] = (form.get("download_dir") or [config["paths"]["download_dir"]])[0].strip()
    config["paths"]["profile_dir"] = (form.get("profile_dir") or [config["paths"]["profile_dir"]])[0].strip()
    config["paths"]["state_file"] = (form.get("state_file") or [config["paths"]["state_file"]])[0].strip()
    config["paths"]["legacy_state_file"] = (form.get("legacy_state_file") or [config["paths"]["legacy_state_file"]])[0].strip()
    config["paths"]["debug_dir"] = (form.get("debug_dir") or [config["paths"]["debug_dir"]])[0].strip()

    config["runtime"]["headless"] = parse_bool(form, "headless")
    config["runtime"]["keepalive_interval_seconds"] = parse_float(form, "keepalive_interval_seconds", config["runtime"]["keepalive_interval_seconds"])
    config["runtime"]["session_recovery_timeout_seconds"] = parse_float(form, "session_recovery_timeout_seconds", config["runtime"]["session_recovery_timeout_seconds"])
    config["runtime"]["session_recovery_poll_seconds"] = parse_float(form, "session_recovery_poll_seconds", config["runtime"]["session_recovery_poll_seconds"])
    config["runtime"]["between_downloads_seconds"] = parse_float(form, "between_downloads_seconds", config["runtime"]["between_downloads_seconds"])
    config["runtime"]["between_searches_seconds"] = parse_float(form, "between_searches_seconds", config["runtime"]["between_searches_seconds"])
    config["runtime"]["max_session_recovery_per_search"] = parse_int(form, "max_session_recovery_per_search", config["runtime"]["max_session_recovery_per_search"])

    return config


def checked_attr(value: bool) -> str:
    return "checked" if value else ""


def selected_group_set(config: dict) -> set[str]:
    return set(config["search"].get("selected_group_values", []))


def render_group_picker(config: dict) -> str:
    selected = selected_group_set(config)
    parts = []
    for item in config["search"].get("group_options", []):
        label = html.escape(item["label"])
        value = html.escape(item["value"])
        checked = "checked" if item["value"] in selected else ""
        parts.append(
            f'<label class="choice"><input type="checkbox" name="selected_group_values" value="{value}" {checked}>'
            f'<span>{label}</span></label>'
        )
    return "".join(parts)


class DownloaderRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._started_at = 0.0

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False, "Downloader už běží."
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as log:
                log.write(f"\n===== START {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            log_handle = LOG_FILE.open("a", encoding="utf-8")
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            cmd = [
                sys.executable,
                "-u",
                "chainway_cert_downloader_v13.py",
                "--config",
                str(CONFIG_PATH),
                "--no-manual-ready",
            ]
            self._process = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            self._started_at = time.time()
            return True, "Downloader spuštěn."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return False, "Downloader momentálně neběží."
            self._process.terminate()
            try:
                self._process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            return True, "Downloader zastaven."

    def status(self) -> dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "running": running,
                "pid": self._process.pid if running else None,
                "started_at": self._started_at if running else None,
                "returncode": None if running or self._process is None else self._process.poll(),
            }


RUNNER = DownloaderRunner()


def read_log_tail(max_lines: int = 120) -> str:
    if not LOG_FILE.exists():
        return ""
    lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def pick_directory(initial_dir: str) -> tuple[bool, str]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=initial_dir or str(BASE_DIR))
        root.destroy()
        if selected:
            return True, selected
        return False, "Výběr složky byl zrušen."
    except Exception as exc:
        return False, f"Dialog pro výběr složky není dostupný: {exc}"


def render_page(message: str = "", error: str = "") -> str:
    config = load_ui_config()
    status = RUNNER.status()
    runtime = runtime_payload()
    state_summary = load_state_summary()
    status_text = "Běží" if status["running"] else "Zastaveno"
    terms_text = "\n".join(config["search"].get("terms", []))
    group_options_text = format_group_options_text(config["search"].get("group_options", []))
    group_keywords = "\n".join(config["search"].get("group_keywords", []))
    path_fragments = "\n".join(config["search"].get("path_fragments", []))
    allowed_extensions = ", ".join(config["filters"].get("allowed_extensions", []))
    blocked_extensions = ", ".join(config["filters"].get("blocked_extensions", []))
    log_tail = html.escape(read_log_tail())
    downloaded_count = runtime.get("downloaded_count", 0)
    matched_count = runtime.get("matched_count", 0)
    error_count = runtime.get("error_count", 0)
    current_group = html.escape(runtime.get("current_group", "") or "-")
    current_search = html.escape(runtime.get("current_search", "") or "-")
    current_file = html.escape(runtime.get("current_file", "") or "-")
    current_phase = html.escape(runtime.get("phase", "") or "-")
    current_message = html.escape(runtime.get("message", "") or "-")
    current_downloaded = fmt_bytes(runtime.get("current_downloaded_bytes", 0))
    current_total = fmt_bytes(runtime.get("current_total_bytes", 0))
    current_speed = fmt_bytes(runtime.get("current_speed_bps", 0))
    task_index = runtime.get("task_index", 0) or 0
    task_total = runtime.get("task_total", 0) or 0
    item_index = runtime.get("current_item_index", 0) or 0
    item_total = runtime.get("current_item_total", 0) or 0
    updated_at = html.escape(runtime.get("updated_at", "") or "-")
    state_path = html.escape(state_summary["path"])
    resume_downloaded = state_summary["downloaded_count"]
    resume_matches = state_summary["match_count"]
    resume_errors = state_summary["error_count"]

    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CHAINWAY MIS Downloader</title>
  <style>
    :root {{
      --bg: #0b1117;
      --panel: #121a23;
      --panel-2: #18222d;
      --ink: #e7eef5;
      --muted: #8fa2b3;
      --line: #263546;
      --accent: #1e8f8c;
      --accent-2: #f0a33a;
      --danger: #c95a5a;
      --shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Trebuchet MS", "Lucida Sans Unicode", "Lucida Grande", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(30,143,140,0.18), transparent 24%),
        radial-gradient(circle at top right, rgba(240,163,58,0.12), transparent 22%),
        linear-gradient(135deg, #081018 0%, var(--bg) 48%, #0d141c 100%);
    }}
    .shell {{
      max-width: 1260px;
      margin: 32px auto;
      padding: 0 18px 40px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(13,110,110,0.94), rgba(19,53,73,0.95));
      color: #fff8ef;
      border-radius: 26px;
      padding: 26px 28px;
      box-shadow: var(--shadow);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 1;
      letter-spacing: 0.02em;
    }}
    .hero p {{
      margin: 0;
      max-width: 900px;
      color: rgba(255,248,239,0.88);
      font-size: 16px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
      margin-top: 18px;
    }}
    .card {{
      background: rgba(18,26,35,0.92);
      border: 1px solid rgba(38,53,70,0.95);
      border-radius: 22px;
      box-shadow: var(--shadow);
      padding: 22px;
      backdrop-filter: blur(6px);
    }}
    .card h2 {{
      margin: 0 0 16px;
      font-size: 22px;
    }}
    .stack {{
      display: grid;
      gap: 14px;
    }}
    .cols {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 7px;
      font-size: 14px;
      color: var(--muted);
    }}
    input[type="text"], input[type="password"], input[type="number"], textarea {{
      width: 100%;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #0e151d;
      padding: 12px 14px;
      font: inherit;
      color: var(--ink);
    }}
    input[type="text"]::placeholder, input[type="password"]::placeholder, input[type="number"]::placeholder, textarea::placeholder {{
      color: #6f8498;
    }}
    textarea {{ min-height: 120px; resize: vertical; }}
    .path-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }}
    .btn-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-top: 8px;
    }}
    button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      width: auto;
      height: auto;
      border: 0;
      border-radius: 12px;
      padding: 10px 16px;
      min-height: 0;
      line-height: 1.2;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.18s ease, opacity 0.18s ease;
    }}
    button:hover {{ transform: translateY(-1px); }}
    .primary {{ background: var(--accent); color: white; }}
    .secondary {{ background: #223242; color: #dce8f2; }}
    .danger {{ background: var(--danger); color: white; }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      border-radius: 999px;
      padding: 10px 14px;
      background: #13202a;
      color: #dce8f2;
      font-weight: 700;
    }}
    .status-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: {"#1f9d55" if status["running"] else "#b7791f"};
      box-shadow: 0 0 0 6px rgba(31, 157, 85, 0.12);
    }}
    .choices {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 10px;
      max-height: 320px;
      overflow: auto;
      padding: 6px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #0d151d;
    }}
    .choice {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 10px 12px;
      border-radius: 14px;
      background: #16202a;
      color: var(--ink);
    }}
    .choice input {{ margin-top: 3px; }}
    .flash {{
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 14px;
      font-weight: 700;
    }}
    .flash.ok {{ background: #123228; color: #9be3c3; }}
    .flash.err {{ background: #3a1d22; color: #ffb2b2; }}
    details {{
      border: 1px dashed var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      background: rgba(15, 23, 31, 0.78);
    }}
    summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--ink);
    }}
    pre {{
      margin: 0;
      background: #0a0f14;
      color: #d6e3ef;
      border-radius: 18px;
      padding: 16px;
      min-height: 360px;
      max-height: 680px;
      overflow: auto;
      font-family: "Consolas", "Liberation Mono", monospace;
      font-size: 13px;
      line-height: 1.45;
    }}
    .tiny {{
      font-size: 13px;
      color: var(--muted);
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .metric {{
      background: linear-gradient(135deg, #16212c, #101821);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
    }}
    .metric .k {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .metric .v {{
      margin-top: 6px;
      font-size: 26px;
      font-weight: 700;
      color: var(--ink);
    }}
    .facts {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .fact {{
      background: #0f161f;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
    }}
    .fact .k {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .fact .v {{
      margin-top: 6px;
      font-weight: 700;
      word-break: break-word;
    }}
    .progress {{
      margin-top: 10px;
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #0b1218;
      border: 1px solid var(--line);
      overflow: hidden;
    }}
    .progress-fill {{
      height: 100%;
      width: 0%;
      border-radius: 999px;
      background: linear-gradient(90deg, #1e8f8c 0%, #37b6b2 100%);
      transition: width 0.25s ease;
      box-shadow: 0 0 18px rgba(55, 182, 178, 0.25);
    }}
    .check {{
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--ink);
    }}
    @media (max-width: 980px) {{
      .grid, .cols {{ grid-template-columns: 1fr; }}
      .shell {{ margin-top: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>CHAINWAY MIS Downloader</h1>
    </section>

    {f'<div class="flash ok">{html.escape(message)}</div>' if message else ''}
    {f'<div class="flash err">{html.escape(error)}</div>' if error else ''}

    <div class="grid">
      <form class="card stack" method="post" action="/save">
        <h2>Nastavení</h2>

        <div class="cols">
          <label>Uživatelské jméno
            <input type="text" name="username" value="{html.escape(config['login']['username'])}">
          </label>
          <label>Heslo
            <input type="password" name="password" value="{html.escape(config['login']['password'])}">
          </label>
        </div>

        <label>Hledané výrazy
          <textarea name="terms" placeholder="Každý výraz na nový řádek">{html.escape(terms_text)}</textarea>
        </label>

        <label>Skupiny v MIS
          <div class="choices">
            {render_group_picker(config)}
          </div>
        </label>

        <label>Cílová složka
          <div class="path-row">
            <input type="text" id="download_dir" name="download_dir" value="{html.escape(config['paths']['download_dir'])}">
            <button type="button" class="secondary" onclick="browseDir()">Procházet...</button>
          </div>
        </label>

        <details>
          <summary>Pokročilé</summary>
          <div class="stack" style="margin-top:14px;">
            <label>Složka profilu
              <input type="text" name="profile_dir" value="{html.escape(config['paths']['profile_dir'])}">
            </label>
            <div class="cols">
              <label>Soubor stavu
                <input type="text" name="state_file" value="{html.escape(config['paths']['state_file'])}">
              </label>
              <label>Původní soubor stavu
                <input type="text" name="legacy_state_file" value="{html.escape(config['paths']['legacy_state_file'])}">
              </label>
            </div>
            <label>Debug složka
              <input type="text" name="debug_dir" value="{html.escape(config['paths']['debug_dir'])}">
            </label>
            <label>Volby skupin
              <textarea name="group_options_text" placeholder="Popisek|Hodnota na každý řádek">{html.escape(group_options_text)}</textarea>
            </label>
            <div class="cols">
              <label>Klíčová slova skupin
                <textarea name="group_keywords">{html.escape(group_keywords)}</textarea>
              </label>
              <label>Fragmenty cest
                <textarea name="path_fragments">{html.escape(path_fragments)}</textarea>
              </label>
            </div>
            <div class="cols">
              <label>Povolené přípony
                <input type="text" name="allowed_extensions" value="{html.escape(allowed_extensions)}">
              </label>
              <label>Blokované přípony
                <input type="text" name="blocked_extensions" value="{html.escape(blocked_extensions)}">
              </label>
            </div>
            <div class="cols">
              <label>Velikost stránky
                <input type="number" name="page_size" value="{int(config['search']['page_size'])}">
              </label>
              <label class="check">Bez zobrazení prohlížeče
                <input type="checkbox" name="headless" {checked_attr(config['runtime']['headless'])}>
              </label>
            </div>
            <div class="cols">
              <label>Interval keepalive
                <input type="number" step="1" name="keepalive_interval_seconds" value="{config['runtime']['keepalive_interval_seconds']}">
              </label>
              <label>Timeout obnovy session
                <input type="number" step="1" name="session_recovery_timeout_seconds" value="{config['runtime']['session_recovery_timeout_seconds']}">
              </label>
            </div>
            <div class="cols">
              <label>Interval kontroly obnovy
                <input type="number" step="0.5" name="session_recovery_poll_seconds" value="{config['runtime']['session_recovery_poll_seconds']}">
              </label>
              <label>Pauza mezi downloady
                <input type="number" step="0.1" name="between_downloads_seconds" value="{config['runtime']['between_downloads_seconds']}">
              </label>
            </div>
            <div class="cols">
              <label>Pauza mezi hledáními
                <input type="number" step="0.1" name="between_searches_seconds" value="{config['runtime']['between_searches_seconds']}">
              </label>
              <label>Max. obnov na hledání
                <input type="number" step="1" name="max_session_recovery_per_search" value="{config['runtime']['max_session_recovery_per_search']}">
              </label>
            </div>
          </div>
        </details>

        <div class="btn-row">
          <button class="primary" type="submit">Uložit nastavení</button>
          <button class="secondary" type="submit" formaction="/start">Uložit a spustit</button>
          <button class="danger" type="submit" formaction="/stop" formmethod="post">Zastavit běh</button>
        </div>
      </form>

      <section class="card stack">
        <h2>Stav a log</h2>
        <div class="status-pill">
          <span class="status-dot"></span>
          <span id="status-text">{status_text}</span>
        </div>
        <div class="tiny" id="status-meta">
          PID: {status['pid'] or '-'} | Návratový kód: {status['returncode'] if status['returncode'] is not None else '-'}
        </div>
        <div class="tiny" id="resume-meta">
          Naváže z {resume_downloaded} stažených položek, {resume_matches} shod, {resume_errors} chyb.
        </div>
        <div class="tiny" id="state-path">
          Stavový soubor: {state_path}
        </div>
        <div class="metrics" id="metrics">
          <div class="metric">
            <div class="k">Staženo</div>
            <div class="v" id="metric-downloaded">{downloaded_count}</div>
          </div>
          <div class="metric">
            <div class="k">Shod</div>
            <div class="v" id="metric-matched">{matched_count}</div>
          </div>
          <div class="metric">
            <div class="k">Chyb</div>
            <div class="v" id="metric-errors">{error_count}</div>
          </div>
        </div>
        <div class="facts">
          <div class="fact">
            <div class="k">Fáze</div>
            <div class="v" id="fact-phase">{current_phase}</div>
          </div>
          <div class="fact">
            <div class="k">Úloha</div>
            <div class="v" id="fact-task">{task_index}/{task_total}</div>
          </div>
          <div class="fact">
            <div class="k">Soubor v úloze</div>
            <div class="v" id="fact-item">{item_index}/{item_total}</div>
          </div>
          <div class="fact">
            <div class="k">Skupina</div>
            <div class="v" id="fact-group">{current_group}</div>
          </div>
          <div class="fact">
            <div class="k">Hledaný výraz</div>
            <div class="v" id="fact-search">{current_search}</div>
          </div>
          <div class="fact">
            <div class="k">Aktuální soubor</div>
            <div class="v" id="fact-file">{current_file}</div>
          </div>
          <div class="fact">
            <div class="k">Přenos</div>
            <div class="v" id="fact-transfer">{runtime.get("current_percent", 0):.2f}% | {current_downloaded} / {current_total} @ {current_speed}/s</div>
            <div class="progress" aria-label="Průběh stahování">
              <div class="progress-fill" id="fact-transfer-bar" style="width: {runtime.get('current_percent', 0):.2f}%;"></div>
            </div>
          </div>
          <div class="fact">
            <div class="k">Poslední zpráva</div>
            <div class="v" id="fact-message">{current_message}</div>
          </div>
          <div class="fact">
            <div class="k">Aktualizováno</div>
            <div class="v" id="fact-updated">{updated_at}</div>
          </div>
        </div>
        <pre id="log-view">{log_tail}</pre>
      </section>
    </div>
  </div>

  <script>
    let lastLogText = document.getElementById('log-view').textContent || '';

    async function refreshStatus() {{
      try {{
        const res = await fetch('/api/status');
        const data = await res.json();
        document.getElementById('status-text').textContent = data.running ? 'Běží' : 'Zastaveno';
        document.getElementById('status-meta').textContent =
          `PID: ${{data.pid || '-'}} | Návratový kód: ${{data.returncode ?? '-'}}`;
        document.getElementById('resume-meta').textContent =
          `Naváže z ${{data.state.downloaded_count || 0}} stažených položek, ${{data.state.match_count || 0}} shod, ${{data.state.error_count || 0}} chyb.`;
        document.getElementById('state-path').textContent = `Stavový soubor: ${{data.state.path || '-'}}`;
        document.getElementById('metric-downloaded').textContent = data.runtime.downloaded_count ?? 0;
        document.getElementById('metric-matched').textContent = data.runtime.matched_count ?? 0;
        document.getElementById('metric-errors').textContent = data.runtime.error_count ?? 0;
        document.getElementById('fact-phase').textContent = data.runtime.phase || '-';
        document.getElementById('fact-task').textContent = `${{data.runtime.task_index || 0}}/${{data.runtime.task_total || 0}}`;
        document.getElementById('fact-item').textContent = `${{data.runtime.current_item_index || 0}}/${{data.runtime.current_item_total || 0}}`;
        document.getElementById('fact-group').textContent = data.runtime.current_group || '-';
        document.getElementById('fact-search').textContent = data.runtime.current_search || '-';
        document.getElementById('fact-file').textContent = data.runtime.current_file || '-';
        document.getElementById('fact-transfer').textContent =
          `${{(data.runtime.current_percent ?? 0).toFixed(2)}}% | ${{data.runtime.current_downloaded_human || '0 B'}} / ${{data.runtime.current_total_human || '0 B'}} @ ${{data.runtime.current_speed_human || '0 B'}}/s`;
        document.getElementById('fact-transfer-bar').style.width = `${{Math.max(0, Math.min(100, data.runtime.current_percent ?? 0))}}%`;
        document.getElementById('fact-message').textContent = data.runtime.message || '-';
        document.getElementById('fact-updated').textContent = data.runtime.updated_at || '-';
        const logEl = document.getElementById('log-view');
        const nextLog = data.log_tail || '';
        if (nextLog !== lastLogText) {{
          const distanceFromBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight;
          const shouldStickToBottom = distanceFromBottom < 24;
          const previousTop = logEl.scrollTop;
          logEl.textContent = nextLog;
          lastLogText = nextLog;
          if (shouldStickToBottom) {{
            logEl.scrollTop = logEl.scrollHeight;
          }} else {{
            logEl.scrollTop = previousTop;
          }}
        }}
      }} catch (err) {{}}
    }}

    async function browseDir() {{
      const current = document.getElementById('download_dir').value || '';
      const res = await fetch('/api/browse-download-dir', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
        body: new URLSearchParams({{ current }})
      }});
      const data = await res.json();
      if (data.ok && data.path) {{
        document.getElementById('download_dir').value = data.path;
      }} else if (data.error) {{
        alert(data.error);
      }}
    }}

    refreshStatus();
    setInterval(refreshStatus, 3000);
  </script>
</body>
</html>"""


class UIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            params = parse_qs(parsed.query)
            message = (params.get("message") or [""])[0]
            error = (params.get("error") or [""])[0]
            self.respond_html(render_page(message=message, error=error))
            return
        if parsed.path == "/api/status":
            status = RUNNER.status()
            status["log_tail"] = read_log_tail()
            status["runtime"] = runtime_payload()
            status["state"] = load_state_summary()
            self.respond_json(status)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/browse-download-dir":
            form = self.read_form()
            ok, result = pick_directory((form.get("current") or [""])[0])
            if ok:
                self.respond_json({"ok": True, "path": result})
            else:
                self.respond_json({"ok": False, "error": result})
            return

        if parsed.path in {"/save", "/start", "/stop"}:
            form = self.read_form()
            if parsed.path in {"/save", "/start"}:
                config = config_from_form(form)
                save_ui_config(config)

            if parsed.path == "/save":
                self.redirect("/?message=Nastavení%20uloženo.")
                return

            if parsed.path == "/start":
                ok, message = RUNNER.start()
                if ok:
                    self.redirect("/?message=Downloader%20spuštěn.")
                else:
                    self.redirect(f"/?error={quote(message)}")
                return

            if parsed.path == "/stop":
                ok, message = RUNNER.stop()
                if ok:
                    self.redirect("/?message=Downloader%20zastaven.")
                else:
                    self.redirect(f"/?error={quote(message)}")
                return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        return parse_qs(body, keep_blank_values=True)

    def respond_html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_json(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    downloader.ensure_config_file(CONFIG_PATH)
    server = ThreadingHTTPServer((HOST, PORT), UIHandler)
    url = f"http://{HOST}:{PORT}"
    print(f"UI běží na {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
