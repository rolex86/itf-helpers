#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Chainway MIS downloader v13 - relaxed session detection + group filter + watchdog

Co opravuje proti v11:
- úvodní kontrola session je méně přísná
- zkouší rozpoznat více variant stránky (search placeholder, grid, texty v UI)
- při prvním failu se nepoloží hned, ale zkusí znovu načíst filemanager
- teprve pak případně vyžádá relogin

Zachováno:
- stahování podle vybraných skupin
- skip videí a audia
- progress stahování
- debug HTML + screenshot
- ruční relogin a pokračování
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote, unquote_plus

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - fallback for config/UI usage without Playwright installed
    class PlaywrightTimeoutError(Exception):
        pass

    sync_playwright = None

DEFAULT_SEARCH_TERMS = [
    "C61","R1","MC50","P100","MC51","P80","MC95","C63","C66","R3","MC62","C5",
    "CP30","R5","MC21","R6","C6000","MR20","C71","SR160","C72","CM710-1",
    "CM710-4","CM710-8","CM710-16","UR4","URA4","U300","UR1A",
]
DEFAULT_GROUP_OPTIONS = [
    {"label": "Certificate", "value": "ABA900B305DB751D1FE1135E46CAA9130000"},
    {"label": "Accessories Guide", "value": "ABA900B305DA9CE9D717CC294561BF0C0000"},
    {"label": "Antenna For Fixed UHF Reader", "value": "ABA900B305DB8E1FE1359EA3437DAF6D0000"},
    {"label": "Company", "value": "ABA900B305DEAF0D7EB65BF2453D97AD0000"},
    {"label": "Data Sheet (Chainway)", "value": "ABA900B305DE24392EDA02ED446B94E40000"},
    {"label": "Data Sheet (Neutral)", "value": "ABA900B305DE6B80772411A14C5CABA60000"},
    {"label": "Demo software", "value": "ABA900B305E14E3F7EDC85F044BD98B90000"},
    {"label": "Driving Training", "value": "ABA900B305E1E5676B74622224299A8E60000"},
    {"label": "Healthcare", "value": "ABA900B305E19CE222EE49AA741E1A8DB0000"},
    {"label": "Keyboardemulator EN User Manual", "value": "ABA900B305E1E34ED2205BCC406FB8EE0000"},
    {"label": "Logistics", "value": "ABA900B305E198FE198AE583849FDA1970000"},
    {"label": "Maintenance Center", "value": "ABA900B305E11A997C4BB10241B9A3530000"},
    {"label": "MDM", "value": "ABA900B305E1E0D7A14457E84D8BB7A00000"},
    {"label": "Product Picture", "value": "ABA900B305E119BE6FBDBBB04350BCBB0000"},
    {"label": "Publicity Materials", "value": "ABA900B305E23A25406D3D9B463CBBC40000"},
    {"label": "Device Quick Guide", "value": "ABA900B305E1112E9721153A47EDB7430000"},
    {"label": "Retail", "value": "ABA900B305E2B3750AF69B8849759D460000"},
    {"label": "User Manual", "value": "ABA900B305E21AA3FEA0B43D4FEFBCA10000"},
    {"label": "Vehicle", "value": "ABA900B305E3409E3AAD26784354B2990000"},
    {"label": "Product Video", "value": "ABA900B305E365B05FD5D36E49578DC40000"},
]
DEFAULT_ALLOWED_EXTENSIONS = [".pdf", ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx"]
DEFAULT_BLOCKED_EXTENSIONS = [
    ".mp4",".avi",".mov",".mkv",".wmv",".flv",".webm",
    ".mp3",".wav",".aac",".m4a",".ogg",".flac",".wma",
]
DEFAULT_GROUP_KEYWORDS = ["certificate"]
DEFAULT_PATH_FRAGMENTS = ["/certificate/"]
DEFAULT_CONFIG_FILE = Path("chainway_downloader_config_v1.json")

DEFAULT_CONFIG = {
    "server": {
        "base_url": "http://119.136.31.217:9890/",
        "filemanager_url": "http://119.136.31.217:9890/filemanager/filemanager.aspx",
        "main_hash_url": "http://119.136.31.217:9890/main.aspx#/filemanager/filemanager.aspx",
        "download_url_template": "http://119.136.31.217:9890/filemanager/FileDownload.ashx?id={guid}",
    },
    "login": {
        "username": "",
        "password": "",
        "username_env": "CHAINWAY_MIS_USERNAME",
        "password_env": "CHAINWAY_MIS_PASSWORD",
    },
    "search": {
        "terms": DEFAULT_SEARCH_TERMS,
        "group_options": DEFAULT_GROUP_OPTIONS,
        "selected_group_values": ["ABA900B305DB751D1FE1135E46CAA9130000"],
        "group_keywords": DEFAULT_GROUP_KEYWORDS,
        "path_fragments": DEFAULT_PATH_FRAGMENTS,
        "page_size": 100,
    },
    "filters": {
        "allowed_extensions": DEFAULT_ALLOWED_EXTENSIONS,
        "blocked_extensions": DEFAULT_BLOCKED_EXTENSIONS,
    },
    "paths": {
        "profile_dir": "chainway_mis_profile",
        "download_dir": "chainway_downloads",
        "state_file": "chainway_download_state_v13.json",
        "legacy_state_file": "chainway_download_state_v12.json",
        "debug_dir": "chainway_debug_v13",
        "status_file": "chainway_runtime_status_v1.json",
    },
    "runtime": {
        "headless": False,
        "chunk_size": 1024 * 512,
        "progress_update_interval": 0.35,
        "postback_wait_seconds": 3.5,
        "between_downloads_seconds": 1.0,
        "between_searches_seconds": 0.8,
        "max_session_recovery_per_search": 2,
        "keepalive_interval_seconds": 20,
        "session_recovery_timeout_seconds": 0,
        "session_recovery_poll_seconds": 2.0,
        "prompt_for_manual_ready": True,
    },
}

BASE_URL = DEFAULT_CONFIG["server"]["base_url"]
FILEMANAGER_URL = DEFAULT_CONFIG["server"]["filemanager_url"]
MAIN_HASH_URL = DEFAULT_CONFIG["server"]["main_hash_url"]
DOWNLOAD_URL_TEMPLATE = DEFAULT_CONFIG["server"]["download_url_template"]

SEARCH_TERMS = list(DEFAULT_SEARCH_TERMS)
GROUP_OPTIONS = copy.deepcopy(DEFAULT_GROUP_OPTIONS)
SELECTED_GROUP_VALUES = {"ABA900B305DB751D1FE1135E46CAA9130000"}
GROUP_KEYWORDS = {item.lower() for item in DEFAULT_GROUP_KEYWORDS}
PATH_FRAGMENTS = {item.lower() for item in DEFAULT_PATH_FRAGMENTS}

PROFILE_DIR = Path(DEFAULT_CONFIG["paths"]["profile_dir"])
DOWNLOAD_DIR = Path(DEFAULT_CONFIG["paths"]["download_dir"])
STATE_FILE = Path(DEFAULT_CONFIG["paths"]["state_file"])
LEGACY_STATE_FILE = Path(DEFAULT_CONFIG["paths"]["legacy_state_file"])
DEBUG_DIR = Path(DEFAULT_CONFIG["paths"]["debug_dir"])
STATUS_FILE = Path(DEFAULT_CONFIG["paths"]["status_file"])

PAGE_SIZE = int(DEFAULT_CONFIG["search"]["page_size"])
POSTBACK_WAIT_SECONDS = float(DEFAULT_CONFIG["runtime"]["postback_wait_seconds"])
BETWEEN_DOWNLOADS_SECONDS = float(DEFAULT_CONFIG["runtime"]["between_downloads_seconds"])
BETWEEN_MODELS_SECONDS = float(DEFAULT_CONFIG["runtime"]["between_searches_seconds"])
HEADLESS = bool(DEFAULT_CONFIG["runtime"]["headless"])
CHUNK_SIZE = int(DEFAULT_CONFIG["runtime"]["chunk_size"])
PROGRESS_UPDATE_INTERVAL = float(DEFAULT_CONFIG["runtime"]["progress_update_interval"])
MAX_SESSION_RECOVERY_PER_MODEL = int(DEFAULT_CONFIG["runtime"]["max_session_recovery_per_search"])
KEEPALIVE_INTERVAL_SECONDS = float(DEFAULT_CONFIG["runtime"]["keepalive_interval_seconds"])
SESSION_RECOVERY_TIMEOUT_SECONDS = float(DEFAULT_CONFIG["runtime"]["session_recovery_timeout_seconds"])
SESSION_RECOVERY_POLL_SECONDS = float(DEFAULT_CONFIG["runtime"]["session_recovery_poll_seconds"])
PROMPT_FOR_MANUAL_READY = bool(DEFAULT_CONFIG["runtime"]["prompt_for_manual_ready"])

LOGIN_USERNAME_ENV = DEFAULT_CONFIG["login"]["username_env"]
LOGIN_PASSWORD_ENV = DEFAULT_CONFIG["login"]["password_env"]
LOGIN_USERNAME = DEFAULT_CONFIG["login"]["username"]
LOGIN_PASSWORD = DEFAULT_CONFIG["login"]["password"]

ALLOWED_CERT_EXTENSIONS = {item.lower() for item in DEFAULT_ALLOWED_EXTENSIONS}
BLOCKED_MEDIA_EXTENSIONS = {item.lower() for item in DEFAULT_BLOCKED_EXTENSIONS}
RUNTIME_STATUS: Dict[str, Any] = {}


class SessionExpiredError(RuntimeError):
    pass


class SessionKeepAlive:
    def __init__(self, session: requests.Session, url: str, interval_seconds: float) -> None:
        self.session = session
        self.url = url
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="chainway-keepalive", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=max(self.interval_seconds, 1.0) + 2.0)
        self.session.close()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                resp = self.session.get(self.url, allow_redirects=False, timeout=30)
                resp.close()
            except Exception:
                pass


def deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_string_list(values: List[Any]) -> List[str]:
    out = []
    for value in values or []:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def normalize_group_options(values: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    options = []
    seen = set()
    for item in values or []:
        label = str((item or {}).get("label", "")).strip()
        value = str((item or {}).get("value", "")).strip()
        if not label or not value:
            continue
        key = (label, value)
        if key in seen:
            continue
        seen.add(key)
        options.append({"label": label, "value": value})
    return options


def ensure_config_file(config_path: Path) -> None:
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(config_path: Path) -> Dict[str, Any]:
    ensure_config_file(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = deep_merge_dict(DEFAULT_CONFIG, raw)
    config["search"]["terms"] = normalize_string_list(config["search"].get("terms", []))
    config["search"]["group_options"] = normalize_group_options(config["search"].get("group_options", []))
    config["search"]["selected_group_values"] = normalize_string_list(config["search"].get("selected_group_values", []))
    config["search"]["group_keywords"] = normalize_string_list(config["search"].get("group_keywords", []))
    config["search"]["path_fragments"] = normalize_string_list(config["search"].get("path_fragments", []))
    config["filters"]["allowed_extensions"] = normalize_string_list(config["filters"].get("allowed_extensions", []))
    config["filters"]["blocked_extensions"] = normalize_string_list(config["filters"].get("blocked_extensions", []))
    return config


def save_config(config_path: Path, config: Dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def save_runtime_status() -> None:
    if not STATUS_FILE:
        return
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(RUNTIME_STATUS)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def set_runtime_status(**changes: Any) -> None:
    RUNTIME_STATUS.update(changes)
    save_runtime_status()


def reset_runtime_status() -> None:
    RUNTIME_STATUS.clear()
    set_runtime_status(
        running=False,
        phase="idle",
        message="Připraveno",
        downloaded_count=0,
        matched_count=0,
        error_count=0,
        current_group="",
        current_search="",
        current_file="",
        current_downloaded_bytes=0,
        current_total_bytes=0,
        current_speed_bps=0,
        task_index=0,
        task_total=0,
        current_item_index=0,
        current_item_total=0,
    )


def resolve_config_path(base_dir: Path, value: str) -> Path:
    path = Path(str(value).strip())
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def active_group_labels_from_config(group_options: List[Dict[str, str]], selected_group_values: List[str]) -> set[str]:
    selected = {value.strip() for value in selected_group_values if str(value).strip()}
    labels = set()
    for item in group_options:
        if item["value"] in selected:
            labels.add(item["label"].strip().lower())
    return labels


def active_group_items_from_config(group_options: List[Dict[str, str]], selected_group_values: List[str]) -> List[Dict[str, str]]:
    selected = {value.strip() for value in selected_group_values if str(value).strip()}
    if not selected:
        return [{"label": "", "value": ""}]
    items = [item for item in group_options if item["value"] in selected]
    return items or [{"label": "", "value": ""}]


def apply_runtime_config(config: Dict[str, Any], config_path: Path) -> None:
    global BASE_URL, FILEMANAGER_URL, MAIN_HASH_URL, DOWNLOAD_URL_TEMPLATE
    global SEARCH_TERMS, GROUP_OPTIONS, SELECTED_GROUP_VALUES, GROUP_KEYWORDS, PATH_FRAGMENTS
    global PROFILE_DIR, DOWNLOAD_DIR, STATE_FILE, LEGACY_STATE_FILE, DEBUG_DIR, STATUS_FILE
    global PAGE_SIZE, POSTBACK_WAIT_SECONDS, BETWEEN_DOWNLOADS_SECONDS, BETWEEN_MODELS_SECONDS
    global HEADLESS, CHUNK_SIZE, PROGRESS_UPDATE_INTERVAL, MAX_SESSION_RECOVERY_PER_MODEL
    global KEEPALIVE_INTERVAL_SECONDS, SESSION_RECOVERY_TIMEOUT_SECONDS, SESSION_RECOVERY_POLL_SECONDS
    global PROMPT_FOR_MANUAL_READY, LOGIN_USERNAME_ENV, LOGIN_PASSWORD_ENV, LOGIN_USERNAME, LOGIN_PASSWORD
    global ALLOWED_CERT_EXTENSIONS, BLOCKED_MEDIA_EXTENSIONS

    base_dir = config_path.resolve().parent
    server = config["server"]
    search = config["search"]
    filters = config["filters"]
    paths = config["paths"]
    runtime = config["runtime"]
    login = config["login"]

    BASE_URL = str(server["base_url"]).strip()
    FILEMANAGER_URL = str(server["filemanager_url"]).strip()
    MAIN_HASH_URL = str(server["main_hash_url"]).strip()
    DOWNLOAD_URL_TEMPLATE = str(server["download_url_template"]).strip()

    SEARCH_TERMS = normalize_string_list(search.get("terms", []))
    GROUP_OPTIONS = normalize_group_options(search.get("group_options", []))
    SELECTED_GROUP_VALUES = {item.strip() for item in normalize_string_list(search.get("selected_group_values", []))}
    GROUP_KEYWORDS = {item.lower() for item in normalize_string_list(search.get("group_keywords", []))}
    GROUP_KEYWORDS.update(active_group_labels_from_config(GROUP_OPTIONS, list(SELECTED_GROUP_VALUES)))
    PATH_FRAGMENTS = {item.lower() for item in normalize_string_list(search.get("path_fragments", []))}

    PROFILE_DIR = resolve_config_path(base_dir, paths["profile_dir"])
    DOWNLOAD_DIR = resolve_config_path(base_dir, paths["download_dir"])
    STATE_FILE = resolve_config_path(base_dir, paths["state_file"])
    LEGACY_STATE_FILE = resolve_config_path(base_dir, paths["legacy_state_file"])
    DEBUG_DIR = resolve_config_path(base_dir, paths["debug_dir"])
    STATUS_FILE = resolve_config_path(base_dir, paths["status_file"])

    PAGE_SIZE = max(1, int(search["page_size"]))
    POSTBACK_WAIT_SECONDS = float(runtime["postback_wait_seconds"])
    BETWEEN_DOWNLOADS_SECONDS = float(runtime["between_downloads_seconds"])
    BETWEEN_MODELS_SECONDS = float(runtime["between_searches_seconds"])
    HEADLESS = bool(runtime["headless"])
    CHUNK_SIZE = max(1024, int(runtime["chunk_size"]))
    PROGRESS_UPDATE_INTERVAL = float(runtime["progress_update_interval"])
    MAX_SESSION_RECOVERY_PER_MODEL = max(1, int(runtime["max_session_recovery_per_search"]))
    KEEPALIVE_INTERVAL_SECONDS = max(5.0, float(runtime["keepalive_interval_seconds"]))
    SESSION_RECOVERY_TIMEOUT_SECONDS = max(0.0, float(runtime["session_recovery_timeout_seconds"]))
    SESSION_RECOVERY_POLL_SECONDS = max(0.5, float(runtime["session_recovery_poll_seconds"]))
    PROMPT_FOR_MANUAL_READY = bool(runtime["prompt_for_manual_ready"])

    LOGIN_USERNAME_ENV = str(login["username_env"]).strip() or LOGIN_USERNAME_ENV
    LOGIN_PASSWORD_ENV = str(login["password_env"]).strip() or LOGIN_PASSWORD_ENV
    LOGIN_USERNAME = str(login.get("username", "")).strip()
    LOGIN_PASSWORD = str(login.get("password", ""))

    ALLOWED_CERT_EXTENSIONS = {item.lower() for item in normalize_string_list(filters.get("allowed_extensions", []))}
    BLOCKED_MEDIA_EXTENSIONS = {item.lower() for item in normalize_string_list(filters.get("blocked_extensions", []))}


def model_matches(text: str, model_code: str) -> bool:
    """
    Přísnější párování modelu:
    - R1 nesmí matchnout UR1A ani SR160
    - hledáme model jako samostatný token, ne jen substring
    - oddělovače typu mezera, /, _, -, &, (, ) jsou OK
    """
    hay = (text or "").upper()
    model = (model_code or "").upper().strip()
    if not model:
        return True
    pattern = rf'(?<![A-Z0-9]){re.escape(model)}(?![A-Z0-9])'
    return re.search(pattern, hay) is not None


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def safe_filename(name: str) -> str:
    return (re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())[:180] or "soubor").strip()


def normalize_remote_filename(name: str) -> str:
    value = (name or "").strip().strip('"').strip("'")
    if not value:
        return ""
    if "%" in value or ("+" in value and " " not in value):
        value = unquote_plus(value)
    else:
        value = unquote(value)
    value = value.replace("\\", "/").rstrip("/")
    return value.split("/")[-1].strip()


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True, write_through=True)
        except Exception:
            pass


def human_bytes(num: float) -> str:
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < step or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= step
    return f"{value:.2f} TB"


def load_state() -> Dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if LEGACY_STATE_FILE.exists():
        return json.loads(LEGACY_STATE_FILE.read_text(encoding="utf-8"))
    return {
        "downloaded_keys": [],
        "seen_keys": [],
        "matches": [],
        "errors": [],
        "skipped_noncert": [],
        "debug_files": [],
    }


def save_state(state: Dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def has_downloaded_guid(downloaded_keys: set[str], guid: str) -> bool:
    if guid in downloaded_keys:
        return True
    return any(key.endswith(f"|{guid}") for key in downloaded_keys)


def build_requests_session_from_cookies(cookies: List[Dict]) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": FILEMANAGER_URL,
    })
    for c in cookies:
        s.cookies.set(
            c["name"],
            c["value"],
            domain=(c.get("domain") or "").lstrip("."),
            path=c.get("path", "/"),
        )
    return s


def restart_keepalive(keepalive: SessionKeepAlive | None, cookies: List[Dict]) -> SessionKeepAlive:
    if keepalive is not None:
        keepalive.stop()
    keepalive = SessionKeepAlive(
        session=build_requests_session_from_cookies(cookies),
        url=FILEMANAGER_URL,
        interval_seconds=KEEPALIVE_INTERVAL_SECONDS,
    )
    keepalive.start()
    return keepalive


def get_login_credentials() -> Tuple[str, str] | None:
    username = (os.getenv(LOGIN_USERNAME_ENV) or "").strip()
    password = os.getenv(LOGIN_PASSWORD_ENV) or ""
    if not username:
        username = LOGIN_USERNAME
    if not password:
        password = LOGIN_PASSWORD
    if username and password:
        return username, password
    return None


def extract_filename(resp: requests.Response, fallback_name: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.IGNORECASE)
    if m:
        return safe_filename(normalize_remote_filename(m.group(1)))
    return safe_filename(normalize_remote_filename(fallback_name))


def click_english_if_present(page) -> None:
    selectors = ['text=English', 'label:has-text("English")', 'span:has-text("English")', 'td:has-text("English")']
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible():
                    try:
                        el.click(timeout=1500)
                        time.sleep(1.0)
                        return
                    except Exception:
                        pass
        except Exception:
            pass


def autofill_login_form_if_possible(page) -> bool:
    creds = get_login_credentials()
    if not creds:
        return False
    username, password = creds
    js = r"""
    ({username, password}) => {
      const setValue = (name, value) => {
        const el = document.querySelector(`[name="${name}"]`);
        if (!el) return false;
        const tag = (el.tagName || '').toUpperCase();
        const proto =
          tag === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype :
          tag === 'SELECT' ? window.HTMLSelectElement.prototype :
          window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) setter.call(el, value);
        else el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      };

      const userOk = setValue('Window1$SimpleForm1$tbxUserName', username);
      const passOk = setValue('Window1$SimpleForm1$tbxPassword', password);
      const captcha = document.querySelector('[name="Window1$SimpleForm1$Panel1$tbxCaptcha"]');
      if (captcha && typeof captcha.focus === 'function') captcha.focus();
      return { userOk, passOk, focusedCaptcha: Boolean(captcha) };
    }
    """
    try:
        result = page.evaluate(js, {"username": username, "password": password})
    except Exception:
        return False
    return bool(result and (result.get("userOk") or result.get("passOk")))


def wait_after_postback(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(POSTBACK_WAIT_SECONDS)


def do_postback(page, event_target: str, event_argument: str, setters: List[dict] | None = None) -> None:
    js = r"""
    ({event_target, event_argument, setters}) => {
      const setValue = (name, value) => {
        const el = document.querySelector(`[name="${name}"]`);
        if (!el) return false;
        const tag = (el.tagName || '').toUpperCase();
        const proto =
          tag === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype :
          tag === 'SELECT' ? window.HTMLSelectElement.prototype :
          window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) setter.call(el, value);
        else el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      };

      for (const item of (setters || [])) setValue(item.name, item.value);

      if (typeof __doPostBack === 'function') {
        __doPostBack(event_target, event_argument);
        return { ok: true };
      }

      const form = document.forms[0];
      if (!form) return { ok: false, reason: 'No form found' };
      const et = document.querySelector('[name="__EVENTTARGET"]');
      const ea = document.querySelector('[name="__EVENTARGUMENT"]');
      if (et) et.value = event_target;
      if (ea) ea.value = event_argument;
      form.submit();
      return { ok: true };
    }
    """
    result = page.evaluate(js, {
        "event_target": event_target,
        "event_argument": event_argument,
        "setters": setters or [],
    })
    if not result or not result.get("ok"):
        raise RuntimeError(f"Postback selhal: {event_target} | {event_argument} | {result}")
    wait_after_postback(page)


def do_search(page, term: str) -> None:
    do_postback(
        page,
        "Panel1$Form2$FormRow1$ttbSearchMessage",
        "Trigger$2",
        setters=[
            {"name": "Panel1$Form2$FormRow1$ttbSearchMessage", "value": term.lower()},
            {"name": "Panel1_Grid1_pagingToolbar_pageNumberBox", "value": "1"},
        ],
    )


def set_page_size_100(page) -> None:
    do_postback(
        page,
        "Panel1$Grid1$ddlGridPageSize",
        "",
        setters=[
            {"name": "Panel1$Grid1$ddlGridPageSize$Value", "value": str(PAGE_SIZE)},
            {"name": "Panel1$Grid1$ddlGridPageSize", "value": str(PAGE_SIZE)},
        ],
    )


def set_group_filter(page, group_value: str, group_label: str) -> None:
    if not (group_value or group_label):
        return
    do_postback(
        page,
        "Panel1$Form2$FormRow1$ddlGroup",
        "",
        setters=[
            {"name": "Panel1$Form2$FormRow1$ddlGroup$Value", "value": group_value},
            {"name": "Panel1$Form2$FormRow1$ddlGroup", "value": group_label},
            {"name": "Panel1_Grid1_pagingToolbar_pageNumberBox", "value": "1"},
        ],
    )


def go_to_page(page, page_no: int) -> None:
    if page_no <= 1:
        return
    do_postback(
        page,
        "Panel1$Grid1",
        f"Page${page_no - 1}$0",
        setters=[
            {"name": "Panel1_Grid1_pagingToolbar_pageNumberBox", "value": str(page_no)},
            {"name": "Panel1$Grid1$ddlGridPageSize$Value", "value": str(PAGE_SIZE)},
            {"name": "Panel1$Grid1$ddlGridPageSize", "value": str(PAGE_SIZE)},
        ],
    )


def extract_total_pages(page) -> int:
    js = r"""
    () => {
      const text = document.body.innerText || '';
      let m = text.match(/共\s*(\d+)\s*页/);
      if (m) return Number(m[1]);
      const nums = [...document.querySelectorAll('a,span,td,div')]
        .map(el => (el.innerText || '').trim())
        .filter(t => /^\d+$/.test(t))
        .map(Number)
        .filter(n => n > 0 && n < 10000);
      if (nums.length) return Math.max(...nums);
      return 1;
    }
    """
    try:
        return max(1, int(page.evaluate(js) or 1))
    except Exception:
        return 1


def extract_rows_current_page(page) -> List[Dict]:
    js = r"""
    () => {
      const out = [];
      const pushUnique = (rows) => {
        const seen = new Set();
        return rows.filter(r => {
          const key = `${r.guid || ''}||${r.display_name || ''}||${r.file_path || ''}||${r.rowid || ''}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      };

      try {
        if (window.F) {
          let grid = null;
          try { grid = F('Panel1_Grid1'); } catch(e) {}
          let sourceRows = null;
          if (grid && grid.f_state && Array.isArray(grid.f_state.F_Rows)) sourceRows = grid.f_state.F_Rows;
          else if (grid && Array.isArray(grid.data)) sourceRows = grid.data;

          if (sourceRows && sourceRows.length) {
            for (const r of sourceRows) {
              const a = r.f0 || [];
              const ids = r.f1 || [];
              out.push({
                guid: ids[0] || '',
                rowid: r.f6 || '',
                display_name: a[1] || '',
                language: a[2] || '',
                file_path: a[3] || '',
                file_level: a[4] || '',
                file_group: a[5] || '',
                file_size: a[6] || '',
                created_at: a[7] || '',
                creator: a[8] || '',
              });
            }
            return pushUnique(out);
          }
        }
      } catch(e) {}

      const html = document.documentElement.outerHTML;
      const rows = [];
      const re = /"f0":\s*\[(.*?)\]\s*,\s*"f1":\s*\[(.*?)\]\s*,\s*"f6":\s*"([^"]+)"/gs;
      let m;
      while ((m = re.exec(html)) !== null) {
        const rawF0 = m[1];
        const rawF1 = m[2];
        const rowid = m[3] || '';
        const strs = [...rawF0.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map(x => {
          try { return JSON.parse('"' + x[1] + '"'); } catch(e) { return x[1]; }
        });
        const ids = [...rawF1.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map(x => {
          try { return JSON.parse('"' + x[1] + '"'); } catch(e) { return x[1]; }
        });
        rows.push({
          guid: ids[0] || '',
          rowid: rowid,
          display_name: strs[1] || '',
          language: strs[2] || '',
          file_path: strs[3] || '',
          file_level: strs[4] || '',
          file_group: strs[5] || '',
          file_size: strs[6] || '',
          created_at: strs[7] || '',
          creator: strs[8] || '',
        });
      }
      return pushUnique(rows);
    }
    """
    return page.evaluate(js)


def page_has_login_form(page) -> bool:
    js = r"""
    () => {
      const body = document.body.innerText || '';
      const url = location.href || '';
      if (/default\.aspx/i.test(url) && /returnurl/i.test(url)) return true;
      if (document.querySelector('[name="Window1$SimpleForm1$tbxUserName"]')) return true;
      if (document.querySelector('[name="Window1$SimpleForm1$tbxPassword"]')) return true;
      if (/captcha/i.test(body) || /验证码/.test(body)) {
        if (/user|login|password|用户名|密码/.test(body)) return true;
      }
      return false;
    }
    """
    try:
        return bool(page.evaluate(js))
    except Exception:
        return False


def page_has_file_grid(page) -> bool:
    js = r"""
    () => {
      const body = document.body.innerText || '';
      if (document.querySelector('[name="Panel1$Form2$FormRow1$ttbSearchMessage"]')) return true;
      if (document.querySelector('[name="Panel1$Grid1$ddlGridPageSize$Value"]')) return true;
      if (document.querySelector('tr.f-grid-row')) return true;
      if (/在展示名称中搜索/.test(body)) return true;
      if (/document download/i.test(body) || /文档下载/.test(body)) return true;
      if (/certificate/i.test(body) && /english/i.test(body)) return true;
      try { if (window.F && F('Panel1_Grid1')) return true; } catch(e) {}
      return false;
    }
    """
    try:
        return bool(page.evaluate(js))
    except Exception:
        return False


def save_debug_snapshot(page, tag: str) -> Tuple[Path, Path]:
    DEBUG_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    html_path = DEBUG_DIR / f"{ts}_{safe_filename(tag)}.html"
    png_path = DEBUG_DIR / f"{ts}_{safe_filename(tag)}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path.write_text("unable to save html", encoding="utf-8")
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception:
        png_path.write_text("unable to save screenshot", encoding="utf-8")
    return html_path, png_path


def wait_for_relogin(page) -> None:
    print()
    print("Vypadá to na ztrátu session nebo přihlašovací stránku.")
    wait_forever = SESSION_RECOVERY_TIMEOUT_SECONDS <= 0
    if get_login_credentials():
        print("Přihlašovací údaje doplním automaticky. Stačí opsat captcha a potvrdit login v prohlížeči.")
        if wait_forever:
            set_runtime_status(phase="waiting_for_captcha", message="Čekám bez timeoutu na opsání captcha a potvrzení loginu.")
        else:
            set_runtime_status(phase="waiting_for_captcha", message="Čekám na opsání captcha a potvrzení loginu.")
    else:
        print("Přihlas se v otevřeném prohlížeči. Jakmile bude tabulka zase dostupná, skript pokračuje sám.")
        if wait_forever:
            set_runtime_status(phase="waiting_for_login", message="Čekám bez timeoutu na ruční přihlášení.")
        else:
            set_runtime_status(phase="waiting_for_login", message="Čekám na ruční přihlášení.")

    deadline = None if wait_forever else (time.time() + SESSION_RECOVERY_TIMEOUT_SECONDS)
    announced_login_page = False
    attempted_fill = False

    while deadline is None or time.time() < deadline:
        try:
            if page_has_file_grid(page) and not page_has_login_form(page):
                return

            if page_has_login_form(page):
                if not announced_login_page:
                    print("Přihlašovací stránka detekována, čekám na dokončení captcha.")
                    announced_login_page = True
                    set_runtime_status(phase="waiting_for_captcha", message="Přihlašovací stránka detekována, čekám na captcha.")
                if not attempted_fill and autofill_login_form_if_possible(page):
                    print("Uživatelské jméno a heslo jsou předvyplněné.")
                    attempted_fill = True
                    set_runtime_status(message="Uživatelské jméno a heslo jsou předvyplněné, čekám na captcha.")
                time.sleep(SESSION_RECOVERY_POLL_SECONDS)
                continue

            for url in (FILEMANAGER_URL, MAIN_HASH_URL):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=120000)
                    time.sleep(2)
                    click_english_if_present(page)
                    if page_has_file_grid(page) and not page_has_login_form(page):
                        set_runtime_status(phase="ready", message="Session obnovena, pokračuji.")
                        return
                    if page_has_login_form(page):
                        break
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(SESSION_RECOVERY_POLL_SECONDS)

    html_path, png_path = save_debug_snapshot(page, "session_recovery_timeout")
    raise SessionExpiredError(f"Session recovery timeout. Debug: {html_path} | {png_path}")


def ensure_active_session(page, reason: str = "", allow_retry_reload: bool = True) -> None:
    login_page = page_has_login_form(page)
    has_grid = page_has_file_grid(page)

    if not login_page and has_grid:
        return

    if allow_retry_reload:
        for url in (FILEMANAGER_URL, MAIN_HASH_URL):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=120000)
                time.sleep(2)
                click_english_if_present(page)
                if not page_has_login_form(page) and page_has_file_grid(page):
                    return
            except Exception:
                pass

    html_path, png_path = save_debug_snapshot(page, f"session_problem_{reason or 'unknown'}")
    raise SessionExpiredError(f"Session neplatná. Debug: {html_path} | {png_path}")


def should_keep_row(search_term: str, row: Dict) -> Tuple[bool, str]:
    display_name = row.get("display_name", "")
    file_path = row.get("file_path", "")
    file_group = row.get("file_group", "")
    file_level = row.get("file_level", "")
    haystack = " ".join([display_name, file_path, file_group, file_level])
    if not model_matches(haystack, search_term):
        return False, "model_mismatch"
    group_ok = True
    normalized_group = norm(file_group)
    normalized_path = norm(file_path)
    if GROUP_KEYWORDS or PATH_FRAGMENTS:
        group_ok = any(keyword in normalized_group for keyword in GROUP_KEYWORDS)
        if not group_ok:
            group_ok = any(fragment in normalized_path for fragment in PATH_FRAGMENTS)
    if not group_ok:
        return False, "not_selected_group"
    ext = Path(file_path or display_name).suffix.lower()
    if ext in BLOCKED_MEDIA_EXTENSIONS:
        return False, "blocked_media_extension"
    if ext and ext not in ALLOWED_CERT_EXTENSIONS:
        return False, f"unsupported_extension:{ext}"
    return True, "ok"


def collect_rows_for_term(page, search_term: str, state: Dict, group_item: Dict[str, str]) -> List[Dict]:
    label = group_item.get("label", "") or "all"
    value = group_item.get("value", "")
    ensure_active_session(page, f"before_group_{label}_{search_term}")
    set_group_filter(page, value, label)
    ensure_active_session(page, f"after_group_{label}_{search_term}", allow_retry_reload=False)
    do_search(page, search_term)
    ensure_active_session(page, f"after_search_{label}_{search_term}", allow_retry_reload=False)
    set_page_size_100(page)
    ensure_active_session(page, f"after_pagesize_{label}_{search_term}", allow_retry_reload=False)

    total_pages = extract_total_pages(page)
    collected = []

    for page_no in range(1, total_pages + 1):
        if page_no > 1:
            go_to_page(page, page_no)
            ensure_active_session(page, f"{label}_{search_term}_page_{page_no}", allow_retry_reload=False)
        collected.extend(extract_rows_current_page(page))

    seen = set()
    out = []
    skipped_noncert = set(state.get("skipped_noncert", []))

    for row in collected:
        keep, reason = should_keep_row(search_term, row)
        if not keep:
            skip_key = f"{search_term}|{label}|{row.get('display_name','')}|{row.get('file_path','')}|{reason}"
            if skip_key not in skipped_noncert:
                skipped_noncert.add(skip_key)
                print(f"    SKIP {row.get('display_name','(bez názvu)')} [{reason}]")
            continue

        key = (row.get("guid",""), row.get("display_name",""), row.get("file_path",""), row.get("rowid",""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    state["skipped_noncert"] = sorted(skipped_noncert)

    if not out:
        html_path, png_path = save_debug_snapshot(page, f"empty_results_{label}_{search_term}")
        if page_has_login_form(page) or not page_has_file_grid(page):
            raise SessionExpiredError(f"Po searchi není aktivní grid. Debug: {html_path} | {png_path}")

    return out


def print_progress_line(label: str, downloaded: int, total: int | None, started_at: float) -> None:
    elapsed = max(time.time() - started_at, 0.001)
    speed = downloaded / elapsed
    set_runtime_status(
        phase="downloading",
        current_file=label,
        current_downloaded_bytes=downloaded,
        current_total_bytes=total or 0,
        current_speed_bps=int(speed),
    )
    if total and total > 0:
        percent = (downloaded / total) * 100
        msg = f"\r         {label} | {percent:6.2f}% | {human_bytes(downloaded)} / {human_bytes(total)} | {human_bytes(speed)}/s"
    else:
        msg = f"\r         {label} | {human_bytes(downloaded)} staženo | {human_bytes(speed)}/s"
    sys.stdout.write(msg[:220])
    sys.stdout.flush()


def save_html_debug_from_response(resp: requests.Response, model_code: str, guid: str, fallback_name: str, state: Dict) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = safe_filename(f"{model_code}_{guid}_{fallback_name}")[:120]
    path = DEBUG_DIR / f"{ts}_{name}.html"
    try:
        path.write_text(resp.text, encoding="utf-8", errors="ignore")
    except Exception:
        path.write_text("unable to save html response", encoding="utf-8")
    debug_files = set(state.get("debug_files", []))
    debug_files.add(str(path))
    state["debug_files"] = sorted(debug_files)


def prepare_download_target(final: Path, tmp: Path) -> Path:
    if final.exists():
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        set_runtime_status(message=f"Soubor už existuje: {final.name}")
        return final
    if tmp.exists():
        size = 0
        try:
            size = tmp.stat().st_size
        except Exception:
            pass
        print(f"      Nalezen nedokončený soubor {tmp.name} ({human_bytes(size)}), mažu ho a stahuji znovu.")
        set_runtime_status(message=f"Nalezen .part {tmp.name}, mažu ho a stahuji znovu.")
        try:
            tmp.unlink()
        except Exception as exc:
            raise RuntimeError(f"Nepodařilo se smazat nedokončený soubor {tmp.name}: {exc}") from exc
    return final


def response_looks_like_login(text: str) -> bool:
    t = norm(text)
    return (
        "returnurl" in t
        or "tbxusername" in t
        or "tbxpassword" in t
        or ("captcha" in t and "login" in t)
        or ("验证码" in text and "密码" in text)
    )


def download_guid(session: requests.Session, guid: str, out_dir: Path, fallback_name: str, model_code: str, state: Dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    url = DOWNLOAD_URL_TEMPLATE.format(guid=guid)

    with session.get(url, stream=True, timeout=180, allow_redirects=False) as resp:
        if resp.status_code in (301, 302, 303, 307, 308):
            raise SessionExpiredError(f"Download redirect na {resp.headers.get('location', '') or 'neznámé místo'}")
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()

        if "text/html" in ctype:
            resp2 = session.get(url, timeout=180, allow_redirects=True)
            save_html_debug_from_response(resp2, model_code, guid, fallback_name, state)
            if response_looks_like_login(resp2.text):
                raise SessionExpiredError("Download vrátil login HTML.")
            raise RuntimeError("Server vrátil HTML místo souboru.")

        filename = extract_filename(resp, fallback_name)
        ext = Path(filename).suffix.lower()
        if ext in BLOCKED_MEDIA_EXTENSIONS:
            raise RuntimeError(f"Blokované multimédium: {ext}")
        if ext and ext not in ALLOWED_CERT_EXTENSIONS:
            raise RuntimeError(f"Nepovolená přípona: {ext}")

        total_header = resp.headers.get("content-length")
        total = int(total_header) if total_header and total_header.isdigit() else None
        tmp = out_dir / (filename + ".part")
        final = out_dir / filename
        existing_final = prepare_download_target(final, tmp)
        if existing_final.exists():
            print(f"      SKIP soubor už existuje: {existing_final.name}")
            return existing_final

        started = time.time()
        last_update = 0.0
        downloaded = 0

        print(f"      STAHUJI {filename}")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_update >= PROGRESS_UPDATE_INTERVAL:
                    print_progress_line(filename, downloaded, total, started)
                    last_update = now

        print_progress_line(filename, downloaded, total, started)
        sys.stdout.write("\n")
        sys.stdout.flush()

        tmp.replace(final)
        return final


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CHAINWAY MIS Downloader")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_FILE), help="Cesta ke konfiguračnímu JSON souboru")
    parser.add_argument("--no-manual-ready", action="store_true", help="Neptej se na Enter před startem")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    config_path = Path(args.config).expanduser()
    config = load_config(config_path)
    if args.no_manual_ready:
        config["runtime"]["prompt_for_manual_ready"] = False
    apply_runtime_config(config, config_path)
    if sync_playwright is None:
        raise RuntimeError("Playwright není nainstalovaný. Spusť: pip install -r requirements-chainway-downloader-v13.txt a playwright install chromium")

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    DEBUG_DIR.mkdir(exist_ok=True)
    reset_runtime_status()
    set_runtime_status(running=True, phase="starting", message="Spouštím downloader.")

    state = load_state()
    downloaded_keys = set(state.get("downloaded_keys", []))
    seen_keys = set(state.get("seen_keys", []))
    errors = list(state.get("errors", []))
    all_matches = list(state.get("matches", []))
    keepalive = None
    group_items = active_group_items_from_config(GROUP_OPTIONS, list(SELECTED_GROUP_VALUES))
    search_terms = SEARCH_TERMS or [""]
    tasks = [(group_item, search_term) for group_item in group_items for search_term in search_terms]
    set_runtime_status(
        downloaded_count=len(downloaded_keys),
        matched_count=len(all_matches),
        error_count=len(errors),
        task_total=len(tasks),
        message="Čekám na aktivní session.",
    )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=HEADLESS,
            accept_downloads=False,
        )
        try:
            page = context.new_page()
            page.goto(FILEMANAGER_URL, wait_until="domcontentloaded", timeout=120000)

            print()
            print("1) Přihlas se do Chainway MIS")
            print("2) Otevři stránku Document Download / 文档下载")
            print("3) Nech otevřenou tabulku")
            if get_login_credentials():
                print(f"Přihlašovací stránku umím předvyplnit z env: {LOGIN_USERNAME_ENV}, {LOGIN_PASSWORD_ENV}")
            if PROMPT_FOR_MANUAL_READY:
                set_runtime_status(phase="waiting_for_manual_ready", message="Čekám na ruční potvrzení startu.")
                input("Až budeš připravený, stiskni Enter... ")
            else:
                print("Čekám automaticky na aktivní session bez potvrzení Enterem.")
                set_runtime_status(phase="waiting_for_login", message="Čekám na otevřenou tabulku nebo login.")

            click_english_if_present(page)
            try:
                ensure_active_session(page, "initial", allow_retry_reload=True)
            except SessionExpiredError:
                print("Počáteční kontrola stránku nepoznala. Zkusím automatické čekání na návrat session.")
                wait_for_relogin(page)
                ensure_active_session(page, "initial_after_retry", allow_retry_reload=True)

            session = build_requests_session_from_cookies(context.cookies())
            keepalive = restart_keepalive(keepalive, context.cookies())
            print(f"Keepalive běží každých {KEEPALIVE_INTERVAL_SECONDS} s.")
            set_runtime_status(phase="ready", message="Session aktivní, začínám zpracování.")

            for idx, (group_item, search_term) in enumerate(tasks, start=1):
                group_label = group_item.get("label", "") or "Všechny skupiny"
                task_label = search_term or "(prázdný výraz)"
                print(f"[{idx}/{len(tasks)}] Skupina: {group_label} | Hledání: {task_label}")
                recovery_count = 0
                set_runtime_status(
                    phase="searching",
                    task_index=idx,
                    task_total=len(tasks),
                    current_item_index=0,
                    current_item_total=0,
                    current_group=group_label,
                    current_search=search_term,
                    current_file="",
                    current_downloaded_bytes=0,
                    current_total_bytes=0,
                    current_speed_bps=0,
                    message=f"Hledám: {group_label} | {task_label}",
                )

                while True:
                    try:
                        page.goto(FILEMANAGER_URL, wait_until="domcontentloaded", timeout=120000)
                        time.sleep(2)
                        click_english_if_present(page)
                        ensure_active_session(page, f"task_start_{group_label}_{task_label}", allow_retry_reload=True)

                        rows = collect_rows_for_term(page, search_term, state, group_item)
                        if not rows:
                            print("    nic relevantního nenalezeno")
                            set_runtime_status(
                                current_item_index=0,
                                current_item_total=0,
                                message=f"Nic relevantního nenalezeno pro {group_label} | {task_label}",
                            )
                            time.sleep(BETWEEN_MODELS_SECONDS)
                            break

                        print(f"    nalezeno {len(rows)} položek")
                        set_runtime_status(
                            current_item_index=0,
                            current_item_total=len(rows),
                            message=f"Nalezeno {len(rows)} položek pro {group_label} | {task_label}",
                        )

                        for row_index, row in enumerate(rows, start=1):
                            set_runtime_status(
                                current_item_index=row_index,
                                current_item_total=len(rows),
                                current_file=normalize_remote_filename(row.get("file_path") or "") or normalize_remote_filename(row.get("display_name") or ""),
                            )
                            unique_key = f"{group_label}|{search_term}|{row.get('display_name','')}|{row.get('file_path','')}"
                            if unique_key not in seen_keys:
                                all_matches.append({
                                    "search_term": search_term,
                                    "selected_group_label": group_label,
                                    "selected_group_value": group_item.get("value", ""),
                                    **row,
                                })
                                seen_keys.add(unique_key)

                            guid = row.get("guid", "")
                            if not guid:
                                msg = f"{group_label} | {search_term} | {row.get('display_name','')} | bez GUID"
                                errors.append(msg)
                                print(f"      FAIL {msg}")
                                set_runtime_status(error_count=len(errors), message=msg)
                                continue

                            download_key = guid
                            if has_downloaded_guid(downloaded_keys, guid):
                                print("    SKIP už staženo")
                                set_runtime_status(message=f"Přeskakuji už stažený GUID {guid}")
                                continue

                            target_dir = DOWNLOAD_DIR / safe_filename(search_term or group_label or "all")
                            fallback_name = (
                                normalize_remote_filename(row.get("file_path") or "")
                                or normalize_remote_filename(row.get("display_name") or "")
                                or f"{guid}.bin"
                            )

                            try:
                                set_runtime_status(
                                    phase="downloading",
                                    current_item_index=row_index,
                                    current_item_total=len(rows),
                                    current_file=fallback_name,
                                    message=f"Stahuji {fallback_name}",
                                )
                                saved = download_guid(session, guid, target_dir, fallback_name, search_term or group_label or "all", state)
                                downloaded_keys.add(download_key)
                                print(f"      OK {saved}")
                                set_runtime_status(
                                    downloaded_count=len(downloaded_keys),
                                    matched_count=len(all_matches),
                                    error_count=len(errors),
                                    message=f"Staženo: {Path(saved).name}",
                                )
                            except SessionExpiredError as e:
                                print(f"      SESSION PROBLÉM {e}")
                                html_path, png_path = save_debug_snapshot(page, f"session_during_download_{group_label}_{task_label}")
                                print(f"      Debug uložen: {html_path} | {png_path}")
                                set_runtime_status(phase="waiting_for_login", message=f"Session problém při stahování {fallback_name}")
                                raise
                            except Exception as e:
                                msg = f"{group_label} | {search_term} | {row.get('display_name','')} | {guid} :: {e}"
                                errors.append(msg)
                                print(f"      FAIL {msg}")
                                set_runtime_status(error_count=len(errors), message=msg)

                            state["downloaded_keys"] = sorted(downloaded_keys)
                            state["seen_keys"] = sorted(seen_keys)
                            state["matches"] = all_matches
                            state["errors"] = errors
                            save_state(state)
                            time.sleep(BETWEEN_DOWNLOADS_SECONDS)

                        time.sleep(BETWEEN_MODELS_SECONDS)
                        break

                    except SessionExpiredError as e:
                        recovery_count += 1
                        if recovery_count > MAX_SESSION_RECOVERY_PER_MODEL:
                            msg = f"{group_label} | {search_term} :: příliš mnoho pokusů o obnovu session :: {e}"
                            errors.append(msg)
                            print(f"    FAIL {msg}")
                            state["errors"] = errors
                            save_state(state)
                            set_runtime_status(phase="error", error_count=len(errors), message=msg)
                            break

                        print(f"    Obnovuji session pro {group_label} | {task_label} (pokus {recovery_count}/{MAX_SESSION_RECOVERY_PER_MODEL})")
                        set_runtime_status(phase="waiting_for_login", message=f"Obnovuji session pro {group_label} | {task_label}")
                        wait_for_relogin(page)
                        session = build_requests_session_from_cookies(context.cookies())
                        keepalive = restart_keepalive(keepalive, context.cookies())
                        set_runtime_status(phase="searching", message=f"Session obnovena pro {group_label} | {task_label}")
                        continue

                    except Exception as e:
                        msg = f"{group_label} | {search_term} :: hledání selhalo :: {e}"
                        errors.append(msg)
                        print(f"    FAIL {msg}")
                        state["downloaded_keys"] = sorted(downloaded_keys)
                        state["seen_keys"] = sorted(seen_keys)
                        state["matches"] = all_matches
                        state["errors"] = errors
                        save_state(state)
                        set_runtime_status(phase="error", error_count=len(errors), message=msg)
                        break
        finally:
            if keepalive is not None:
                keepalive.stop()
            context.close()

    print()
    print("HOTOVO")
    print(f"Staženo: {len(downloaded_keys)}")
    print(f"Záznamů: {len(all_matches)}")
    print(f"Chyb:    {len(errors)}")
    print(f"Soubory: {DOWNLOAD_DIR.resolve()}")
    print(f"Debug:   {DEBUG_DIR.resolve()}")
    print(f"Stav:    {STATE_FILE.resolve()}")
    set_runtime_status(
        running=False,
        phase="done",
        downloaded_count=len(downloaded_keys),
        matched_count=len(all_matches),
        error_count=len(errors),
        current_file="",
        current_downloaded_bytes=0,
        current_total_bytes=0,
        current_speed_bps=0,
        message="Hotovo",
    )


if __name__ == "__main__":
    main()
