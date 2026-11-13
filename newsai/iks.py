"""X — цепочка запасных путей (спека, Р13): FxTwitter → встраиваемая лента X → twitterapi.io.

По каждому аккаунту — по очереди до первого рабочего ответа. Ответ 204 FxTwitter — новых постов нет,
это рабочий ответ. Без ответов и без репостов.

Бережный сбор (спека, Р57): аккаунты по одному, пауза между любыми двумя запросами, 429 — ожидание
`Retry-After` и один повтор, 429 встраиваемой ленты выключает её до конца сбора, 404 FxTwitter —
повтор и проверка профиля, общий срок сбора X. Пауза (`spat`) и часы (`chasy`) — функции модуля,
тест их подменяет.

Повторные проходы (спека, Р71): аккаунты без рабочего ответа — в очередь; проход 2 через 60 с —
FxTwitter без `since`, проход 3 через 60 с — с `since`; «аккаунт не найден» — профиль 404 в двух
проходах.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote

from . import nastrojki, sekrety
from .progon import Kandidat, Period
from .razbor import data_x, odnoy_strokoy
from .zaprosy import OshibkaZaprosa

FX = "https://api.fxtwitter.com/2/profile/{akkaunt}/statuses?since={nachalo_okna}"
FX_BEZ_SINCE = "https://api.fxtwitter.com/2/profile/{akkaunt}/statuses"
FX_PROFIL = "https://api.fxtwitter.com/2/profile/{akkaunt}"
SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{akkaunt}"
TWITTERAPI = "https://api.twitterapi.io/twitter/user/last_tweets?userName={akkaunt}&includeReplies=false"
PEREMENNAYA_KLYUCHA = "TWITTERAPI_KEY"
STRANIC_FX = 5

PAUZA_SEKUND = 2
PAUZA_404_SEKUND = 5
ZHDAT_429_SEKUND = 30
ZHDAT_429_MAKS = 60
SROK_X_SEKUND = 480
PROHODOV = 3
PAUZA_PROHODA_SEKUND = 60

NE_NAYDEN = "FxTwitter: аккаунт не найден"
PROFIL_404 = "FxTwitter: профиль 404"
PROFIL_404_ODIN_RAZ = "FxTwitter: профиль 404 один раз"
NE_USPELI = "не успели: общий срок X"
LENTA_VYKLYUCHENA = "лента X: пропущена — 429 раньше в этом сборе"


def spat(sekundy: float) -> None:
    time.sleep(sekundy)


def chasy() -> float:
    return time.monotonic()


class NePoluchilos(Exception):
    """Путь не дал рабочего ответа."""


class NetAkkaunta(Exception):
    """Профиль FxTwitter — 404: аккаунта нет, запасные пути не пробуются."""


class NeUspeli(Exception):
    """Общий срок сбора X вышел."""


def _zhdat_429(otvet) -> float:
    """Retry-After в секундах; нет или не число — 30, больше 60 — 60, не меньше обычной паузы."""
    try:
        sekundy = float(otvet.zagolovki.get("retry-after", ""))
    except (TypeError, ValueError):
        sekundy = ZHDAT_429_SEKUND
    return max(PAUZA_SEKUND, min(sekundy, ZHDAT_429_MAKS))


class Berezhno:
    """Все запросы одного сбора X: по одному, пауза перед каждым, кроме первого; 429 — один повтор."""

    def __init__(self, poluchit, spat_=None, chasy_=None):
        self._poluchit = poluchit
        self._spat = spat_ or spat
        self._chasy = chasy_ or chasy
        self.konec = self._chasy() + SROK_X_SEKUND
        self.byl_zapros = False
        self.lenta_vyklyuchena = False

    def _odin(self, url, zagolovki, pauza):
        ostalos = self.konec - self._chasy()
        nuzhno = pauza if self.byl_zapros else 0
        if ostalos <= 0 or nuzhno >= ostalos:
            raise NeUspeli()
        if nuzhno:
            self._spat(nuzhno)
        self.byl_zapros = True
        return self._poluchit(url, zagolovki) if zagolovki else self._poluchit(url)

    def zapros(self, url, zagolovki=None, pauza=PAUZA_SEKUND):
        otvet = self._odin(url, zagolovki, pauza)
        if otvet.status == 429:
            otvet = self._odin(url, zagolovki, _zhdat_429(otvet))
        return otvet

    def pauza_prohoda(self, sekundy=PAUZA_PROHODA_SEKUND):
        """Пауза перед повторным проходом; не влезает в остаток срока — NeUspeli без паузы."""
        ostalos = self.konec - self._chasy()
        if ostalos <= 0 or sekundy >= ostalos:
            raise NeUspeli()
        self._spat(sekundy)
        self.byl_zapros = False


def _post(akkaunt: str, identifikator: str, tekst: str, data: datetime) -> Kandidat:
    """Пост X → кандидат; имя источника ставит sobrat."""
    url = f"https://x.com/{akkaunt}/status/{identifikator}"
    return Kandidat(identifikator=url, istochnik="", zagolovok=zagolovok(tekst), url=url, data=data,
                    tekst=f"@{akkaunt}: {tekst}", akkaunt=akkaunt)


def _fx_stranica(akkaunt, url, bereg: Berezhno):
    """Страница постов FxTwitter; 404 — пауза 5 с и повтор, снова 404 — проверка профиля."""
    otvet = bereg.zapros(url)
    if otvet.status != 404:
        return otvet
    otvet = bereg.zapros(url, pauza=PAUZA_404_SEKUND)
    if otvet.status != 404:
        return otvet
    try:
        profil = bereg.zapros(FX_PROFIL.format(akkaunt=quote(akkaunt))).status
    except OshibkaZaprosa as e:
        profil = f"ошибка ({e})"
    if profil == 404:
        raise NetAkkaunta()
    raise NePoluchilos(f"FxTwitter 404 на постах после повтора, профиль {profil}")


def _fx(akkaunt, okno: Period, bereg: Berezhno, adres=FX):
    """FxTwitter: [] — новых нет (204); None не бывает — ошибка → NePoluchilos."""
    since = int(okno.nachalo.timestamp())
    url = adres.format(akkaunt=quote(akkaunt), nachalo_okna=since)
    posty = []
    for _ in range(STRANIC_FX):
        otvet = _fx_stranica(akkaunt, url, bereg)
        if otvet.status == 204:
            return posty
        if otvet.status == 429:
            raise NePoluchilos("FxTwitter 429 и после повтора")
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
        url = adres.format(akkaunt=quote(akkaunt), nachalo_okna=since)
        url += ("&" if "?" in url else "?") + "cursor=" + quote(niz, safe="")
    return posty


_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def _syndication(akkaunt, okno: Period, bereg: Berezhno):
    otvet = bereg.zapros(SYNDICATION.format(akkaunt=quote(akkaunt)))
    if otvet.status == 429:
        bereg.lenta_vyklyuchena = True
        raise NePoluchilos("лента X 429 и после повтора — выключена до конца сбора")
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


def _twitterapi(akkaunt, okno: Period, bereg: Berezhno, klyuch):
    otvet = bereg.zapros(TWITTERAPI.format(akkaunt=quote(akkaunt)), {"X-API-Key": klyuch})
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


def odin_akkaunt(akkaunt, okno: Period, bereg: Berezhno, klyuch=None, adres_fx=FX):
    """→ (посты, сработавший путь или None, причины отказов). Срок вышел — NeUspeli наружу.

    Профиль 404 — причины ровно [PROFIL_404], запасные пути в этом проходе не пробуются.
    """
    puti = [("FxTwitter", lambda: _fx(akkaunt, okno, bereg, adres_fx)),
            ("лента X", lambda: _syndication(akkaunt, okno, bereg))]
    if klyuch:
        puti.append(("twitterapi.io", lambda: _twitterapi(akkaunt, okno, bereg, klyuch)))
    otkazy = []
    for imya, put in puti:
        if imya == "лента X" and bereg.lenta_vyklyuchena:
            otkazy.append(LENTA_VYKLYUCHENA)
            continue
        try:
            return put(), imya, otkazy
        except NetAkkaunta:
            return [], None, [PROFIL_404]
        except (NePoluchilos, OshibkaZaprosa) as e:
            otkazy.append(f"{imya}: {e}")
    return [], None, otkazy


def _prichina(prohody: list, ne_uspeli: bool) -> str:
    """Причины молчавшего: по проходам по порядку, «проход N:» — если проходов больше одного."""
    podtverzhden = any(r == NE_NAYDEN for _, r in prohody)
    teksty = [(n, PROFIL_404_ODIN_RAZ if r == PROFIL_404 and not podtverzhden else r) for n, r in prohody]
    chasti = [teksty[0][1]] if len(teksty) == 1 else [f"проход {n}: {r}" for n, r in teksty]
    if ne_uspeli:
        chasti.append(NE_USPELI)
    return " | ".join(chasti)


def sobrat(istochnik: dict, okno: Period, poluchit, spat_=None, chasy_=None) -> tuple[list[Kandidat], dict]:
    """Все аккаунты источника X за окно охвата по одному → (кандидаты, отчёт {puti, prohody, molchat}).

    Проходы (Р71): 1 — все аккаунты; 2 и 3 — только очередь без рабочего ответа, через паузу 60 с.
    puti — путь → число ответивших аккаунтов; prohody — путь → {номер прохода ≥ 2: сколько из них
    ответили с него}; molchat — аккаунт → причины в порядке аккаунтов.
    """
    klyuch = klyuch_twitterapi()
    adres_fx = istochnik.get("adres") or FX
    akkaunty = list(istochnik.get("akkaunty") or [])
    bereg = Berezhno(poluchit, spat_, chasy_)
    posty, puti, prohody = [], {}, {}
    prichiny = {}
    ne_uspeli = set()
    otvetili = set()
    ochered = akkaunty
    for n in range(1, PROHODOV + 1):
        if not ochered:
            break
        if n > 1:
            try:
                bereg.pauza_prohoda()
            except NeUspeli:
                ne_uspeli.update(ochered)
                break
        adres = FX_BEZ_SINCE if n == 2 else adres_fx
        sleduyushchie = []
        for i, akkaunt in enumerate(ochered):
            try:
                p, put, otkazy = odin_akkaunt(akkaunt, okno, bereg, klyuch, adres)
            except NeUspeli:
                ne_uspeli.update(ochered[i:])
                if n < PROHODOV:
                    ne_uspeli.update(sleduyushchie)
                sleduyushchie = []
                break
            if put is None:
                byl_404 = any(r == PROFIL_404 for _, r in prichiny.get(akkaunt, []))
                if otkazy == [PROFIL_404] and byl_404:
                    prichiny[akkaunt].append((n, NE_NAYDEN))
                    continue
                prichiny.setdefault(akkaunt, []).append((n, "; ".join(otkazy)))
                sleduyushchie.append(akkaunt)
                continue
            otvetili.add(akkaunt)
            puti[put] = puti.get(put, 0) + 1
            if n > 1:
                prohody.setdefault(put, {})
                prohody[put][n] = prohody[put].get(n, 0) + 1
            posty.extend(replace(k, istochnik=istochnik["imya"]) for k in p)
        ochered = sleduyushchie
    molchat = {a: _prichina(prichiny.get(a, []), a in ne_uspeli) for a in akkaunty if a not in otvetili}
    return posty, {"puti": puti, "prohody": prohody, "molchat": molchat}


def zagolovok(tekst: str) -> str:
    return odnoy_strokoy(tekst)[: nastrojki.ZAGOLOVOK_X_ZNAKOV]
