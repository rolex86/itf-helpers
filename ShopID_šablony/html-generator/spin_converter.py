from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from bs4 import BeautifulSoup, Tag


SPIN_TEXTS = {
    "cs": {
        "page_title_tpl": "Chainway {model} – 3D prohlídka",
        "subtitle": "3D prohlídka – táhněte myší / prstem pro otočení. Pro návrat na detail produktu toto okno zavřete.",
        "tip": "Tip: na mobilu přepněte na šířku nebo do fullscreenu.",
        "html_lang": "cs-CZ",
    },
    "en": {
        "page_title_tpl": "Chainway {model} – 3D view",
        "subtitle": "3D view – drag with mouse / finger to rotate. To return to product detail, close this window.",
        "tip": "Tip: on mobile, switch to landscape or fullscreen.",
        "html_lang": "en-US",
    },
    "de": {
        "page_title_tpl": "Chainway {model} – 3D-Ansicht",
        "subtitle": "3D-Ansicht – ziehen Sie mit Maus / Finger zum Drehen. Um zur Produktseite zurückzukehren, schließen Sie dieses Fenster.",
        "tip": "Tipp: auf dem Handy auf Querformat oder Vollbild umschalten.",
        "html_lang": "de-DE",
    },
}


@dataclass
class SpinModel:
    language: str  # cs|en|de
    model: str
    config_xml: str
    license_file: str
    graphics_path: str
    placeholder_img: str  # optional; if empty -> no stub is rendered
    alt_text: str


# Support both '...' and "..."
_RE_CONFIG = re.compile(r"['\"]?configFileURL['\"]?\s*[:=]\s*(['\"])(.+?)\1", re.I)
_RE_LICENSE = re.compile(r"['\"]?licenseFileURL['\"]?\s*[:=]\s*(['\"])(.+?)\1", re.I)
_RE_GRAPHICS = re.compile(r"['\"]?graphicsPath['\"]?\s*[:=]\s*(['\"])(.+?)\1", re.I)
_RE_ALT = re.compile(r"['\"]?alt['\"]?\s*[:=]\s*(['\"])(.+?)\1", re.I)

_RE_TITLE_MODEL = re.compile(r"\bchainway\s+([a-z0-9\-]+)\b", re.I)
_RE_MODEL_CODE = re.compile(r"\b([A-Z]{1,3}\d{1,4})\b")


def _norm_lang(lang: Optional[str]) -> str:
    lang = (lang or "cs").lower().strip()
    return lang if lang in ("cs", "en", "de") else "cs"


def _escape_js_single(s: str) -> str:
    """
    Keep output safe for JS single-quoted strings.
    """
    s = s or ""
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _guess_model_from_config(path: str) -> str:
    if not path:
        return ""
    m = re.search(r"/([A-Za-z0-9\-]+)\.xml$", path)
    if m:
        return m.group(1).strip().upper()
    m2 = re.search(r"360_assets/([A-Za-z0-9\-]+)/", path)
    if m2:
        return m2.group(1).strip().upper()
    return ""


def _guess_model_from_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    m = _RE_TITLE_MODEL.search(t)
    if m:
        return m.group(1).strip().upper()
    m2 = _RE_MODEL_CODE.search(t)
    if m2:
        return m2.group(1).strip().upper()
    return ""


def _find_placeholder_img(soup: BeautifulSoup) -> str:
    """
    Prefer stub image inside wr360 container. Fallback: first <img>.
    IMPORTANT: we DO NOT invent IMG_0001.jpg anymore.
    """
    # 1) within the player container (best signal)
    wr = soup.find(id="wr360PlayerId")
    if isinstance(wr, Tag):
        img = wr.find("img")
        if img and img.get("src"):
            return (img.get("src") or "").strip()

    # 2) specifically stub banner if present
    stub = soup.find(class_="stub_banner")
    if isinstance(stub, Tag):
        img = stub.find("img")
        if img and img.get("src"):
            return (img.get("src") or "").strip()

    # 3) fallback first image in document
    img = soup.find("img")
    if img and img.get("src"):
        return (img.get("src") or "").strip()

    return ""


def extract_spin_fields(raw_html: str, language_override: Optional[str] = None) -> SpinModel:
    lang = _norm_lang(language_override)
    soup = BeautifulSoup(raw_html or "", "lxml")

    scripts_text = "\n".join((s.get_text() or "") for s in soup.find_all("script"))

    config_xml = ""
    license_file = ""
    graphics_path = ""
    alt_text = ""

    m = _RE_CONFIG.search(scripts_text)
    if m:
        config_xml = (m.group(2) or "").strip()

    m = _RE_LICENSE.search(scripts_text)
    if m:
        license_file = (m.group(2) or "").strip()

    m = _RE_GRAPHICS.search(scripts_text)
    if m:
        graphics_path = (m.group(2) or "").strip()

    m = _RE_ALT.search(scripts_text)
    if m:
        alt_text = (m.group(2) or "").strip()

    placeholder_img = _find_placeholder_img(soup)

    model = ""
    title = soup.find("title")
    if title:
        model = _guess_model_from_text(title.get_text(" ", strip=True))

    if not model:
        pill = soup.find("span", class_="sid-pill") or soup.find("span")
        if pill:
            model = _guess_model_from_text(pill.get_text(" ", strip=True))

    if not model:
        model = _guess_model_from_text(soup.get_text(" ", strip=True))

    if not model:
        model = _guess_model_from_config(config_xml)

    if not model:
        model = "C5"

    if not config_xml:
        # keep old behavior as a fallback, but still dynamic per model
        config_xml = f"360_assets/{model}/{model}.xml"

    if not license_file:
        license_file = "license.lic"
    if not graphics_path:
        graphics_path = "imagerotator/html/img/basic"

    if not alt_text:
        alt_text = f"360 degree view - {model}"

    return SpinModel(
        language=lang,
        model=model,
        config_xml=config_xml,
        license_file=license_file,
        graphics_path=graphics_path,
        placeholder_img=placeholder_img,  # may be empty on purpose
        alt_text=alt_text,
    )


def render_spin_template(m: SpinModel) -> str:
    lang = _norm_lang(m.language)
    txt = SPIN_TEXTS[lang]

    page_title = txt["page_title_tpl"].format(model=m.model)
    html_lang = txt["html_lang"]

    cfg = _escape_js_single(m.config_xml)
    lic = _escape_js_single(m.license_file or "license.lic")
    gfx = _escape_js_single(m.graphics_path or "imagerotator/html/img/basic")
    alt = _escape_js_single(m.alt_text)

    # Render stub only if placeholder exists
    stub_html = ""
    if (m.placeholder_img or "").strip():
        ph = (m.placeholder_img or "").strip()
        stub_html = f"""
\t\t\t\t\t\t<div class="stub_banner_wrap">
\t\t\t\t\t\t\t<div class="stub_banner" style="aspect-ratio: 1.08;">
\t\t\t\t\t\t\t\t<img src="{ph}" alt="" />
\t\t\t\t\t\t\t</div>
\t\t\t\t\t\t</div>""".rstrip()

    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="{html_lang}">
<head>
\t<title>{page_title}</title>
\t<meta http-equiv="content-type" content="text/html; charset=utf-8"/>

\t<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />

\t<link type="text/css" rel="stylesheet" href="imagerotator/html/css/basic.css"/>
\t<script type="text/javascript" src="imagerotator/html/js/jquery-3.4.1.min.js"></script>
\t<script type="text/javascript" src="imagerotator/html/js/imagerotator.js"></script>

\t<style type="text/css">
\t\thtml, body {{
\t\t\tpadding: 0;
\t\t\tmargin: 0;
\t\t\theight: 100%;
\t\t\tbackground: #0f1620;
\t\t\tcolor: #ffffff;
\t\t\tfont-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, "Noto Sans", "Liberation Sans", sans-serif;
\t\t}}

\t\t.sid-bg {{
\t\t\tmin-height: 100%;
\t\t\tbackground: linear-gradient(135deg,#0f1620 0%,#141b23 45%,#1f2a3a 100%);
\t\t\tpadding: 14px;
\t\t\tbox-sizing: border-box;
\t\t}}

\t\t.sid-wrap {{
\t\t\tmax-width: 980px;
\t\t\tmargin: 0 auto;
\t\t}}

\t\t.sid-topbar {{
\t\t\tdisplay: flex;
\t\t\talign-items: center;
\t\t\tjustify-content: flex-start;
\t\t\tgap: 10px;
\t\t\tpadding: 10px 10px 12px 10px;
\t\t}}

\t\t.sid-pill {{
\t\t\tdisplay: inline-block;
\t\t\tfont-size: 12px;
\t\t\tletter-spacing: .12em;
\t\t\ttext-transform: uppercase;
\t\t\tcolor: rgba(255,255,255,.78);
\t\t\tbackground: rgba(255,255,255,.08);
\t\t\tborder: 1px solid rgba(255,255,255,.10);
\t\t\tpadding: 6px 10px;
\t\t\tborder-radius: 999px;
\t\t\twhite-space: nowrap;
\t\t}}

\t\t.sid-sub {{
\t\t\tfont-size: 12px;
\t\t\tcolor: rgba(255,255,255,.75);
\t\t}}

\t\t.sid-card {{
\t\t\tbackground: rgba(255,255,255,.06);
\t\t\tborder: 1px solid rgba(255,255,255,.12);
\t\t\tborder-radius: 18px;
\t\t\toverflow: hidden;
\t\t\tbox-shadow: 0 12px 30px rgba(0,0,0,.25);
\t\t}}

\t\t#content {{
\t\t\twidth: 100%;
\t\t\theight: calc(100vh - 140px);
\t\t\tmin-height: 520px;
\t\t\tbackground: rgba(255,255,255,.03);
\t\t}}

\t\t.wr360_player {{
\t\t\tbackground-color: #ffffff !important;
\t\t}}

\t\t.sid-foot {{
\t\t\tpadding: 12px 14px 14px 14px;
\t\t\tbackground: rgba(255,255,255,.04);
\t\t\tborder-top: 1px solid rgba(255,255,255,.10);
\t\t\tcolor: rgba(255,255,255,.70);
\t\t\tfont-size: 12px;
\t\t\tline-height: 1.6;
\t\t}}

\t\t@media (max-width: 600px) {{
\t\t\t.sid-topbar {{
\t\t\t\tpadding: 8px 6px 10px 6px;
\t\t\t}}
\t\t\t#content {{
\t\t\t\theight: calc(100vh - 160px);
\t\t\t\tmin-height: 460px;
\t\t\t}}
\t\t}}
\t</style>

\t<script language="javascript" type="text/javascript">
\t\tjQuery(document).ready(function(){{
\t\t\tjQuery('#wr360PlayerId').rotator({{
\t\t\t\tlicenseFileURL: '{lic}',
\t\t\t\tconfigFileURL: '{cfg}',
\t\t\t\tgraphicsPath: '{gfx}',
\t\t\t\talt: '{alt}',
\t\t\t\tresponsiveBaseWidth: 800,
\t\t\t\tresponsiveMinHeight: 0,
\t\t\t\tgoogleEventTracking: false
\t\t\t}});
\t\t}});
\t</script>
</head>

<body>
\t<div class="sid-bg">
\t\t<div class="sid-wrap">

\t\t\t<!-- TOP BAR -->
\t\t\t<div class="sid-topbar">
\t\t\t\t<span class="sid-pill">CHAINWAY {m.model}</span>
\t\t\t\t<div class="sid-sub">{txt["subtitle"]}</div>
\t\t\t</div>

\t\t\t<!-- MAIN CARD -->
\t\t\t<div class="sid-card">

\t\t\t\t<div id="content">
\t\t\t\t\t<div id="wr360PlayerId" class="wr360_player">{stub_html}
\t\t\t\t\t</div>
\t\t\t\t</div>

\t\t\t\t<div class="sid-foot">
\t\t\t\t\t{txt["tip"]}
\t\t\t\t</div>

\t\t\t</div>
\t\t</div>
\t</div>
</body>
</html>"""


def convert(raw_html: str, language_override: Optional[str] = None) -> Tuple[SpinModel, str]:
    model = extract_spin_fields(raw_html, language_override=language_override)
    html_out = render_spin_template(model)
    return model, html_out
