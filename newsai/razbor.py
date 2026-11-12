"""Разбор ответов источников: HTML в текст, даты, RSS и Atom, страницы со списком записей."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

from .vremya import iz_stroki


class OshibkaRazbora(Exception):
    """Ответ источника не разбирается."""


@dataclass
class Zapis:
    """Запись источника до фильтров."""
    zagolovok: str
    url: str
    data: datetime | None
    tekst: str = ""
    identifikator: str = ""
    tolko_data: bool = False          # у записи день без времени
    kategorii: list = field(default_factory=list)
    html: str = ""                    # исходная разметка тела (для фильтра строк)


_BLOCHNYE = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
             "blockquote", "pre", "section", "article", "header", "footer", "figure", "hr", "dd", "dt"}
_PROPUSK = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "form", "button"}


class _Tekst(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chasti: list[str] = []
        self.propusk = 0
        self.vnutri = {"article": 0, "main": 0}
        self.otdelno = {"article": [], "main": []}

    def _dobavit(self, s: str) -> None:
        self.chasti.append(s)
        for k, glubina in self.vnutri.items():
            if glubina:
                self.otdelno[k].append(s)

    def handle_starttag(self, tag, attrs):
        if tag in _PROPUSK:
            self.propusk += 1
        elif tag in self.vnutri:
            self.vnutri[tag] += 1
        if tag == "li" and not self.propusk:
            self._dobavit("\n- ")
        elif tag in _BLOCHNYE and not self.propusk:
            self._dobavit("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr") and not self.propusk:
            self._dobavit("\n")

    def handle_endtag(self, tag):
        if tag in _PROPUSK:
            self.propusk = max(0, self.propusk - 1)
        elif tag in self.vnutri:
            self.vnutri[tag] = max(0, self.vnutri[tag] - 1)
        if tag in _BLOCHNYE and not self.propusk:
            self._dobavit("\n")

    def handle_data(self, data):
        if not self.propusk:
            self._dobavit(data)


def _szhat(s: str) -> str:
    stroki = [re.sub(r"[ \t\r\f\v ]+", " ", x).strip() for x in s.split("\n")]
    vyhod, pustaya = [], False
    for x in stroki:
        if x in ("", "-"):
            pustaya = bool(vyhod)
            continue
        if pustaya:
            vyhod.append("")
        vyhod.append(x)
        pustaya = False
    return "\n".join(vyhod).strip()


def html_v_tekst(razmetka: str) -> str:
    p = _Tekst()
    p.feed(razmetka or "")
    p.close()
    return _szhat("".join(p.chasti))


def tekst_stranicy(razmetka: str) -> str:
    """Текст страницы: <article>, иначе <main>, иначе вся страница."""
    p = _Tekst()
    p.feed(razmetka or "")
    p.close()
    for k in ("article", "main"):
        t = _szhat("".join(p.otdelno[k]))
        if len(t) >= 300:
            return t
    return _szhat("".join(p.chasti))


def odnoy_strokoy(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_MESYACY = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def data_rfc822(s: str) -> tuple[datetime | None, bool]:
    """Дата RSS → (UTC, только день). 00:00:00 ровно — у записи день без времени."""
    if not s:
        return None, False
    try:
        dt = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError, IndexError):
        try:
            dt = iz_stroki(s)
        except ValueError:
            return None, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt, (dt.hour, dt.minute, dt.second) == (0, 0, 0)


def data_iso(s: str) -> datetime | None:
    try:
        return iz_stroki(s) if s else None
    except ValueError:
        return None


def data_x(s: str) -> datetime | None:
    """'Tue Nov 10 11:33:49 +0000 2026' → UTC."""
    try:
        return datetime.strptime(s.strip(), "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def data_mesyac_den(mesyac: str, den: str, god: int) -> datetime | None:
    n = _MESYACY.get(mesyac.strip()[:3].lower())
    if not n:
        return None
    try:
        return datetime(god, n, int(den), tzinfo=timezone.utc)
    except ValueError:
        return None


_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _koren(tekst: str) -> ET.Element:
    try:
        return ET.fromstring(tekst.lstrip("﻿").strip().encode("utf-8"))
    except ET.ParseError as e:
        raise OshibkaRazbora(f"XML не разбирается: {e}") from e


def _tekst_elementa(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def lenta(tekst: str) -> list[Zapis]:
    """RSS 2.0 или Atom — список записей."""
    koren = _koren(tekst)
    if koren.tag == f"{_ATOM}feed":
        return _atom(koren)
    channel = koren.find("channel")
    if koren.tag != "rss" or channel is None:
        raise OshibkaRazbora(f"не RSS и не Atom: <{koren.tag}>")
    zapisi = []
    for item in channel.findall("item"):
        ssylka = _tekst_elementa(item.find("link"))
        guid = _tekst_elementa(item.find("guid"))
        telo = _tekst_elementa(item.find(_CONTENT)) or _tekst_elementa(item.find("description"))
        data, tolko_data = data_rfc822(_tekst_elementa(item.find("pubDate")))
        zapisi.append(Zapis(
            zagolovok=odnoy_strokoy(html.unescape(_tekst_elementa(item.find("title")))),
            url=ssylka or guid,
            data=data,
            tolko_data=tolko_data,
            tekst=html_v_tekst(telo),
            identifikator=ssylka or guid,
            kategorii=[_tekst_elementa(k) for k in item.findall("category")],
            html=telo,
        ))
    return zapisi


def _atom(koren: ET.Element) -> list[Zapis]:
    zapisi = []
    for e in koren.findall(f"{_ATOM}entry"):
        ssylka = ""
        for l in e.findall(f"{_ATOM}link"):
            if l.get("rel", "alternate") == "alternate":
                ssylka = l.get("href", "")
                break
        telo = _tekst_elementa(e.find(f"{_ATOM}content")) or _tekst_elementa(e.find(f"{_ATOM}summary"))
        data = data_iso(_tekst_elementa(e.find(f"{_ATOM}published")) or _tekst_elementa(e.find(f"{_ATOM}updated")))
        zapisi.append(Zapis(
            zagolovok=odnoy_strokoy(html.unescape(_tekst_elementa(e.find(f"{_ATOM}title")))),
            url=ssylka or _tekst_elementa(e.find(f"{_ATOM}id")),
            data=data,
            tekst=html_v_tekst(telo),
            identifikator=ssylka or _tekst_elementa(e.find(f"{_ATOM}id")),
            kategorii=[k.get("term", "") for k in e.findall(f"{_ATOM}category")],
            html=telo,
        ))
    return zapisi


_KARTA = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def karta_sayta(tekst: str) -> dict[str, datetime]:
    """Карта сайта (sitemap.xml, urlset) → {адрес: день lastmod, полночь UTC}.

    Адрес без lastmod или с неразбираемым lastmod в словарь не попадает; время lastmod
    отбрасывается — у записи день без времени [техника]."""
    koren = _koren(tekst)
    if koren.tag != f"{_KARTA}urlset":
        raise OshibkaRazbora(f"не карта сайта со списком адресов: <{koren.tag}>")
    daty = {}
    for url in koren.findall(f"{_KARTA}url"):
        adres = _tekst_elementa(url.find(f"{_KARTA}loc"))
        lastmod = _tekst_elementa(url.find(f"{_KARTA}lastmod"))
        try:
            den = datetime.strptime(lastmod[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if adres:
            daty[adres] = den
    return daty


def bez_strok(razmetka: str, nachalo: str) -> str:
    """Тело релиза без пунктов, начинающихся с `nachalo`; заголовки разделов не в счёт."""
    vse = [s for s in html_v_tekst(razmetka).split("\n") if s.strip()]
    punkty = [s for s in vse if s.startswith("- ")]
    stroki = punkty if punkty else [s for s in vse if not re.fullmatch(r"what'?s changed", s.strip(), re.I)]
    shum = re.compile(r"^(?:[\w./-]+(?: [\w./-]+){0,2}: )?" + re.escape(nachalo), re.I)
    return "\n".join(s for s in stroki if not shum.match(s.lstrip("- ").strip()))


_KARTOCHKA = re.compile(r'<a href="([^"]+)" class="ResourceCard[^"]*"[^>]*>(.*?)</a>', re.S)
_KARTOCHKA_DATA = re.compile(r'__meta">\s*([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})\s*<')
_KARTOCHKA_ZAGOLOVOK = re.compile(r'__title[^>]*>(.*?)</h\d>', re.S)
_KARTOCHKA_TELO = re.compile(r'__excerpt[^>]*>(.*?)</p>', re.S)


def stranica_statey(razmetka: str, adres: str) -> list[Zapis]:
    """Страница статей claude.com: карточки с адресом, датой и заголовком."""
    zapisi, byli = [], set()
    for m in _KARTOCHKA.finditer(razmetka):
        url = urljoin(adres, html.unescape(m.group(1)))
        telo = m.group(2)
        d = _KARTOCHKA_DATA.search(telo)
        z = _KARTOCHKA_ZAGOLOVOK.search(telo)
        if not z or url in byli:
            continue
        byli.add(url)
        opisanie = _KARTOCHKA_TELO.search(telo)
        zapisi.append(Zapis(
            zagolovok=odnoy_strokoy(html_v_tekst(z.group(1))),
            url=url,
            data=data_mesyac_den(d.group(1), d.group(2), int(d.group(3))) if d else None,
            tolko_data=True,
            tekst=html_v_tekst(opisanie.group(1)) if opisanie else "",
            identifikator=url,
        ))
    if not zapisi:
        raise OshibkaRazbora("на странице статей не нашлось карточек")
    return zapisi


_RAZDEL = re.compile(r'_ChangelogSectionTitle[^>]*>\s*([A-Za-z]+),?\s+(\d{4})\s*<')
_ZAPIS = re.compile(r'<div class="mt-5"(?: id="([^"]+)")?><div class="grid')
_DATA_ZAPISI = re.compile(r'data-variant="outline">\s*([A-Za-z]{3,9})\.? (\d{1,2})\s*</div>')
_METKA = re.compile(r'data-variant="soft">(?:<span[^>]*>)?([^<]+)(?:</span>)?</div>')
_MARKDOWN = re.compile(r'<div class="_MarkdownContent[^"]*"[^>]*>(.*)', re.S)


def stranica_changelog(razmetka: str, adres: str) -> list[Zapis]:
    """Changelog OpenAI API: разделы «Месяц, год», в них записи с датой, метками и текстом.

    У всех записей один адрес страницы: идентификатор — адрес, дата и заголовок записи; адрес пункта —
    с якорем записи, если разметка его даёт (id у блока записи) [техника].
    """
    tochki = [(m.start(), "razdel", m) for m in _RAZDEL.finditer(razmetka)]
    tochki += [(m.start(), "zapis", m) for m in _ZAPIS.finditer(razmetka)]
    tochki.sort(key=lambda x: x[0])
    god = None
    zapisi = []
    for n, (nachalo, vid, m) in enumerate(tochki):
        if vid == "razdel":
            god = int(m.group(2))
            continue
        konec = tochki[n + 1][0] if n + 1 < len(tochki) else len(razmetka)
        kusok = razmetka[nachalo:konec]
        d = _DATA_ZAPISI.search(kusok)
        if not d or god is None:
            continue
        data = data_mesyac_den(d.group(1), d.group(2), god)
        metki = [odnoy_strokoy(html.unescape(x)) for x in _METKA.findall(kusok)]
        md = _MARKDOWN.search(kusok)
        tekst = html_v_tekst(md.group(1)) if md else ""
        if not tekst or data is None:
            continue
        pervoe = re.split(r"(?<=[.!?])\s", odnoy_strokoy(tekst), maxsplit=1)[0]
        zagolovok = ((" · ".join(metki) + ": " if metki else "") + pervoe)[:200]
        yakor = m.group(1)
        zapisi.append(Zapis(
            zagolovok=zagolovok,
            url=f"{adres}#{html.unescape(yakor)}" if yakor else adres,
            data=data,
            tolko_data=True,
            tekst=tekst,
            identifikator=f"{adres}#{data.date().isoformat()} {zagolovok}",
            kategorii=metki,
        ))
    if not zapisi:
        raise OshibkaRazbora("в changelog не нашлось записей с датой")
    return zapisi
