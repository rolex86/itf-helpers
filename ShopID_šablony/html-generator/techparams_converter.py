# techparams_converter.py
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Tag
from jinja2 import Template


@dataclass
class Row:
    label_html: str
    value_html: str


@dataclass
class Section:
    title: str
    rows: List[Row]


def _is_blank_html(html: str) -> bool:
    if html is None:
        return True
    s = html.replace("\xa0", " ").replace("&nbsp;", " ").strip()
    s = s.replace("<br/>", "").replace("<br>", "").strip()
    return s == ""


def _strip_leading_nbsp(html: str) -> str:
    if html is None:
        return ""
    s = html
    s = s.lstrip().lstrip("\xa0").lstrip()
    while s.startswith("&nbsp;"):
        s = s[len("&nbsp;") :].lstrip()
    return s


def _cell_inner_html(td: Tag) -> str:
    return td.decode_contents().strip()


def _strong_text(tag: Tag) -> Optional[str]:
    st = tag.find("strong")
    if not st:
        return None
    txt = st.get_text(strip=True)
    return txt or None


def _find_top_heading_before_table(table: Tag) -> Optional[str]:
    cur = table
    steps = 0
    while cur and steps < 6:
        cur = cur.previous_sibling
        steps += 1
        if not isinstance(cur, Tag):
            continue
        if cur.name in ("p", "div"):
            st = _strong_text(cur)
            if st:
                return st
    return None


def parse_old_tables(html: str) -> Tuple[Optional[str], List[Section]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="tabulka") or soup.find("table")
    if not table:
        return None, []

    top_title = _find_top_heading_before_table(table)

    sections: List[Section] = []
    current: Optional[Section] = None

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        if len(tds) == 2:
            label_td, value_td = tds[0], tds[1]
        elif len(tds) >= 3:
            label_td, value_td = tds[0], tds[2]  # prostřední spacer ignorujeme
        else:
            continue

        # Skip spacer rows (vše prázdné / &nbsp;)
        row_all_html = "".join([_cell_inner_html(td) for td in tds])
        if _is_blank_html(row_all_html):
            continue

        label_html = _cell_inner_html(label_td)
        value_html = _cell_inner_html(value_td)

        # Section row
        section_name = _strong_text(label_td)
        if section_name and _is_blank_html(value_html):
            current = Section(title=section_name, rows=[])
            sections.append(current)
            continue

        # Normal row
        if current is None:
            current = Section(title="PARAMETRY", rows=[])
            sections.append(current)

        label_html = _strip_leading_nbsp(label_html)
        value_html = value_html.strip()
        current.rows.append(Row(label_html=label_html, value_html=value_html))

    sections = [s for s in sections if s.rows]
    return top_title, sections


def parse_new_sid_techparams(html: str) -> Tuple[Optional[str], List[Section]]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("div", class_="sid-techparams")
    if not root:
        return None, []

    top_title: Optional[str] = None
    sections: List[Section] = []

    for block in root.find_all("div", recursive=False):
        classes = block.get("class") or []
        if "sid-tp-spacer" in classes:
            continue

        title_div = block.find("div", class_="sid-tp-title")
        if not title_div:
            continue

        strong = title_div.find("strong")
        if not strong:
            continue
        title = strong.get_text(strip=True)
        if not title:
            continue

        card = block.find("div", class_="sid-tp-card")
        if not card:
            if top_title is None:
                top_title = title
            continue

        rows: List[Row] = []
        for row_div in card.find_all("div", recursive=False):
            row_classes = row_div.get("class") or []
            if "bsuc-flex2" not in row_classes or "bg0" not in row_classes:
                continue

            kids = [k for k in row_div.find_all("div", recursive=False)]
            if len(kids) != 2:
                continue

            label_html = kids[0].decode_contents().strip()
            value_html = kids[1].decode_contents().strip()
            if _is_blank_html(label_html) and _is_blank_html(value_html):
                continue
            rows.append(Row(label_html=_strip_leading_nbsp(label_html), value_html=value_html))

        if rows:
            sections.append(Section(title=title, rows=rows))

    return top_title, sections


def parse_any(html: str) -> Tuple[Optional[str], List[Section]]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("div", class_="sid-techparams"):
        return parse_new_sid_techparams(html)
    return parse_old_tables(html)


def _margin_top(section_title: str, index0: int) -> int:
    title = (section_title or "").strip().upper()
    if index0 == 0:
        return 10
    if "NFC" in title:
        return 20
    return 4


def _read_template_text(filename: str) -> str:
    """
    Robustní načtení šablony:
    1) vedle tohoto .py souboru
    2) PyInstaller onefile (sys._MEIPASS)
    3) aktuální working dir (fallback)
    """
    name = (filename or "").strip()
    if not name:
        raise FileNotFoundError("Template filename is empty.")

    # 1) next to this module
    p1 = Path(__file__).with_name(name)
    if p1.exists():
        return p1.read_text(encoding="utf-8")

    # 2) PyInstaller onefile extraction dir
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p2 = Path(meipass) / name
        if p2.exists():
            return p2.read_text(encoding="utf-8")

    # 3) fallback: current working directory
    p3 = Path(name)
    if p3.exists():
        return p3.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Template '{name}' not found (module dir / MEIPASS / CWD).")


@lru_cache(maxsize=8)
def _get_template(template_path: str) -> Template:
    tpl_src = _read_template_text(template_path)
    return Template(tpl_src)


def render_sid_techparams(
    sections: List[Section],
    template_path: str,
    top_title: Optional[str] = None,
) -> str:
    tpl = _get_template(template_path)
    return tpl.render(
        top_title_html=top_title or "",
        sections=sections,
        margin_top=_margin_top,
    ).strip()


def convert(html: str) -> Tuple[Optional[str], List[Section], str]:
    top_title, sections = parse_any(html)
    if not sections:
        return top_title, [], ""
    out = render_sid_techparams(
        sections=sections,
        template_path="template_tp.j2",
        top_title=top_title,
    )
    return top_title, sections, out
