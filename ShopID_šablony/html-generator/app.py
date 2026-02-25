import json
import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

import techparams_converter
import spin_converter

from converter import (
    convert_old_html_to_c5,
    new_extracted,
    render_new_template,
    CardItem,
    Tile,
)

st.set_page_config(page_title="Generátor HTML", layout="wide")

# ---------- Modern UI CSS ----------
st.markdown(
    r"""
<style>
/* page width + spacing */
.block-container { padding-top: 1.25rem; padding-bottom: 2.5rem; }

/* ---------------------------------------------
   PRIMARY buttons = "generovací" (zelené)
---------------------------------------------- */
button[kind="primary"]{
  border-radius: 999px !important;
  padding: 0.70rem 1.15rem !important;

  border: 1px solid rgba(90, 240, 160, .45) !important;
  background: linear-gradient(135deg, rgba(30, 190, 105, .28) 0%, rgba(18, 140, 78, .32) 100%) !important;

  color: #ffffff !important;
  font-weight: 700 !important;

  transition: transform .08s ease, filter .2s ease, box-shadow .2s ease, border-color .2s ease;
  box-shadow: 0 10px 26px rgba(0,0,0,.16);
}
button[kind="primary"]:hover{
  filter: brightness(1.10);
  transform: translateY(-1px);
  border-color: rgba(120, 255, 185, .75) !important;
  box-shadow: 0 14px 34px rgba(0,0,0,.24);
}
button[kind="primary"]:active{ transform: translateY(0px) scale(0.99); }

/* ---------------------------------------------
   OTHER buttons (např. download) – dark
---------------------------------------------- */
.stDownloadButton>button{
  border-radius: 999px !important;
  padding: 0.65rem 1.1rem !important;
  border: 1px solid rgba(255,255,255,.12) !important;
  background: linear-gradient(135deg,#141b23 0%,#1f2a3a 100%) !important;
  color: #fff !important;
  transition: transform .08s ease, filter .2s ease, box-shadow .2s ease;
  box-shadow: 0 6px 18px rgba(0,0,0,.12);
}
.stDownloadButton>button:hover{
  filter: brightness(1.08);
  transform: translateY(-1px);
  box-shadow: 0 10px 26px rgba(0,0,0,.18);
}

/* Expander summary look */
details summary {
  border-radius: 14px !important;
  padding: .75rem .9rem !important;
  border: 1px solid rgba(20,27,35,.10) !important;
  background: rgba(20,27,35,.03) !important;
}
details[open] summary { background: rgba(20,27,35,.06) !important; }

/* Small pill header */
.sid-pill {
  display:inline-block;
  padding:6px 10px;
  border-radius:999px;
  background: rgba(20,27,35,.06);
  border:1px solid rgba(20,27,35,.14);
  font-size:12px;
  letter-spacing:.08em;
  text-transform:uppercase;
  margin: 0 0 6px 0;
}

/* ---------- Radio pills (global) ---------- */
div[data-testid="stRadio"] div[role="radiogroup"] label{
  border-radius: 999px !important;
  padding: .42rem .95rem !important;
  border: 1px solid rgba(20,27,35,.18) !important;
  background: rgba(20,27,35,.04) !important;
  margin-right: .35rem !important;
  transition: transform .08s ease, filter .2s ease, box-shadow .2s ease, background .2s ease;
}
div[data-testid="stRadio"] div[role="radiogroup"] label:hover{
  background: rgba(20,27,35,.08) !important;
  transform: translateY(-1px);
}
div[data-testid="stRadio"] div[role="radiogroup"] label[aria-checked="true"],
div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked){
  background: linear-gradient(135deg, rgba(20,27,35,.18) 0%, rgba(31,42,58,.18) 100%) !important;
  border: 1px solid rgba(20,27,35,.35) !important;
  box-shadow: 0 8px 20px rgba(0,0,0,.12);
  font-weight: 700;
}

/* =========================================================
   Language picker BOX – stylujeme přes třídu "sid-lang-box",
   kterou doplní JS (protože streamlit wrapper classy se mění).
========================================================= */
.sid-lang-box{
  border: 2px solid rgba(255, 75, 75, .65) !important;
  background: rgba(255, 75, 75, .15) 50% !important;
  border-radius: 16px !important;

  box-shadow:
    0 10px 26px rgba(0,0,0,.16),
    0 0 0 4px rgba(255, 170, 70, .10) !important;
}
.sid-lang-box > div{ border-radius: 14px !important; }
.sid-lang-box .sid-pill{ color: rgba(255,255,255,.95) !important; font-weight: 800 !important; }

/* radio pills jen uvnitř boxu */
.sid-lang-box div[data-testid="stRadio"] div[role="radiogroup"] label{
  border: 1px solid rgba(255, 75, 75, .30) !important;
  background: rgba(255, 75, 75, .06) !important;
}
.sid-lang-box div[data-testid="stRadio"] div[role="radiogroup"] label:hover{
  background: rgba(255, 170, 70, .10) !important;
}
.sid-lang-box div[data-testid="stRadio"] div[role="radiogroup"] label[aria-checked="true"],
.sid-lang-box div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked){
  background: linear-gradient(135deg, rgba(255, 170, 70, .22) 0%, rgba(255, 170, 70, .12) 100%) !important;
  border: 1px solid rgba(255, 170, 70, .65) !important;
  box-shadow:
    0 10px 22px rgba(0,0,0,.12),
    0 0 0 3px rgba(255, 170, 70, .12) !important;
  font-weight: 800 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

st.title("Generátor HTML")
st.caption("Převod ze starého HTML + editor")

_SUPPORTED_LANGS = ("cs", "en", "de")
_APPDATA_DIRNAME = "ShopID_HTML_Generator"
_NOTE_OVERRIDES_FILENAME = "note_overrides.json"


def _norm_lang(lang: str) -> str:
    val = (lang or "cs").lower().strip()
    return val if val in _SUPPORTED_LANGS else "cs"


def _note_overrides_path() -> Path:
    fallback = Path(__file__).with_name(_NOTE_OVERRIDES_FILENAME)
    try:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        app_dir = base / _APPDATA_DIRNAME
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir / _NOTE_OVERRIDES_FILENAME
    except Exception:
        return fallback


def _load_note_overrides() -> dict:
    path = _note_overrides_path()
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    out = {}
    for k, v in raw.items():
        key = (k or "").lower().strip()
        if key not in _SUPPORTED_LANGS:
            continue
        val = (v or "").strip()
        if val:
            out[key] = val
    return out


def _save_note_overrides(overrides: dict) -> bool:
    safe = {}
    for lang in _SUPPORTED_LANGS:
        val = (overrides or {}).get(lang, "")
        val = val if isinstance(val, str) else str(val or "")
        if val.strip():
            safe[lang] = val

    try:
        _note_overrides_path().write_text(
            json.dumps(safe, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _base_note_html(lang: str) -> str:
    return new_extracted(_norm_lang(lang)).note_html


def _resolved_note_html(lang: str) -> str:
    l = _norm_lang(lang)
    overrides = st.session_state.get("note_overrides", {})
    custom = ""
    if isinstance(overrides, dict):
        custom = (overrides.get(l) or "").strip()
    return custom or _base_note_html(l)


def _apply_note_profile(model, preserve_custom: bool = True) -> None:
    lang = _norm_lang(getattr(model, "language", "cs"))
    current = (getattr(model, "note_html", "") or "").strip()
    default_note = _base_note_html(lang).strip()
    target_note = _resolved_note_html(lang)

    if preserve_custom and current and current != default_note:
        model.has_note = True
        return

    model.note_html = target_note
    model.has_note = True

# ---------- Session state ----------
if "note_overrides" not in st.session_state:
    st.session_state.note_overrides = _load_note_overrides()

if "model" not in st.session_state:
    _m = new_extracted("cs")
    _apply_note_profile(_m, preserve_custom=False)
    st.session_state.model = _m
else:
    _apply_note_profile(st.session_state.model, preserve_custom=True)

if "out_html_desc" not in st.session_state:
    st.session_state.out_html_desc = ""
if "out_html_params" not in st.session_state:
    st.session_state.out_html_params = ""
if "out_html_spin" not in st.session_state:
    st.session_state.out_html_spin = ""

if "last_output_kind" not in st.session_state:
    st.session_state.last_output_kind = "desc"  # desc|params|spin

# Persisted output radio selection (user comfort)
if "radio_output_choice" not in st.session_state:
    st.session_state.radio_output_choice = "Popis produktu"

if "tp_input_html" not in st.session_state:
    st.session_state.tp_input_html = ""
if "tp_sections_info" not in st.session_state:
    st.session_state.tp_sections_info = []

if "spin_input_html" not in st.session_state:
    st.session_state.spin_input_html = ""
if "spin_info" not in st.session_state:
    st.session_state.spin_info = None

# nonce pro editor widgety (aby se po převodu znovu inicializovaly z modelu)
if "editor_nonce" not in st.session_state:
    st.session_state.editor_nonce = 0


def _bump_editor_nonce() -> None:
    st.session_state.editor_nonce = int(st.session_state.get("editor_nonce", 0)) + 1
    # FIX: vyčistit "store" DF cache (ne widget keys), aby se editor inicializoval z modelu
    for k in list(st.session_state.keys()):
        if (
            k.startswith("store_df_hl_")
            or k.startswith("store_df_tiles_")
            or k.startswith("store_df_feat_")
            or k.startswith("store_df_func_")
        ):
            del st.session_state[k]


# ----------------------------
# Helpers for editors (FIX: empty tables)
# ----------------------------
def _items_to_rows(items):
    rows = [{"label": x.label, "value": x.value} for x in (items or [])]
    # IMPORTANT: allow adding rows even when starting empty
    if not rows:
        rows = [{"label": "", "value": ""}]
    return rows


def _rows_to_items(rows, default_label: str = "Funkce"):
    out = []
    for r in rows or []:
        label = (r.get("label") or "").strip()
        value = (r.get("value") or "").strip()
        if not (label or value):
            continue
        out.append(CardItem(label=label or default_label, value=value))
    return out


def _tiles_to_rows(tiles):
    rows = [{"title": t.title, "img_src": t.img_src, "text_html": t.text_html} for t in (tiles or [])]
    # allow adding tiles even when starting empty
    if not rows:
        rows = [{"title": "", "img_src": "", "text_html": ""}]
    return rows


def _rows_to_tiles(rows):
    out = []
    for r in rows or []:
        title = (r.get("title") or "").strip()
        img_src = (r.get("img_src") or "").strip()
        text_html = (r.get("text_html") or "").strip()
        if not (title or img_src or text_html):
            continue
        out.append(Tile(title=title, img_src=img_src, text_html=text_html))
    return out


def _show_error_compact(where, title: str, err: Exception) -> None:
    where.error(title)
    with where.expander("Detaily chyby", expanded=False):
        st.code(str(err), language="text")


_LANG_KEYS = list(_SUPPORTED_LANGS)
_LANG_LABELS = {"cs": "CZ", "en": "EN", "de": "DE"}


def _inject_lang_box_js(marker_id: str) -> None:
    js = f"""
    <script>
    (function() {{
      const markerId = "{marker_id}";
      let tries = 0;

      function findWrapper(el) {{
        if (!el) return null;

        let w = el.closest('div[data-testid="stVerticalBlockBorderWrapper"]');
        if (w) return w;

        w = el.closest('div[data-testid="stVerticalBlock"]');
        if (w) return w;

        w = el.closest('div[class^="st-emotion-cache-"]');
        return w;
      }}

      function tick() {{
        tries++;
        const el = window.parent.document.getElementById(markerId) || document.getElementById(markerId);
        if (!el) {{
          if (tries < 20) setTimeout(tick, 150);
          return;
        }}

        const w = findWrapper(el);
        if (!w) {{
          if (tries < 20) setTimeout(tick, 150);
          return;
        }}

        w.classList.add("sid-lang-box");
      }}

      setTimeout(tick, 0);
    }})();
    </script>
    """
    components.html(js, height=0, width=0)


def _lang_picker(label: str, key: str, default: str = "cs") -> str:
    default = (default or "cs").lower().strip()
    if default not in _LANG_KEYS:
        default = "cs"

    marker_id = f"sid-lang-marker-{key}"

    try:
        ctx = st.container(border=True)
    except TypeError:
        ctx = st.container()

    with ctx:
        st.markdown(f'<span id="{marker_id}"></span>', unsafe_allow_html=True)
        st.markdown('<div class="sid-pill">Důležité: jazyk výstupu</div>', unsafe_allow_html=True)

        chosen = st.radio(
            label,
            options=[_LANG_LABELS[k] for k in _LANG_KEYS],
            horizontal=True,
            index=_LANG_KEYS.index(default),
            label_visibility="collapsed",
            key=f"radio_{key}",
        )

    _inject_lang_box_js(marker_id)

    for k in _LANG_KEYS:
        if chosen == _LANG_LABELS[k]:
            return k
    return "cs"


def _set_last_output(kind: str) -> None:
    """
    kind: 'desc' | 'params' | 'spin'
    nastaví i "radio_output_choice", aby byl ve Výstupu předvybraný poslední typ.
    """
    st.session_state.last_output_kind = kind
    if kind == "desc":
        st.session_state.radio_output_choice = "Popis produktu"
    elif kind == "params":
        st.session_state.radio_output_choice = "Parametry (tabulka)"
    else:
        st.session_state.radio_output_choice = "Spin (soubor)"


tab_convert, tab_params, tab_spin, tab_new, tab_editor, tab_output = st.tabs(
    [
        "Popis",
        "Parametry (tabulka)",
        "Spin (soubor)",
        "Nový produkt (ručně)",
        "Editor obsahu",
        "Výstup HTML",
    ]
)

# =========================
# TAB: Convert
# =========================
with tab_convert:
    banner = st.empty()

    left, right = st.columns([1.35, 0.65], gap="large")

    with left:
        st.subheader("Vstup (starý HTML)")
        old_html = st.text_area(
            "Starý HTML",
            value="",
            height=460,
            placeholder="Sem vlož starý HTML popis produktu…",
            label_visibility="collapsed",
            key="ta_old_html",
        )

    with right:
        st.subheader("Základní volby")

        lang = _lang_picker("Jazyk", key="lang_convert", default="cs")

        include_tiles = st.radio(
            "Dlaždice (H2 + obrázek + text)",
            options=["Auto", "Zapnout", "Vypnout"],
            horizontal=True,
            index=0,
            key="tiles_convert",
        )

        run = st.button("Převést do Nové šablony", type="primary", width="stretch", key="btn_convert_run")

        st.divider()
        st.subheader("Co se stane")
        st.write(
            "- Z HTML se vytáhne titulek, intro, média (YT/3D), Vlastnosti/Funkce a highlighty\n"
            "- V editoru můžeš vše upravit a přegenerovat\n"
            "- Poznámka se doplní vždy; v editoru ji můžeš upravit a uložit jako výchozí"
        )

    if run:
        raw = (old_html or "").strip()
        if not raw:
            banner.warning("Vlož prosím vstupní HTML.")
        else:
            tiles_mode = {"Auto": None, "Zapnout": True, "Vypnout": False}.get(include_tiles, None)
            try:
                _, extracted = convert_old_html_to_c5(
                    old_html=raw,
                    include_tiles=tiles_mode,
                    language_override=lang,
                )
                extracted.language = lang
                _apply_note_profile(extracted, preserve_custom=True)
                st.session_state.model = extracted

                # vynutí znovu-inicializaci editor widgetů z modelu
                _bump_editor_nonce()

                st.session_state.out_html_desc = render_new_template(st.session_state.model)
                _set_last_output("desc")

                banner.success("Hotovo. Otevři záložku „Editor obsahu“ a případně uprav detaily.")
                st.toast("Vygenerováno ✅")
            except Exception as e:
                _show_error_compact(banner, "Nepodařilo se převést HTML.", e)
                st.toast("Chyba při převodu", icon="⚠️")

# =========================
# TAB: Params
# =========================
with tab_params:
    banner_tp = st.empty()
    st.subheader("Parametry – převod tabulky do sid-techparams")

    left, right = st.columns([1.35, 0.65], gap="large")

    with left:
        st.subheader("Vstup (HTML tabulka)")
        st.session_state.tp_input_html = st.text_area(
            "Vstupní HTML tabulky",
            value=st.session_state.tp_input_html,
            height=460,
            placeholder="Sem vlož starou tabulku (<table class='tabulka'>...) nebo i nový sid-techparams…",
            label_visibility="collapsed",
            key="ta_tp_input",
        )

    with right:
        st.subheader("Akce")

        run_tp = st.button("Převést tabulku", type="primary", width="stretch", key="btn_tp_run")
        clear_tp = st.button("Vymazat", width="stretch", key="btn_tp_clear")

        st.divider()
        st.subheader("Co se stane")
        st.write(
            "- Ze staré tabulky se rozpoznají sekce (řádky s <strong>)\n"
            "- Obyčejné řádky se převedou na nový layout sid-techparams\n"
            "- Výstup se propíše do záložky „Výstup HTML“"
        )

    if clear_tp:
        st.session_state.tp_input_html = ""
        st.session_state.out_html_params = ""
        st.session_state.tp_sections_info = []
        st.rerun()

    if run_tp:
        raw = (st.session_state.tp_input_html or "").strip()
        if not raw:
            banner_tp.warning("Vlož prosím vstupní HTML tabulky.")
        else:
            try:
                _, sections, out = techparams_converter.convert(raw)
                st.session_state.out_html_params = out
                st.session_state.tp_sections_info = [(s.title, len(s.rows)) for s in sections]
                _set_last_output("params")

                banner_tp.success("Hotovo. Výstup najdeš v záložce „Výstup HTML“.")
                st.toast("Parametry převedeny ✅")
            except Exception as e:
                st.session_state.out_html_params = ""
                st.session_state.tp_sections_info = []
                _show_error_compact(banner_tp, "Nepodařilo se převést tabulku parametrů.", e)
                st.toast("Chyba při převodu", icon="⚠️")

    if st.session_state.tp_sections_info:
        st.markdown("**Detekované sekce:**")
        for title, cnt in st.session_state.tp_sections_info:
            st.write(f"- **{title}**: {cnt} řádků")

# =========================
# TAB: Spin
# =========================
with tab_spin:
    banner_spin = st.empty()
    st.subheader("Spin (soubor) – formátování dle šablony")

    left, right = st.columns([1.35, 0.65], gap="large")

    with left:
        st.subheader("Vstup (HTML spin stránky)")
        st.session_state.spin_input_html = st.text_area(
            "Vstupní HTML (spin)",
            value=st.session_state.spin_input_html,
            height=460,
            placeholder="Sem vlož HTML spin stránky (celý soubor)…",
            label_visibility="collapsed",
            key="ta_spin_input",
        )

    with right:
        st.subheader("Akce")

        run_spin = st.button(
            "Převést do Spin šablony",
            type="primary",
            width="stretch",
            key="btn_spin_run",
        )
        clear_spin = st.button(
            "Vymazat",
            width="stretch",
            key="btn_spin_clear",
        )

        st.divider()
        st.subheader("Co se stane")
        st.write(
            "- Z HTML se vytáhne konfigurace rotátoru (hlavně `configFileURL`, případně i `licenseFileURL`, `graphicsPath`)\n"
            "- Najde se název/model a použije se do `<title>` a horního „pill“ labelu\n"
            "- Vezme se placeholder obrázek (první fotka ve `stub_banner`), aby se zobrazil ještě před načtením vieweru\n"
            "- Vygeneruje se nový HTML soubor ve stylu tvé šablony (gradient pozadí, topbar, karta, footer)\n"
            "- Výstup se propíše do záložky „Výstup HTML“"
        )

    if clear_spin:
        st.session_state.spin_input_html = ""
        st.session_state.out_html_spin = ""
        st.session_state.spin_info = None
        st.rerun()

    if run_spin:
        raw = (st.session_state.spin_input_html or "").strip()
        if not raw:
            banner_spin.warning("Vlož prosím vstupní HTML spin stránky.")
        else:
            try:
                info, out = spin_converter.convert(raw)
                st.session_state.out_html_spin = out
                st.session_state.spin_info = info
                _set_last_output("spin")

                banner_spin.success("Hotovo. Výstup najdeš v záložce „Výstup HTML“.")
                st.toast("Spin vygenerován ✅")
            except Exception as e:
                st.session_state.out_html_spin = ""
                st.session_state.spin_info = None
                _show_error_compact(banner_spin, "Nepodařilo se převést spin HTML.", e)
                st.toast("Chyba při převodu", icon="⚠️")

    if st.session_state.spin_info:
        info = st.session_state.spin_info
        st.markdown("**Detekované hodnoty:**")
        st.write(f"- Model: **{info.model}**")
        st.write(f"- configFileURL: `{info.config_xml}`")
        if getattr(info, "license_file", None):
            st.write(f"- licenseFileURL: `{info.license_file}`")
        if getattr(info, "graphics_path", None):
            st.write(f"- graphicsPath: `{info.graphics_path}`")
        st.write(f"- placeholder img: `{info.placeholder_img}`")

# =========================
# TAB: New product
# =========================
with tab_new:
    banner_new = st.empty()

    st.subheader("Nový produkt bez původní šablony")
    c1, c2 = st.columns([1, 2], gap="large")

    with c1:
        lang_new = _lang_picker("Jazyk", key="lang_new", default="cs")
        create_empty = st.button("Vytvořit prázdný návrh", type="primary", width="stretch", key="btn_new_empty")

        if create_empty:
            try:
                st.session_state.model = new_extracted(lang_new)
                _apply_note_profile(st.session_state.model, preserve_custom=False)

                # vynutí znovu-inicializaci editor widgetů z modelu
                _bump_editor_nonce()

                st.session_state.out_html_desc = render_new_template(st.session_state.model)
                _set_last_output("desc")

                banner_new.success("Prázdný návrh připraven. Otevři „Editor obsahu“ a vyplň detaily.")
                st.toast("Návrh vytvořen ✅")
            except Exception as e:
                _show_error_compact(banner_new, "Nepodařilo se vytvořit prázdný návrh.", e)
                st.toast("Chyba", icon="⚠️")

    with c2:
        st.info(
            "Tip: pro nové produkty vyplníš title/subtitle/intro, média, a doplníš položky Vlastnosti/Funkce. "
            "Highlight si můžeš napsat ručně nebo nechat prázdný."
        )

# =========================
# TAB: Editor
# =========================
with tab_editor:
    banner_edit = st.empty()
    m = st.session_state.model

    note_lang = _norm_lang(getattr(m, "language", "cs"))
    if st.button(
        f"Obnovit uloženou výchozí poznámku pro jazyk {note_lang.upper()}",
        key=f"btn_note_reset_default_{note_lang}",
        width="stretch",
    ):
        overrides = dict(st.session_state.get("note_overrides", {}))
        had_override = note_lang in overrides
        reset_persisted = True

        if had_override:
            new_overrides = dict(overrides)
            new_overrides.pop(note_lang, None)
            if _save_note_overrides(new_overrides):
                st.session_state.note_overrides = new_overrides
            else:
                reset_persisted = False

        m.note_html = _base_note_html(note_lang)
        m.has_note = True
        st.session_state.model = m
        st.session_state.out_html_desc = render_new_template(st.session_state.model)
        _set_last_output("desc")
        _bump_editor_nonce()

        if had_override and reset_persisted:
            st.toast(f"Výchozí poznámka pro {note_lang.upper()} obnovena na systémový text ✅")
        elif had_override and not reset_persisted:
            banner_edit.warning(
                "Poznámka v modelu byla obnovena, ale reset výchozí hodnoty se nepodařilo uložit na disk."
            )
        else:
            st.toast(f"Pro {note_lang.upper()} nebyla uložená vlastní výchozí poznámka.")

    n = st.session_state.editor_nonce

    # Tyto přepínače jsou mimo form, aby se média překreslila hned po kliknutí.
    media_toggles = st.columns([1, 1], gap="large")
    with media_toggles[0]:
        st.toggle("Zobrazit video", value=bool(m.has_video), key=f"tg_has_video_{n}")
    with media_toggles[1]:
        st.toggle("Zobrazit 3D / spin", value=bool(m.has_spin), key=f"tg_has_spin_{n}")

    has_video = bool(st.session_state.get(f"tg_has_video_{n}", bool(m.has_video)))
    has_spin = bool(st.session_state.get(f"tg_has_spin_{n}", bool(m.has_spin)))

    # FORM = žádné reruny při přechodu mezi buňkami v data_editoru
    with st.form(key=f"form_editor_{n}", clear_on_submit=False):
        lang_editor = _lang_picker("Jazyk", key=f"lang_editor_{n}", default=(m.language or "cs"))

        st.divider()

        colA, colB = st.columns([1, 1], gap="large")

        with colA:
            with st.expander("Základní texty", expanded=True):
                title = st.text_input("Název produktu", value=m.title or "", key=f"in_title_{n}")
                subtitle = st.text_input("Podtitulek", value=m.subtitle or "", key=f"in_subtitle_{n}")
                hero_badge = st.text_input("Badge (krátký řádek nahoře)", value=m.hero_badge or "", key=f"in_badge_{n}")
                intro_html = st.text_area("Intro (HTML povoleno)", value=m.intro_html or "", height=220, key=f"ta_intro_{n}")

            with st.expander("Média (obrázek / video / 3D)", expanded=True):
                hero_img_src = st.text_input("Hero obrázek (src)", value=m.hero_img_src or "", key=f"in_hero_img_{n}")

                video_src = m.video_src or ""
                if has_video:
                    video_src = st.text_input("YouTube embed URL", value=video_src or "", key=f"in_video_{n}")

                spin_href = m.spin_href or ""
                spin_placeholder_img = m.spin_placeholder_img or ""
                if has_spin:
                    spin_href = st.text_input(
                        "3D / Spin URL (href nebo iframe src)",
                        value=spin_href or "",
                        key=f"in_spin_href_{n}",
                    )
                    spin_placeholder_img = st.text_input(
                        "Placeholder obrázek pro 3D (jen název souboru nebo celé URL)",
                        value=spin_placeholder_img or "",
                        placeholder="např. MC50_3D_ph.jpg",
                        help=(
                            'Stačí zadat název souboru. Výstup automaticky doplní cestu '
                            '"https://cdn.shopid.cz/data/user-content/360_produkty/". '
                            'Soubor musíš nahrát na FTP do složky "360_produkty".'
                        ),
                        key=f"in_spin_ph_{n}",
                    )

        with colB:
            with st.expander("Highlight karty (max 4)", expanded=True):
                hl_rows = st.data_editor(
                    pd.DataFrame(_items_to_rows(m.highlights)),
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "label": st.column_config.TextColumn("Label", width="small"),
                        "value": st.column_config.TextColumn("Value", width="large"),
                    },
                    key=f"de_hl_{n}",
                )

            with st.expander("Dlaždice (sekce H2 + obrázek + text)", expanded=True):
                tiles_rows = st.data_editor(
                    pd.DataFrame(_tiles_to_rows(m.tiles)),
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "title": st.column_config.TextColumn("Nadpis", width="medium"),
                        "img_src": st.column_config.TextColumn("Obrázek (src)", width="large"),
                        "text_html": st.column_config.TextColumn("Text (HTML)", width="large"),
                    },
                    key=f"de_tiles_{n}",
                )

        st.divider()

        cF, cG = st.columns([1, 1], gap="large")
        with cF:
            with st.expander("Vlastnosti", expanded=True):
                feat_rows = st.data_editor(
                    pd.DataFrame(_items_to_rows(m.features)),
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "label": st.column_config.TextColumn("Label", width="small"),
                        "value": st.column_config.TextColumn("Value", width="large"),
                    },
                    key=f"de_feat_{n}",
                )

        with cG:
            with st.expander("Funkce", expanded=True):
                func_rows = st.data_editor(
                    pd.DataFrame(_items_to_rows(m.functions)),
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "label": st.column_config.TextColumn("Label", width="small"),
                        "value": st.column_config.TextColumn("Value", width="large"),
                    },
                    key=f"de_func_{n}",
                )

        st.divider()

        with st.expander("Poznámka (vždy se doplní do výstupu)", expanded=False):
            note_html = st.text_area(
                "Poznámka (HTML)",
                value=m.note_html or "",
                height=200,
                key=f"ta_note_{n}",
            )
            save_note_default = st.checkbox(
                "Uložit tuto poznámku jako výchozí pro vybraný jazyk",
                value=False,
                key=f"cb_note_default_{n}",
            )

        submitted = st.form_submit_button(
            "Uložit změny a aktualizovat výstup HTML",
            type="primary",
            width="stretch",
        )

    # ---- po submitu (tady už jsou hodnoty stabilně commited) ----
    if submitted:
        try:
            m.language = lang_editor
            m.has_video = bool(has_video)
            m.has_spin = bool(has_spin)

            m.title = title
            m.subtitle = subtitle
            m.hero_badge = hero_badge
            m.intro_html = intro_html

            m.hero_img_src = hero_img_src
            m.video_src = video_src if has_video else (m.video_src or "")
            m.spin_href = spin_href if has_spin else (m.spin_href or "")
            m.spin_placeholder_img = spin_placeholder_img if has_spin else (m.spin_placeholder_img or "")

            # DF -> list[dict]
            hl_list = hl_rows.to_dict("records") if isinstance(hl_rows, pd.DataFrame) else list(hl_rows or [])
            tiles_list = tiles_rows.to_dict("records") if isinstance(tiles_rows, pd.DataFrame) else list(tiles_rows or [])
            feat_list = feat_rows.to_dict("records") if isinstance(feat_rows, pd.DataFrame) else list(feat_rows or [])
            func_list = func_rows.to_dict("records") if isinstance(func_rows, pd.DataFrame) else list(func_rows or [])

            m.highlights = _rows_to_items(hl_list, default_label="Funkce")[:4]
            m.tiles = _rows_to_tiles(tiles_list)
            m.features = _rows_to_items(feat_list, default_label="Vlastnost")
            m.functions = _rows_to_items(func_list, default_label="Funkce")
            m.note_html = (note_html or "").strip() or _resolved_note_html(m.language)
            m.has_note = True

            if save_note_default:
                overrides = dict(st.session_state.get("note_overrides", {}))
                overrides[m.language] = m.note_html
                if _save_note_overrides(overrides):
                    st.session_state.note_overrides = overrides
                    st.toast(f"Výchozí poznámka pro {m.language.upper()} uložena ✅")
                else:
                    banner_edit.warning(
                        "Poznámka byla uložena v modelu, ale nepodařilo se uložit výchozí hodnotu na disk."
                    )

            st.session_state.model = m
            st.session_state.out_html_desc = render_new_template(st.session_state.model)
            _set_last_output("desc")

            banner_edit.success("Uloženo. Výstup aktualizován – otevři „Výstup HTML“.")
            st.toast("Aktualizováno ✅")
        except Exception as e:
            _show_error_compact(banner_edit, "Nepodařilo se uložit změny / vygenerovat výstup.", e)
            st.toast("Chyba při renderu", icon="⚠️")

# =========================
# TAB: Output
# =========================
with tab_output:
    st.subheader("Výstup HTML")

    has_desc = bool((st.session_state.out_html_desc or "").strip())
    has_params = bool((st.session_state.out_html_params or "").strip())
    has_spin = bool((st.session_state.out_html_spin or "").strip())

    if not has_desc and not has_params and not has_spin:
        st.info(
            "Nejdřív proveď převod / vytvoř produkt nebo převeď tabulku parametrů / spin soubor. "
            "Pak se výstup zobrazí tady."
        )
    else:
        options = []
        if has_desc:
            options.append("Popis produktu")
        if has_params:
            options.append("Parametry (tabulka)")
        if has_spin:
            options.append("Spin (soubor)")

        kind_map = {"desc": "Popis produktu", "params": "Parametry (tabulka)", "spin": "Spin (soubor)"}
        default = kind_map.get(st.session_state.get("last_output_kind", "desc"), options[0])
        if default not in options:
            default = options[0]

        # 1) inicializace / oprava hodnoty v session_state
        if "radio_output_choice" not in st.session_state:
            st.session_state.radio_output_choice = default
        if st.session_state.radio_output_choice not in options:
            st.session_state.radio_output_choice = default

        # 2) radio BEZ index= (default bere ze session_state přes key)
        if len(options) > 1:
            chosen = st.radio(
                "Co zobrazit ve výstupu",
                options=options,
                horizontal=True,
                key="radio_output_choice",
            )
        else:
            chosen = options[0]
            st.session_state.radio_output_choice = chosen

        if chosen == "Popis produktu":
            out_html = st.session_state.out_html_desc
            fname = "produkt_nova_sablona.html"
            dl_key = "dl_desc"
        elif chosen == "Parametry (tabulka)":
            out_html = st.session_state.out_html_params
            fname = "sid-techparams.html"
            dl_key = "dl_params"
        else:
            out_html = st.session_state.out_html_spin
            fname = "spin_sablona.html"
            dl_key = "dl_spin"

        st.code(out_html, language="html")
        st.download_button(
            "Stáhnout HTML",
            data=out_html,
            file_name=fname,
            mime="text/html",
            width="stretch",
            key=dl_key,
        )

