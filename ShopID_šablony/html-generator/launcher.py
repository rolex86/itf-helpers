from __future__ import annotations

import os
import sys
import time
import socket
import threading
import webbrowser
from pathlib import Path
import logging


APP_NAME = "ShopID_HTML_Generator"


def _app_dir() -> Path:
    d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _setup_logging() -> Path:
    log_path = _app_dir() / "launcher.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    logging.info("=== launcher start ===")
    logging.info("sys.executable=%s", sys.executable)
    logging.info("cwd=%s", os.getcwd())
    return log_path


def _resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def _is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _pick_port(start: int = 8501, end: int = 8599) -> int:
    for p in range(start, end + 1):
        if _is_port_free(p):
            return p
    raise RuntimeError(f"No free port in range {start}-{end}")


def _wait_for_port(host: str, port: int, timeout_s: int = 90) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> None:
    log_path = _setup_logging()

    host = "127.0.0.1"
    port = _pick_port()
    url = f"http://{host}:{port}/"

    # Streamlit konfigurace přes ENV (spolehlivé i v exe)
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_SERVER_ADDRESS"] = host
    os.environ["STREAMLIT_SERVER_PORT"] = str(port)

    # KRITICKÉ: vypnout auto-reload / watchery
    os.environ["STREAMLIT_SERVER_FILEWATCHER_TYPE"] = "none"
    os.environ["STREAMLIT_SERVER_RUN_ON_SAVE"] = "false"

    # KRITICKÉ: vypnout development mode (jinak nejde nastavit port)
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"

    # Doporučené pro lokální exe
    os.environ["STREAMLIT_SERVER_ENABLE_CORS"] = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] = "false"

    app_py = _resource_path("app.py")
    if not app_py.exists():
        logging.error("Missing app.py at: %s", app_py)
        raise FileNotFoundError(f"Missing app.py at: {app_py}")

    logging.info("Using app: %s", app_py)
    logging.info("Opening URL will be: %s", url)
    logging.info("Log file: %s", log_path)

    # Otevřít browser až když port opravdu běží (1x)
    opened_flag = {"done": False}

    def open_browser_when_ready():
        ok = _wait_for_port(host, port, timeout_s=120)
        logging.info("Port ready=%s for %s", ok, url)
        if ok and not opened_flag["done"]:
            opened_flag["done"] = True
            try:
                webbrowser.open(url, new=1)
                logging.info("Browser opened.")
            except Exception as e:
                logging.exception("Failed to open browser: %s", e)

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    try:
        from streamlit.web import cli as stcli
    except Exception as e:
        logging.exception("Failed to import streamlit.web.cli: %s", e)
        raise

    # Headless = streamlit sám browser neotevře
    sys.argv = [
        "streamlit",
        "run",
        str(app_py),

        "--server.address", host,
        "--server.port", str(port),
        "--server.headless", "true",
        "--server.fileWatcherType", "none",
        "--server.runOnSave", "false",

        "--browser.gatherUsageStats", "false",

        # FIX: musí být false, jinak streamlit odmítne server.port
        "--global.developmentMode", "false",
    ]

    logging.info("Starting Streamlit with argv: %s", " ".join(sys.argv))

    try:
        stcli.main()
    except SystemExit as e:
        logging.info("Streamlit exited: %s", e)
    except Exception as e:
        logging.exception("Streamlit crashed: %s", e)
        raise
    finally:
        logging.info("=== launcher end ===")


if __name__ == "__main__":
    main()
