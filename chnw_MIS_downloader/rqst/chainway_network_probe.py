#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Chainway MIS network probe
--------------------------
Účel:
- přihlásíš se ručně do MIS
- otevřeš Document Download
- ručně zadáš třeba C61, klikneš Search
- klikneš Next page
- klidně klikneš i jeden Download
- skript mezitím odchytí requesty a uloží je do logu + HAR

Pak z toho půjde zjistit backend endpoint pro:
- search
- paging
- download
a udělat downloader bez nespolehlivého klikání do UI.

Spuštění:
    pip install playwright
    playwright install chromium
    python chainway_network_probe.py
"""

from pathlib import Path
import json
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://119.136.31.217:9890/"
PROFILE_DIR = "chainway_probe_profile"
OUT_DIR = Path("chainway_probe_output")
TEXT_LOG = OUT_DIR / "network_log.txt"
JSON_LOG = OUT_DIR / "network_log.jsonl"
HAR_FILE = OUT_DIR / "chainway_probe.har"

KEYWORDS = [
    "filemanager",
    "filedownload",
    "download",
    "document",
    "search",
    "query",
    "list",
    "grid",
    "page",
    "ashx",
]


def is_interesting(url: str, resource_type: str) -> bool:
    u = (url or "").lower()
    if resource_type in ("xhr", "fetch"):
        return True
    return any(k in u for k in KEYWORDS)


def append_text(line: str) -> None:
    with open(TEXT_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_json(obj: dict) -> None:
    with open(JSON_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    TEXT_LOG.write_text("", encoding="utf-8")
    JSON_LOG.write_text("", encoding="utf-8")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            record_har_path=str(HAR_FILE),
            record_har_mode="full",
        )

        page = context.new_page()

        def on_request(req):
            try:
                if not is_interesting(req.url, req.resource_type):
                    return
                headers = req.headers or {}
                post_data = req.post_data if req.method.upper() in ("POST", "PUT", "PATCH") else None
                entry = {
                    "kind": "request",
                    "method": req.method,
                    "resource_type": req.resource_type,
                    "url": req.url,
                    "headers": headers,
                    "post_data": post_data,
                }
                append_json(entry)
                append_text(f"[REQ] {req.method} {req.resource_type} {req.url}")
                if post_data:
                    append_text(f"      BODY: {post_data[:1000]}")
            except Exception as e:
                append_text(f"[REQ-ERR] {e}")

        def on_response(resp):
            try:
                req = resp.request
                if not is_interesting(resp.url, req.resource_type):
                    return
                headers = resp.headers or {}
                entry = {
                    "kind": "response",
                    "status": resp.status,
                    "resource_type": req.resource_type,
                    "method": req.method,
                    "url": resp.url,
                    "headers": headers,
                }
                append_json(entry)
                append_text(f"[RES] {resp.status} {req.method} {req.resource_type} {resp.url}")
            except Exception as e:
                append_text(f"[RES-ERR] {e}")

        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)

        print()
        print("1) Přihlas se ručně do Chainway MIS")
        print("2) Otevři Document Download / 文档下载")
        print("3) Zadej třeba C61 a klikni Search")
        print("4) Klikni Next page")
        print("5) Klikni aspoň jeden Download")
        print("6) Pak se vrať do terminálu a stiskni Enter")
        input("Hotovo? Stiskni Enter... ")

        # krátká rezerva, aby se dopsaly pozdní requesty
        time.sleep(5)

        context.close()

    print()
    print("Hotovo. Výstupy:")
    print(TEXT_LOG.resolve())
    print(JSON_LOG.resolve())
    print(HAR_FILE.resolve())


if __name__ == "__main__":
    main()
