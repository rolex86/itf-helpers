# converter.py
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from bs4 import BeautifulSoup, Tag
from jinja2 import Template


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
SPIN_PLACEHOLDER_BASE = "https://cdn.shopid.cz/data/user-content/360_produkty/"


# ------------------------------------------------------------
# Data model (imports used by app.py)
# ------------------------------------------------------------
@dataclass
class CardItem:
    label: str
    value: str


@dataclass
class Tile:
    title: str
    img_src: str
    text_html: str  # keep HTML as-is


@dataclass
class Extracted:
    language: str  # "cs" | "en" | "de"

    # hero / intro
    title: str
    subtitle: str
    intro_html: str
    hero_badge: str
    hero_img_src: str

    # media
    video_src: str  # youtube embed url
    has_video: bool

    spin_href: str  # URL to 3D/spin
    spin_placeholder_img: str
    has_spin: bool

    # content
    highlights: List[CardItem]
    features: List[CardItem]
    functions: List[CardItem]
    tiles: List[Tile]

    # note (always forced in render)
    note_html: str
    has_note: bool


# ------------------------------------------------------------
# Localizations (your exact strings)
# ------------------------------------------------------------
NOTE_TEXTS: Dict[str, str] = {
    "cs": (
        "V mnoha technických parametrech je CHAINWAY srovnatelný s jinými značkami ve stejné třídě "
        "nebo rozdíly nejsou z pohledu uživatele příliš významné. Hlavní výhodou je však rychlá lokální "
        "prodejní a technická podpora a servisní zastoupení v České republice (od roku 2013) s možností "
        "bezplatného zapůjčení náhradních zařízení po dobu opravy. Díky místnímu skladu máme také řadu "
        "modelů skladem k okamžité expedici a nabízíme bezplatné zapůjčení zařízení pro vývoj a testování "
        "kompatibility se stávajícími aplikacemi. Další z výhod zařízení CHAINWAY oproti jiným značkám jsou "
        "náklady na příslušenství, které výrazně zvyšují pořizovací cenu některých konkurenčních značek, "
        "i když cena samotného zařízení může být nižší než u CHAINWAY."
    ),
    "en": (
        "In many technical parameters CHAINWAY is comparable to other brands in the same class or the "
        "differences are not very significant from the user's point of view. However, the main advantage "
        "is the fast local sales and technical support and service representation in the Czech Republic "
        "(since 2013) with the possibility of free loan of spare equipment for the period of repair. Due "
        "to the local warehouse, we also have a number of models in stock for immediate dispatch and offer "
        "free loan of equipment for development and compatibility testing with existing applications. "
        "Another of the advantages of CHAINWAY equipment over other brands is the cost of accessories, "
        "which significantly increase the purchase price of some competing brands, although the price of "
        "the device itself may be lower than CHAINWAY."
    ),
    "de": (
        "In vielen technischen Parametern ist CHAINWAY mit anderen Marken der gleichen Klasse vergleichbar "
        "oder die Unterschiede sind aus Sicht des Nutzers nicht sehr bedeutend. Der Hauptvorteil ist jedoch "
        "der schnelle lokale Vertrieb und die technische Unterstützung und Servicevertretung in der "
        "Tschechischen Republik (seit 2013) mit der Möglichkeit der kostenlosen Ausleihe von Ersatzgeräten "
        "für die Dauer der Reparatur. Dank des lokalen Lagers haben wir auch eine Reihe von Modellen für den "
        "sofortigen Versand vorrätig und bieten eine kostenlose Leihgabe von Geräten für die Entwicklung und "
        "Kompatibilitätstests mit bestehenden Anwendungen. Ein weiterer Vorteil der CHAINWAY-Geräte gegenüber "
        "anderen Marken sind die Kosten für das Zubehör, die den Kaufpreis einiger konkurrierender Marken "
        "deutlich erhöhen, obwohl der Preis des Geräts selbst niedriger sein kann als bei CHAINWAY."
    ),
}

UI_TEXTS: Dict[str, Dict[str, str]] = {
    "cs": {
        "cta_video": "Pustit video",
        "cta_featfunc": "Vlastnosti a funkce",
        "spin_button": "Spustit 3D prohlídku",
        "spin_note": "Otevře se v nové záložce",
        "features_title": "Vlastnosti",
        "functions_title": "Funkce",
        "spin_alt": "3D prohlídka produktu",
        "hero_img_alt": "Fotografie produktu",
    },
    "en": {
        "cta_video": "Play video",
        "cta_featfunc": "Features and functions",
        "spin_button": "Launch 3D view",
        "spin_note": "Opens in a new tab",
        "features_title": "Features",
        "functions_title": "Functions",
        "spin_alt": "3D product view",
        "hero_img_alt": "Product image",
    },
    "de": {
        "cta_video": "Video abspielen",
        "cta_featfunc": "Eigenschaften und Funktionen",
        "spin_button": "3D-Ansicht starten",
        "spin_note": "Öffnet sich in einem neuen Tab",
        "features_title": "Eigenschaften",
        "functions_title": "Funktionen",
        "spin_alt": "3D-Produktansicht",
        "hero_img_alt": "Produktbild",
    },
}

LABEL_TEXTS: Dict[str, Dict[str, str]] = {
    "cs": {
        "product": "Produkt",
        "feature": "Vlastnost",
        "function": "Funkce",
        "os": "OS",
        "cpu": "CPU",
        "memory": "Paměť",
        "storage": "Úložiště",
        "battery": "Baterie",
        "charging": "Nabíjení",
        "rugged": "Odolnost",
        "display": "Displej",
        "network": "Síť",
        "cellular": "Mobilní síť",
        "wifi": "Wi-Fi",
        "bluetooth": "Bluetooth",
        "gps": "GPS",
        "camera": "Kamera",
        "security": "Bezpečnost",
        "codes": "Kódy",
        "scanner": "Skenování",
        "rfid": "RFID",
        "nfc": "NFC",
        "keyboard": "Klávesnice",
        "ports": "Konektory",
        "audio": "Audio",
        "sim": "SIM",
        "accessories": "Příslušenství",
        "sensors": "Senzory",
        "dimensions": "Rozměry",
        "weight": "Hmotnost",
    },
    "en": {
        "product": "Product",
        "feature": "Feature",
        "function": "Function",
        "os": "OS",
        "cpu": "CPU",
        "memory": "Memory",
        "storage": "Storage",
        "battery": "Battery",
        "charging": "Charging",
        "rugged": "Rugged",
        "display": "Display",
        "network": "Network",
        "cellular": "Cellular",
        "wifi": "Wi-Fi",
        "bluetooth": "Bluetooth",
        "gps": "GPS",
        "camera": "Camera",
        "security": "Security",
        "codes": "Codes",
        "scanner": "Scanning",
        "rfid": "RFID",
        "nfc": "NFC",
        "keyboard": "Keyboard",
        "ports": "Ports",
        "audio": "Audio",
        "sim": "SIM",
        "accessories": "Accessories",
        "sensors": "Sensors",
        "dimensions": "Dimensions",
        "weight": "Weight",
    },
    "de": {
        "product": "Produkt",
        "feature": "Merkmal",
        "function": "Funktion",
        "os": "OS",
        "cpu": "CPU",
        "memory": "Arbeitsspeicher",
        "storage": "Speicher",
        "battery": "Akku",
        "charging": "Laden",
        "rugged": "Robust",
        "display": "Display",
        "network": "Netzwerk",
        "cellular": "Mobilfunk",
        "wifi": "WLAN",
        "bluetooth": "Bluetooth",
        "gps": "GPS",
        "camera": "Kamera",
        "security": "Sicherheit",
        "codes": "Codes",
        "scanner": "Scannen",
        "rfid": "RFID",
        "nfc": "NFC",
        "keyboard": "Tastatur",
        "ports": "Anschlüsse",
        "audio": "Audio",
        "sim": "SIM",
        "accessories": "Zubehör",
        "sensors": "Sensoren",
        "dimensions": "Abmessungen",
        "weight": "Gewicht",
    },
}



def _ui(lang: str) -> Dict[str, str]:
    lang = (lang or "cs").lower().strip()
    return UI_TEXTS.get(lang, UI_TEXTS["cs"])


def _lbl(lang: str, key: str) -> str:
    lang = (lang or "cs").lower().strip()
    return LABEL_TEXTS.get(lang, LABEL_TEXTS["cs"]).get(key, LABEL_TEXTS["cs"].get(key, key))


def _fixed_note_html(lang: str) -> str:
    txt = NOTE_TEXTS.get((lang or "cs").lower().strip(), NOTE_TEXTS["cs"])
    return f'<p style="margin: 0;">{txt}</p>'


def _normalize_spin_placeholder(src: str) -> str:
    """
    Allow user to enter either:
      - full URL (keep)
      - $USER-CONTENT/... (keep)
      - /data/... (convert to https://cdn.shopid.cz + path)
      - plain filename or relative path (prefix with SPIN_PLACEHOLDER_BASE)
    """
    s = (src or "").strip()
    if not s:
        return ""

    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return s
    if low.startswith("$user-content/"):
        return s
    if s.startswith("/"):
        return "https://cdn.shopid.cz" + s

    return SPIN_PLACEHOLDER_BASE + s.lstrip("/")


def _denormalize_spin_placeholder_to_editor(src: str) -> str:
    """
    Reverse of normalize for editor UX:
    - if placeholder is CDN url to SPIN_PLACEHOLDER_BASE -> return only the relative part
      so user can type only filename/path (e.g. "MC50_3D_ph.jpg")
    - otherwise keep as-is (supports $USER-CONTENT, external urls, etc.)
    """
    s = (src or "").strip()
    if not s:
        return ""
    if s.startswith(SPIN_PLACEHOLDER_BASE):
        return s[len(SPIN_PLACEHOLDER_BASE):].lstrip("/")
    return s


# ------------------------------------------------------------
# Extraction helpers
# ------------------------------------------------------------
_EMPTY_P_RE = re.compile(r"^\s*(?:&nbsp;|\u00a0)?\s*$", re.I)


def _strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _remove_empty_paragraphs(soup: BeautifulSoup) -> None:
    for p in soup.find_all("p"):
        html = (p.decode_contents() or "").strip()
        if _EMPTY_P_RE.match(html):
            p.decompose()


def _detect_language(html: str) -> str:
    t = (html or "").lower()
    if any(x in t for x in [" eigenschaften", " funktionen", " öffnet", "gerät", "tschechischen republik"]):
        return "de"
    if any(x in t for x in [" features", " functions", " rugged", " device", " barcode", "opens in a new tab"]):
        return "en"
    return "cs"


def _is_icon_img(img: Tag) -> bool:
    src = (img.get("src") or "").lower()
    if "ikony_ctecky" in src or "svg ikony" in src or src.endswith(".svg"):
        return True
    style = (img.get("style") or "").lower()

    if "height: 30px" in style or "width: 30px" in style or "width:20px" in style:
        return True
    if "height:40px" in style or "height: 40px" in style or "width:40px" in style or "width: 40px" in style:
        return True
    return False


def _first_meaningful_img(soup: BeautifulSoup) -> str:
    for img in soup.find_all("img"):
        if _is_icon_img(img):
            continue
        src = (img.get("src") or "").strip()
        if src:
            return src
    return ""


# ---------------------------
# ✅ FIX: subtitle heuristics
# ---------------------------
def _find_title_subtitle(soup: BeautifulSoup) -> Tuple[str, str]:
    h = soup.find(["h1", "h2"])
    if h:
        title = _strip_ws(h.get_text(" ", strip=True))
        p = h.find_next("p")
        subtitle = _strip_ws(p.get_text(" ", strip=True)) if p else ""

        sub_low = subtitle.lower()
        if (
            not subtitle
            or len(subtitle) > 160
            or "součástí balení" in sub_low
            or subtitle.strip().endswith(":")
        ):
            subtitle = ""

        return title, subtitle

    for strong in soup.find_all("strong"):
        txt = _strip_ws(strong.get_text(" ", strip=True))
        if not txt:
            continue
        if "chainway" in txt.lower() or re.search(r"\b[A-Z]{1,4}\d{1,4}\b", txt):
            title = txt
            p = strong.find_parent().find_next("p") if strong.find_parent() else None
            subtitle = _strip_ws(p.get_text(" ", strip=True)) if p else ""

            sub_low = subtitle.lower()
            if (
                not subtitle
                or len(subtitle) > 160
                or "součástí balení" in sub_low
                or subtitle.strip().endswith(":")
            ):
                subtitle = ""

            return title, subtitle

    return "", ""


def _find_youtube_embed(soup: BeautifulSoup) -> str:
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        if "youtube.com/embed/" in src or "youtube-nocookie.com/embed/" in src:
            return src
    return ""


def _find_spin_3d(soup: BeautifulSoup) -> Tuple[str, str]:
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        if "360_produkty" in src or "/360_produkty/" in src:
            return src, ""
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if "360_produkty" in href or "/360_produkty/" in href:
            img = a.find("img")
            ph = (img.get("src") or "").strip() if img else ""
            return href, ph
    return "", ""


# -----------------------------------------
# ✅ FIX: intro collects p + ul + ol in col
# -----------------------------------------
def _collect_intro_html(soup: BeautifulSoup) -> str:
    stop_words = ["Vlastnosti", "Funkce", "Features", "Functions", "Eigenschaften", "Funktionen", "Funktion"]

    def _has_stop_header(text: str) -> bool:
        t = (text or "").lower()
        return any(w.lower() in t for w in stop_words)

    candidates: List[Tag] = []
    for d in soup.find_all("div"):
        st = (d.get("style") or "").replace(" ", "").lower()
        if "--width_max:50%" in st or "--width_max:55%" in st or "--width_max:60%" in st:
            if d.find("p"):
                candidates.append(d)

    for d in candidates:
        # ✅ NEW: take p + ul + ol in document order
        els = d.find_all(["p", "ul", "ol"])
        if not els:
            continue

        text_len = len(_strip_ws(d.get_text(" ", strip=True)))
        if text_len < 80:
            continue

        chunks: List[str] = []
        for el in els:
            if el.name in ("ul", "ol"):
                if el.find("li") and _strip_ws(el.get_text(" ", strip=True)):
                    chunks.append(str(el))
                continue

            # p
            if _strip_ws(el.get_text(" ", strip=True)):
                chunks.append(str(el))

        if chunks:
            return "\n".join(chunks)

    # fallback scan after heading
    chunks: List[str] = []
    start = soup.find(["h1", "h2"])
    cursor = start if start else soup.body

    for el in cursor.find_all_next(["p", "ul", "ol", "div"], limit=220):
        txt = el.get_text(" ", strip=True)

        if _has_stop_header(txt) and len(txt) < 120:
            break
        if el.find("iframe"):
            break

        if el.name in ["p", "ul", "ol"]:
            if _strip_ws(txt):
                chunks.append(str(el))
        elif el.name == "div":
            if el.find(["p", "ul", "ol"]):
                continue
            t = _strip_ws(txt)
            if 80 <= len(t) <= 1500:
                chunks.append(f"<p>{el.decode_contents()}</p>")

        if len("".join(chunks)) > 8000:
            break

    return "\n".join(chunks)


def _extract_items_features_functions(soup: BeautifulSoup) -> Tuple[List[str], List[str]]:
    features_raw: List[str] = []
    functions_raw: List[str] = []

    feature_headers = {"vlastnosti", "features", "eigenschaften"}
    function_headers = {"funkce", "function", "functions", "funktion", "funktionen"}

    for table in soup.find_all("table"):
        text_blob = _strip_ws(table.get_text(" ", strip=True)).lower()
        if not any(k in text_blob for k in (feature_headers | function_headers)):
            continue

        current = None
        for cell in table.find_all(["td", "th"]):
            t = _strip_ws(cell.get_text(" ", strip=True))
            if not t:
                continue
            low = t.lower()

            if low in feature_headers:
                current = "features"
                continue
            if low in function_headers:
                current = "functions"
                continue

            if len(low) <= 2 or low in {"&nbsp;"}:
                continue

            if 3 <= len(t) <= 160:
                if current == "features":
                    features_raw.append(t)
                elif current == "functions":
                    functions_raw.append(t)

    headers = {
        "Vlastnosti": "features",
        "Features": "features",
        "Eigenschaften": "features",
        "Funkce": "functions",
        "Functions": "functions",
        "Function": "functions",
        "Funktionen": "functions",
        "Funktion": "functions",
    }

    def _style_norm(tag: Tag) -> str:
        return (tag.get("style") or "").replace(" ", "").lower()

    def _collect_values_from_block(block: Tag, header_bar: Optional[Tag]) -> List[str]:
        vals: List[str] = []
        for d in block.find_all("div"):
            if header_bar and header_bar in d.parents:
                continue
            st = _style_norm(d)
            if "--width_max:40%" in st:
                t = _strip_ws(d.get_text(" ", strip=True))
                if t and len(t) <= 160 and t not in headers:
                    vals.append(t)

        out: List[str] = []
        seen = set()
        for x in vals:
            k = x.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        return out

    for hdr in soup.find_all(["strong", "span"]):
        txt = _strip_ws(hdr.get_text(" ", strip=True))
        if txt not in headers:
            continue

        bucket = headers[txt]
        header_bar = hdr.find_parent("div")
        if not header_bar:
            continue

        row = header_bar.find_parent("div")
        if not row:
            continue

        collected = _collect_values_from_block(row, header_bar)
        if bucket == "features":
            features_raw.extend(collected)
        else:
            functions_raw.extend(collected)

        sib = row.find_next_sibling()
        steps = 0
        while sib is not None and steps < 30:
            steps += 1
            if isinstance(sib, Tag):
                inner_headers = [_strip_ws(x.get_text(" ", strip=True)) for x in sib.find_all(["strong", "span"])]
                if any(h in headers for h in inner_headers):
                    break

                collected2 = _collect_values_from_block(sib, None)
                if collected2:
                    if bucket == "features":
                        features_raw.extend(collected2)
                    else:
                        functions_raw.extend(collected2)
            sib = sib.find_next_sibling()

    def dedup(xs: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for x in xs:
            x = _strip_ws(x)
            if not x:
                continue
            k = x.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        return out

    return dedup(features_raw), dedup(functions_raw)


def _norm_low(s: str) -> str:
    s = (s or "").lower()
    for ch in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"]:
        s = s.replace(ch, "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _map_label_value(item: str, bucket: str, lang: str) -> CardItem:
    s = _strip_ws(item)

    # "Label: value" => respektuj explicitní label
    if ":" in s and len(s.split(":", 1)[0]) <= 24:
        a, b = s.split(":", 1)
        return CardItem(label=_strip_ws(a), value=_strip_ws(b))

    low = _norm_low(s)

    # ---------------------------
    # Regex helpers
    # ---------------------------
    def has(pat: str) -> bool:
        return re.search(pat, low, flags=re.I) is not None

    # společné vzory (multijazyčné)
    # pořadí je důležité (nejdřív nej-specifičtější)
    FEATURE_RULES = [
        ("dimensions", r"\b(rozm[eě]ry|dimensions?|abmessungen?)\b|(\b\d+(\.\d+)?\s*(mm|cm)\b.*\b\d+(\.\d+)?\s*(mm|cm)\b)"),
        ("weight", r"\b(v[áa]ha|hmotnost|weight|gewicht)\b|\b\d+(\.\d+)?\s*(g|kg)\b"),

        ("os", r"\b(android|windows|win10|win11|linux|harmonyos|ios)\b"),
        ("cpu", r"\b(qualcomm|snapdragon|mediatek|helio|exynos|octa[- ]?core|hexa[- ]?core|quad[- ]?core|cortex|kirin)\b|\b\d+(\.\d+)?\s*ghz\b"),
        # RAM / memory
        ("memory", r"\b(ram|lpddr\d*|ddr\d*|arbeitsspeicher)\b|\b\d+\s*gb\s*ram\b"),
        # storage / ROM
        ("storage", r"\b(rom|ufs|emmc|flash|storage|speicher)\b|\b\d+\s*gb\b(?!\s*ram\b)"),

        ("battery", r"\b(bateri|battery|akku)\b|\b\d{3,5}\s*mah\b|\b(hot[- ]?swap|hotswap|wechselbar|abnehmbar|removable|vym[eě]niteln)\b"),
        ("charging", r"\b(qc\s*([23]\.0)?|quick\s*charge|fast\s*charge|schnellladen|power\s*delivery|usb[- ]?pd|pd\s*\d+|super\s*charge)\b"),

        ("display", r"\b(displej|display|bildschirm)\b|\b\d+(\.\d+)?\s*(\"|inch|in)\b|\b\d{3,4}\s*[x×]\s*\d{3,4}\b|\b(nits|ips|oled|lcd|gorilla)\b"),
        ("keyboard", r"\b(qwerty|keyboard|kl[aá]vesnic|tastatur|numerisch(e)?\s*tastatur|numeric\s*keypad)\b"),
        ("ports", r"\b(usb[- ]?c|type[- ]?c|pogo|rs[- ]?232|uart|otg|dock|docking|anschluss|konektor|port)\b"),
        ("audio", r"\b(audio|speaker|lautsprecher|mikrofon|microphone|headset|3\.5mm|jack)\b"),
        ("sim", r"\b(e-?sim|nano-?sim|dual\s*sim|sim\s*card|sim-?karte)\b"),

        ("wifi", r"\b(wi[\s-]?fi|wlan)\b|\b802\.11[abgnacax/]*\b|\bdual[- ]?band\b|\b2\.4\s*ghz\b|\b5\s*ghz\b|\b6\s*ghz\b"),
        ("cellular", r"\b(5g|4g|3g|2g|lte|nr\b|volte|wcdma|gsm|gprs|edge|hspa|td[- ]?lte|fdd[- ]?lte)\b"),
        ("bluetooth", r"\bbluetooth\b|\bbt\s*[0-9]\.?[0-9]?\b"),
        ("gps", r"\b(gps|glonass|galileo|beidou|bds)\b"),
        ("camera", r"\b(kamera|camera)\b|\b\d+\s*mp\b|\bautofocus\b|\btof\b"),

        ("rugged", r"\b(ip\d{2}|mil[- ]?std|p[aá]d|drop|fall|sturz|schutzart)\b|\b(-?\d+)\s*[°c]\b|\brobust\b|\bodolnost\b"),
        ("security", r"\b(psam|sam\b|secure\s*element|se\b|tpm|mdm|encryption|verschl[uü]ssel)\b"),
        ("accessories", r"\b(pistol|pistolengriff|gun\s*grip|griff)\b|\b(cradle|charging\s*cradle|dock|holster|strap|lanyard|hand\s*strap|handschlaufe|poutko)\b"),
        ("sensors", r"\b(accelerometer|gyroscope|magnetometer|g-?sensor|proximity|light\s*sensor|fingerprint|barometer|compass)\b|\b(senzor|sensoren)\b"),
    ]

    FUNCTION_RULES = [
        ("rfid", r"\b(rfid|uhf|hf\b|epc\s*gen2|gen2|6c\b|iso\s*18000)\b"),
        ("nfc", r"\b(nfc)\b"),
        # scanning / barcode
        ("scanner", r"\b(scan|scanner|scannen|skenov[aá]n[ií]|imager|laser)\b|\b(1d|2d)\b|\b(bar[\s-]?code|barcode|qr)\b|\b(zebra\s*se|honeywell)\b"),
        # keep "codes" as fallback if you want to separate "codes vs scanning"
        ("codes", r"\b(1d|2d)\b|\b(bar[\s-]?code|barcode|qr)\b|\b([čc][aá]rov)\b"),

        ("wifi", r"\b(wi[\s-]?fi|wlan)\b|\b802\.11[abgnacax/]*\b"),
        ("cellular", r"\b(5g|4g|3g|2g|lte|nr\b|volte|wcdma|gsm|gprs|edge|hspa)\b"),
        ("bluetooth", r"\bbluetooth\b|\bbt\s*[0-9]\.?[0-9]?\b"),
        ("gps", r"\b(gps|glonass|galileo|beidou|bds)\b"),
        ("camera", r"\b(kamera|camera)\b|\b\d+\s*mp\b|\bautofocus\b"),
        ("security", r"\b(psam|sam\b|secure\s*element|tpm|encryption|verschl[uü]ssel)\b"),
    ]

    # ---------------------------
    # Apply mapping
    # ---------------------------
    if bucket == "features":
        for key, pat in FEATURE_RULES:
            if has(pat):
                return CardItem(_lbl(lang, key), s)
        return CardItem(_lbl(lang, "feature"), s)

    # functions
    for key, pat in FUNCTION_RULES:
        if has(pat):
            # scanner has priority over codes (FUNCTION_RULES ordering)
            return CardItem(_lbl(lang, key), s)

    return CardItem(_lbl(lang, "function"), s)



def _build_highlights(title: str, features: List[CardItem], functions: List[CardItem], lang: str) -> List[CardItem]:
    pool = functions + features
    scored: List[Tuple[int, CardItem]] = []

    for it in pool:
        v = _norm_low(it.value)
        score = 0
        if "rfid" in v or "uhf" in v:
            score += 1000
        if "1d" in v or "2d" in v or "čárov" in v or "barcode" in v:
            score += 900

        if "android" in v:
            score += 500
        if "qualcomm" in v or "octa" in v or "snapdragon" in v:
            score += 450
        if "ip" in v:
            score += 350
        if "5g" in v:
            score += 340
        if "wifi" in v or "wi-fi" in v or re.search(r"\bwi fi\b", v):
            score += 330
        if "bater" in v or "mah" in v or "battery" in v:
            score += 320

        scored.append((score, it))

    scored.sort(key=lambda x: x[0], reverse=True)

    picked: List[CardItem] = []
    used = set()
    for _, it in scored:
        key = (it.label.lower(), it.value.lower())
        if key in used:
            continue
        used.add(key)
        picked.append(it)
        if len(picked) >= 4:
            break

    if len(picked) < 4 and title:
        picked.append(CardItem(label=_lbl(lang, "product"), value=title))

    return picked[:4]


def _extract_tiles_c66_style(soup: BeautifulSoup) -> List[Tile]:
    tiles: List[Tile] = []

    # věci, které nechceme (hero, tmavé sekce, gradienty apod.)
    DARK_MARKERS = ["rgb(20, 27, 35)", "rgb(20,27,35)", "#141b23", "#0f1620", "linear-gradient"]

    def _style_norm(el) -> str:
        return ((el.get("style") or "").lower()).replace(" ", "")

    def _has_dark_marker(el) -> bool:
        s = (el.get("style") or "").lower()
        return any(m in s for m in DARK_MARKERS)

    def _strip_nbsp(t: str) -> str:
        return (t or "").replace("\xa0", " ").strip()

    def _text_ok(el) -> bool:
        if not el:
            return False
        t = _strip_ws(el.get_text(" ", strip=True))
        t = _strip_nbsp(t)
        return len(t) >= 8

    def _is_icon_img(img_tag) -> bool:
        src = (img_tag.get("src") or "").lower()
        return ("ikony_ctecky" in src) or ("/svg" in src) or ("ico_" in src)

    def _find_width_block(root, pct: str):
        pct = pct.replace(" ", "")
        # hledáme div, který má ve style --width_max:60% / 40%
        for el in root.find_all("div", recursive=True):
            st = _style_norm(el)
            if f"--width_max:{pct}%" in st or f"--width_max:{pct}.0%" in st:
                return el
        return None

    # Projdi všechny divy a hledej "dlaždicový" wrapper podle struktury 60/40
    for div in soup.find_all("div"):
        # rychlé eliminace
        if div.find("iframe"):
            continue
        if _has_dark_marker(div):
            continue

        h2 = div.find("h2")
        if not h2:
            continue

        # musí existovat 60% a 40% blok (to je signatura dlaždice)
        block60 = _find_width_block(div, "60")
        block40 = _find_width_block(div, "40")
        if not (block60 and block40):
            continue

        # v 60% bloku musí být "hlavní" obrázek (ne ikona)
        img = None
        for im in block60.find_all("img", recursive=True):
            if not _is_icon_img(im):
                img = im
                break
        if not img:
            continue

        title = _strip_ws(h2.get_text(" ", strip=True))
        img_src = (img.get("src") or "").strip()
        if not title or not img_src:
            continue

        # text ber primárně z 40% bloku
        text_html = ""
        text_plain = ""

        # 1) preferuj span (CZ varianta) uvnitř 40% části
        span = block40.find("span")
        if span and _text_ok(span):
            text_html = (span.decode_contents() or "").strip()
            text_plain = _strip_ws(span.get_text(" ", strip=True))
        else:
            # 2) fallback EN/DE: vezmi první smysluplný textový element v 40% bloku
            cand = None
            for el in block40.find_all(["div", "p", "ul", "ol"], recursive=True):
                if el.find("h2") or el.find("img") or el.find("iframe"):
                    continue
                if not _text_ok(el):
                    continue
                cand = el
                break

            if cand:
                if cand.name in ("ul", "ol"):
                    text_html = str(cand)
                else:
                    text_html = (cand.decode_contents() or "").strip()
                text_plain = _strip_ws(cand.get_text(" ", strip=True))

        # když 40% blok nemá text (někdy je text přímo v block40 jako text node), zkus přímo block40
        if not text_plain and _text_ok(block40):
            text_html = (block40.decode_contents() or "").strip()
            text_plain = _strip_ws(block40.get_text(" ", strip=True))

        if not text_plain:
            continue

        tiles.append(Tile(title=title, img_src=img_src, text_html=text_html))

    # deduplikace podle title (case-insensitive)
    out: List[Tile] = []
    seen = set()
    for t in tiles:
        k = (t.title or "").strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t)

    return out




# ------------------------------------------------------------
# NEW TEMPLATE PARSER (round-trip)
# ------------------------------------------------------------
def _looks_like_new_template(soup: BeautifulSoup) -> bool:
    return bool(
        soup.find(id="sid-c5-features")
        or soup.find(id="sid-c5-video")
        or soup.find(id="sid-c5-spin")
        or soup.find(id="sid-c5-heroimg")
    )


def _safe_decode(tag: Optional[Tag]) -> str:
    return tag.decode_contents().strip() if isinstance(tag, Tag) else ""

def _extract_highlights_legacy_cards(soup: BeautifulSoup) -> List[CardItem]:
    """
    Legacy highlights = 4 cards in "Rychlý přehled" section:
    each card has style like "--width_max: calc(25% - 9px)" and contains:
      - kicker div (font-size 12, uppercase)
      - value div (often with <strong>)
    """
    items: List[CardItem] = []

    def _style(t: Tag) -> str:
        return (t.get("style") or "").lower().replace(" ", "")

    for card in soup.find_all("div"):
        st = _style(card)

        # strong filter so we don't catch feature/function tiles ("flex: 1 1 280px")
        if "flex:1 1 280px" in st:
            continue

        # legacy cards are 25% width in a row
        if "--width_max:calc(25%" not in st and "--width_max:25%" not in st:
            continue

        # typical card markers (keep loose but safe)
        if "border-radius:16px" not in st:
            continue
        if "background:#fbfbff" not in st and "background: #fbfbff" not in (card.get("style") or "").lower():
            # allow if someone tweaks bg, but keep at least border
            if "border:1pxsolid#e6e6e6" not in st:
                continue

        # kicker = first inner div that looks like uppercase label
        kicker_div = card.find(lambda t: isinstance(t, Tag) and t.name == "div" and "text-transform:uppercase" in _style(t))
        if not kicker_div:
            kicker_div = card.find(lambda t: isinstance(t, Tag) and t.name == "div" and "font-size:12px" in _style(t))

        # value = next div (or div with strong) inside the card
        value_div = None
        if kicker_div:
            # try next sibling div
            sib = kicker_div.find_next_sibling("div")
            if isinstance(sib, Tag):
                value_div = sib
        if not value_div:
            value_div = card.find(lambda t: isinstance(t, Tag) and t.name == "div" and t.find("strong") is not None)

        kicker = _strip_ws(kicker_div.get_text(" ", strip=True)) if kicker_div else ""
        value = _strip_ws(value_div.get_text(" ", strip=True)) if value_div else ""

        if kicker and value:
            items.append(CardItem(label=kicker, value=value))

    # dedup + cap to 4
    out: List[CardItem] = []
    seen = set()
    for it in items:
        k = (it.label.lower(), it.value.lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
        if len(out) >= 4:
            break
    return out


def _parse_new_template(soup: BeautifulSoup, lang: str, include_tiles: bool = True) -> Extracted:
    ui = _ui(lang)

    # --- HERO (intro area) ---
    hero_badge = ""
    title = ""
    subtitle = ""
    intro_html = ""

    badge_div = soup.find(
        lambda t: isinstance(t, Tag)
        and t.name == "div"
        and "border-radius: 999px" in (t.get("style") or "").lower()
        and "text-transform: uppercase" in (t.get("style") or "").lower()
    )
    if isinstance(badge_div, Tag):
        hero_badge = _strip_ws(badge_div.get_text(" ", strip=True))

    title_div = soup.find(
        lambda t: isinstance(t, Tag)
        and t.name == "div"
        and "font-size: 28px" in (t.get("style") or "").lower()
        and t.find("strong") is not None
    )
    if isinstance(title_div, Tag):
        title = _strip_ws(title_div.get_text(" ", strip=True))

    subtitle_div = soup.find(
        lambda t: isinstance(t, Tag)
        and t.name == "div"
        and "font-size: 16px" in (t.get("style") or "").lower()
        and "line-height: 1.5" in (t.get("style") or "").lower()
    )
    if isinstance(subtitle_div, Tag):
        subtitle = _strip_ws(subtitle_div.get_text(" ", strip=True))

    body_div = soup.find(
        lambda t: isinstance(t, Tag)
        and t.name == "div"
        and "font-size: 15px" in (t.get("style") or "").lower()
        and "line-height: 1.7" in (t.get("style") or "").lower()
    )
    if isinstance(body_div, Tag):
        intro_html = _safe_decode(body_div)

    hero_img_src = ""
    heroimg = soup.find(id="sid-c5-heroimg")
    if isinstance(heroimg, Tag):
        img = heroimg.find("img")
        if img and img.get("src"):
            hero_img_src = (img.get("src") or "").strip()

    # --- VIDEO ---
    video_src = ""
    has_video = False
    vwrap = soup.find(id="sid-c5-video")
    if isinstance(vwrap, Tag):
        iframe = vwrap.find("iframe")
        if iframe and iframe.get("src"):
            video_src = (iframe.get("src") or "").strip()
            has_video = bool(video_src)

    # --- SPIN ---
    spin_href = ""
    spin_placeholder_img = ""
    has_spin = False

    swrap = soup.find(id="sid-c5-spin")
    _spin_ph_raw = ""

    if isinstance(swrap, Tag):
        a = swrap.find("a")
        if a and a.get("href"):
            spin_href = (a.get("href") or "").strip()
        img = swrap.find("img")
        if img and img.get("src"):
            _spin_ph_raw = (img.get("src") or "").strip()
            spin_placeholder_img = _spin_ph_raw
        has_spin = bool(spin_href)

    # fallback: if hero image missing, use spin placeholder image (raw URL)
    if not (hero_img_src or "").strip() and (_spin_ph_raw or "").strip():
        hero_img_src = _spin_ph_raw

    spin_placeholder_img = _denormalize_spin_placeholder_to_editor(spin_placeholder_img)

        # --- HIGHLIGHTS ---
    highlights: List[CardItem] = []
    # each highlight is an outer span with two inner spans (kicker + value)
    for outer in soup.find_all("span"):
        st = (outer.get("style") or "").lower()
        if "border-radius: 999px" not in st:
            continue
        kids = [k for k in outer.find_all("span", recursive=False)]
        if len(kids) != 2:
            continue
        kicker = _strip_ws(kids[0].get_text(" ", strip=True))
        value = _strip_ws(kids[1].get_text(" ", strip=True))
        if kicker and value:
            highlights.append(CardItem(label=kicker, value=value))

    # ✅ fallback for legacy "Rychlý přehled" cards
    if not highlights:
        highlights = _extract_highlights_legacy_cards(soup)


    # --- PARAMS (features / functions) ---
    features: List[CardItem] = []
    functions: List[CardItem] = []
    pwrap = soup.find(id="sid-c5-features")

    if isinstance(pwrap, Tag):
        cols = pwrap.find_all(lambda t: isinstance(t, Tag) and t.name == "div" and "--width_max: 50%" in (t.get("style") or ""))
        if not cols:
            cols = pwrap.find_all("div", recursive=False)

        for idx, col in enumerate(cols[:2]):
            col_title = ""
            bar = col.find(lambda t: isinstance(t, Tag) and t.name == "div" and "linear-gradient(90deg" in (t.get("style") or ""))
            if isinstance(bar, Tag):
                st = bar.find("strong")
                if st:
                    col_title = _strip_ws(st.get_text(" ", strip=True))

            items: List[CardItem] = []
            for card in col.find_all(lambda t: isinstance(t, Tag) and t.name == "div" and "flex: 1 1 280px" in (t.get("style") or "")):
                kids = [k for k in card.find_all("div", recursive=False)]
                if len(kids) < 2:
                    continue
                lab = _strip_ws(kids[0].get_text(" ", strip=True))
                val = _strip_ws(kids[1].get_text(" ", strip=True))
                if lab and val:
                    items.append(CardItem(label=lab, value=val))

            if col_title.lower() == ui["features_title"].lower():
                features.extend(items)
            elif col_title.lower() == ui["functions_title"].lower():
                functions.extend(items)
            else:
                (features if idx == 0 else functions).extend(items)

    tiles = _extract_tiles_c66_style(soup) if include_tiles else []

    extracted = Extracted(
        language=lang,
        title=title,
        subtitle=subtitle,
        intro_html=intro_html,
        hero_badge=hero_badge,
        hero_img_src=hero_img_src,
        video_src=video_src,
        has_video=has_video,
        spin_href=spin_href,
        spin_placeholder_img=spin_placeholder_img,
        has_spin=has_spin,
        highlights=highlights,
        features=features,
        functions=functions,
        tiles=tiles,
        note_html=_fixed_note_html(lang),
        has_note=True,
    )
    return extracted


# ------------------------------------------------------------
# Public API used by app.py
# ------------------------------------------------------------
def new_extracted(lang: str = "cs") -> Extracted:
    lang = (lang or "cs").lower().strip()
    return Extracted(
        language=lang,
        title="",
        subtitle="",
        intro_html="",
        hero_badge="",
        hero_img_src="",
        video_src="",
        has_video=False,
        spin_href="",
        spin_placeholder_img="",
        has_spin=False,
        highlights=[],
        features=[],
        functions=[],
        tiles=[],
        note_html=_fixed_note_html(lang),
        has_note=True,
    )


def render_new_template(model: Extracted) -> str:
    model.note_html = _fixed_note_html(model.language)
    model.has_note = True

    template_path = Path(__file__).with_name("template_new.j2")
    tpl_src = template_path.read_text(encoding="utf-8")
    tpl = Template(tpl_src)

    ui = _ui(model.language)

    sections = {
        "intro": bool((model.title or "").strip() or (model.subtitle or "").strip() or (model.intro_html or "").strip()),
        "highlights": bool(model.highlights),
        "video": bool(model.has_video and (model.video_src or "").strip()),
        "spin3d": bool(model.has_spin and (model.spin_href or "").strip()),
        "params": True,
        "tiles": bool(model.tiles),
        "note": True,
    }

    hero_links = []
    if sections["video"]:
        hero_links.append({"href": "#sid-c5-video", "label": ui["cta_video"]})
    hero_links.append({"href": "#sid-c5-features", "label": ui["cta_featfunc"]})

    hero = {
        "badge": model.hero_badge or "",
        "title": model.title or "",
        "subtitle": model.subtitle or "",
        "body_html": model.intro_html or "",
        "links": hero_links,
        "img_src": (model.hero_img_src or "").strip(),
        "img_alt": (model.title or ui.get("hero_img_alt") or ui.get("spin_alt") or "Product").strip(),
    }

    video = {"iframe_html": ""}
    if sections["video"]:
        src = (model.video_src or "").strip()
        video["iframe_html"] = (
            f'<iframe title="YouTube video player" loading="lazy" '
            f'referrerpolicy="strict-origin-when-cross-origin" '
            f'src="{src}" frameborder="0" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
            f'allowfullscreen="allowfullscreen" '
            f'style="position: absolute; inset: 0; width: 100%; height: 100%; border: 0;"></iframe>'
        )

    _ph_raw = (model.spin_placeholder_img or model.hero_img_src or "").strip()
    spin3d = {
        "url": (model.spin_href or "").strip(),
        "placeholder_img": _normalize_spin_placeholder(_ph_raw),
        "button_label": ui["spin_button"],
        "note": ui["spin_note"],
        "alt": ui["spin_alt"],
    }

    highlights = [{"kicker": (h.label or "").strip(), "value": (h.value or "").strip()} for h in (model.highlights or [])]

    params = {
        "features_title": ui["features_title"],
        "functions_title": ui["functions_title"],
        "features": [{"label": x.label, "value": x.value} for x in (model.features or [])],
        "functions": [{"label": x.label, "value": x.value} for x in (model.functions or [])],
    }

    tiles = [{"title": t.title, "img_src": t.img_src, "text_html": t.text_html} for t in (model.tiles or [])]

    ctx = asdict(model)
    ctx.update(
        {
            "sections": sections,
            "hero": hero,
            "video": video,
            "spin3d": spin3d,
            "highlights": highlights,
            "params": params,
            "tiles": tiles,
            "note_html": model.note_html,
        }
    )

    html_out = tpl.render(**ctx)
    html_out = re.sub(r"<p>\s*&nbsp;\s*</p>\s*", "", html_out, flags=re.I)
    return html_out


def convert_old_html_to_c5(
    old_html: str,
    include_tiles: bool = True,
    language_override: Optional[str] = None,
) -> Tuple[str, Extracted]:
    soup = BeautifulSoup(old_html or "", "lxml")
    _remove_empty_paragraphs(soup)

    detected = _detect_language(old_html)
    lang = (language_override or detected or "cs").lower().strip()
    if lang not in ("cs", "en", "de"):
        lang = "cs"

    # ✅ NEW FORMAT INPUT (round-trip)
    if _looks_like_new_template(soup):
        extracted = _parse_new_template(soup, lang=lang, include_tiles=include_tiles)
        html_out = render_new_template(extracted)
        return html_out, extracted

    # --- OLD FORMAT INPUT ---
    title, subtitle = _find_title_subtitle(soup)
    hero_img = _first_meaningful_img(soup)

    video_src = _find_youtube_embed(soup)
    has_video = bool(video_src)

    spin_href, spin_placeholder = _find_spin_3d(soup)
    has_spin = bool(spin_href)
    if has_spin and not (spin_placeholder or "").strip():
        spin_placeholder = hero_img

    intro_html = _collect_intro_html(soup)

    # ✅ NEW: if intro starts with same text as subtitle, drop first <p> from intro
    if subtitle and intro_html:
        intro_soup = BeautifulSoup(intro_html, "html.parser")
        first_p = intro_soup.find("p")
        if first_p and _strip_ws(first_p.get_text(" ", strip=True)) == subtitle:
            first_p.decompose()
            intro_html = intro_soup.decode().strip()

    feat_raw, func_raw = _extract_items_features_functions(soup)
    features = [_map_label_value(x, "features", lang) for x in feat_raw][:18]
    functions = [_map_label_value(x, "functions", lang) for x in func_raw][:18]

    highlights = _build_highlights(title, features, functions, lang)

    badge_parts = []
    for it in highlights:
        v = _strip_ws(it.value)
        if v and len(v) <= 28:
            badge_parts.append(v)
        if len(badge_parts) >= 3:
            break
    hero_badge = " • ".join(badge_parts)

    tiles = _extract_tiles_c66_style(soup) if include_tiles else []

    extracted = Extracted(
        language=lang,
        title=title,
        subtitle=subtitle,
        intro_html=intro_html,
        hero_badge=hero_badge,
        hero_img_src=hero_img,
        video_src=video_src,
        has_video=has_video,
        spin_href=spin_href,
        spin_placeholder_img=(spin_placeholder or "").strip(),
        has_spin=has_spin,
        highlights=highlights,
        features=features,
        functions=functions,
        tiles=tiles,
        note_html=_fixed_note_html(lang),
        has_note=True,
    )

    html_out = render_new_template(extracted)
    return html_out, extracted
