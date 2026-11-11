"""X — цепочка запасных путей (спека, Р13): FxTwitter → встраиваемая лента X → twitterapi.io.

По каждому аккаунту — по очереди до первого рабочего ответа. Ответ 204 FxTwitter — новых постов нет,
это рабочий ответ. Без ответов и без репостов.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote

from . import nastrojki, sekrety
from .progon import Kandidat, Period
from .razbor import data_x, odnoy_strokoy
from .zaprosy import OshibkaZaprosa

FX = "https://api.fxtwitter.com/2/profile/{akkaunt}/statuses?since={nachalo_okna}"
SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{akkaunt}"
TWITTERAPI = "https://api.twitterapi.io/twitter/user/last_tweets?userName={akkaunt}&includeReplies=false"
PEREMENNAYA_KLYUCHA = "TWITTERAPI_KEY"
STRANIC_FX = 5
POTOKOV = 6


class NePoluchilos(Exception):
    """Путь не дал рабочего ответа."""


def _post(akkaunt: str, identifikator: str, tekst: str, data: datetime) -> Kandidat:
    """Пост X → кандидат; имя источника ставит sobrat."""
    url = f"https://x.com/{akkaunt}/status/{identifikator}"
    return Kandidat(identifikator=url, istochnik="", zagolovok=zagolovok(tekst), url=url, data=data,
                    tekst=f"@{akkaunt}: {tekst}", akkaunt=akkaunt)


def _fx(akkaunt, okno: Period, poluchit, adres=FX):
    """FxTwitter: [] — новых нет (204); None не бывает — ошибка → NePoluchilos."""
    since = int(okno.nachalo.timestamp())
    url = adres.format(akkaunt=quote(akkaunt), nachalo_okna=since)
    posty = []
    for _ in range(STRANIC_FX):
        otvet = poluchit(url)
        if otvet.status == 204:
            return posty
        if otvet.status != 200:
            raise NePoluchilos(f"FxTwitter {otvet.status}")
        try:
            dannye = json.loads(otvet.tekst())
        except ValueError as e:
            raise NePoluchilos(f"FxTwitter: не JSON ({e})")
        if not isinstance(dannye, dict) or dannye.get("code") not in (200, None) or "results" not in dannye:
            raise NePoluchilos(f"FxTwitter: ответ без results (code {dannye.get('code') if isinstance(dannye, dict) else '?'})")
        rezultaty = dannye.get("results") or []
        samyy_staryy = None
        for p in rezultaty:
            if not isinstance(p, dict) or p.get("type", "status") != "status":
                continue
            ts = p.get("created_timestamp")
            data = datetime.fromtimestamp(ts, tz=okno.nachalo.tzinfo) if ts else data_x(p.get("created_at", ""))
            if data is None:
                continue
            samyy_staryy = data if samyy_staryy is None else min(samyy_staryy, data)
            if p.get("reposted_by") or p.get("replying_to"):
                continue
            if data not in okno:
                continue
            tekst = p.get("text") or ""
            citata = p.get("quote")
            if isinstance(citata, dict) and citata.get("text"):
                kto = (citata.get("author") or {}).get("screen_name", "")
                tekst += f"\n\nЦитирует @{kto}: {citata['text']}"
            posty.append(_post(akkaunt, str(p.get("id")), tekst, data))
        niz = (dannye.get("cursor") or {}).get("bottom")
        if not rezultaty or not niz or samyy_staryy is None or samyy_staryy < okno.nachalo:
            return posty
        url = adres.format(akkaunt=quote(akkaunt), nachalo_okna=since) + "&cursor=" + quote(niz, safe="")
    return posty


_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _syndication(akkaunt, okno: Period, poluchit):
    otvet = poluchit(SYNDICATION.format(akkaunt=quote(akkaunt)))
    if otvet.status != 200:
        raise NePoluchilos(f"лента X {otvet.status}")
    m = _NEXT_DATA.search(otvet.tekst())
    if not m:
        raise NePoluchilos("лента X: нет данных страницы")
    try:
        zapisi = json.loads(m.group(1))["props"]["pageProps"]["timeline"]["entries"]
    except (ValueError, KeyError, TypeError) as e:
        raise NePoluchilos(f"лента X: не разобрать ({e})")
    posty = []
    for z in zapisi:
        t = (z.get("content") or {}).get("tweet") or {}
        if not t:
            continue
        avtor = (t.get("user") or {}).get("screen_name", akkaunt)
        if t.get("retweeted_status") or avtor.lower() != akkaunt.lower():
            continue
        if t.get("in_reply_to_screen_name") or t.get("in_reply_to_status_id_str"):
            continue
        data = data_x(t.get("created_at", ""))
        if data is None or data not in okno:
            continue
        tekst = t.get("full_text") or t.get("text") or ""
        for u in (t.get("entities") or {}).get("urls") or []:
            if u.get("url") and u.get("expanded_url"):
                tekst = tekst.replace(u["url"], u["expanded_url"])
        posty.append(_post(akkaunt, t.get("id_str") or str(t.get("id")), tekst, data))
    return posty


def klyuch_twitterapi() -> str | None:
    return sekrety.prochitat(PEREMENNAYA_KLYUCHA)


def _twitterapi(akkaunt, okno: Period, poluchit, klyuch):
    otvet = poluchit(TWITTERAPI.format(akkaunt=quote(akkaunt)), {"X-API-Key": klyuch})
    if otvet.status != 200:
        raise NePoluchilos(f"twitterapi.io {otvet.status}")
    try:
        dannye = json.loads(otvet.tekst())
        tvity = (dannye.get("data") or {}).get("tweets") or dannye.get("tweets") or []
    except (ValueError, AttributeError) as e:
        raise NePoluchilos(f"twitterapi.io: не разобрать ({e})")
    if dannye.get("status") not in ("success", None):
        raise NePoluchilos(f"twitterapi.io: {dannye.get('msg') or dannye.get('status')}")
    posty = []
    for t in tvity:
        if t.get("retweeted_tweet") or t.get("isReply"):
            continue
        data = data_x(t.get("createdAt", ""))
        if data is None or data not in okno:
            continue
        posty.append(_post(akkaunt, str(t.get("id")), t.get("text") or "", data))
    return posty


def odin_akkaunt(akkaunt, okno: Period, poluchit, klyuch=None, adres_fx=FX):
    """→ (посты, сработавший путь или None, причины отказов)."""
    puti = [("FxTwitter", lambda: _fx(akkaunt, okno, poluchit, adres_fx)),
            ("лента X", lambda: _syndication(akkaunt, okno, poluchit))]
    if klyuch:
        puti.append(("twitterapi.io", lambda: _twitterapi(akkaunt, okno, poluchit, klyuch)))
    otkazy = []
    for imya, put in puti:
        try:
            return put(), imya, otkazy
        except (NePoluchilos, OshibkaZaprosa) as e:
            otkazy.append(f"{imya}: {e}")
    return [], None, otkazy


def sobrat(istochnik: dict, okno: Period, poluchit) -> tuple[list[Kandidat], dict]:
    """Все аккаунты источника X за окно охвата → (кандидаты, отчёт {puti: {путь: число аккаунтов}, molchat})."""
    klyuch = klyuch_twitterapi()
    adres_fx = istochnik.get("adres") or FX
    akkaunty = istochnik.get("akkaunty") or []
    with ThreadPoolExecutor(max_workers=POTOKOV) as pul:
        itogi = list(pul.map(lambda a: (a, *odin_akkaunt(a, okno, poluchit, klyuch, adres_fx)),
                             akkaunty))
    posty, puti, molchat = [], {}, {}
    for akkaunt, p, put, otkazy in itogi:
        if put is None:
            molchat[akkaunt] = "; ".join(otkazy)
            continue
        puti[put] = puti.get(put, 0) + 1
        posty.extend(replace(k, istochnik=istochnik["imya"]) for k in p)
    return posty, {"puti": puti, "molchat": molchat}


def zagolovok(tekst: str) -> str:
    return odnoy_strokoy(tekst)[: nastrojki.ZAGOLOVOK_X_ZNAKOV]
