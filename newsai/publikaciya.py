"""Публикация выпуска в Telegram и сообщения о сбое (спека «Сообщения Telegram»).

Читает файл выпуска или файл сбоя (nastrojki.fayl_vypuska / fayl_sboya) и файл прогона
(progon.prochitat); токен — tokenbota.zagruzit_token; канал — nastrojki.KANAL_ID. После каждого
отправленного сообщения — sostoyanie.zapisat_punkt, в конце — sostoyanie.zavershit_progon.

У публикатора файл прогона — его собственный сбор (`sbor --konec-iz-zayavki`), файл выпуска —
очищенный `proverit --vyhod`: служебная строка сбора, конец периода и увиденное — из сбора публикатора.
Обрыв отправки — одна строка сбоя, её шлёт сам opublikovat; вызывающий второй не шлёт. Строка сбоя в
канал — слова по таблице (slova_sboya, Р66), причина и текст ошибки — только в stderr.

Файл выпуска (Р47–Р50):
    {
      "krupnye": [{"zagolovok", "abzacy": [5–8 строк], "url", "oboroty": bool, "idy": [...]}],   # до 3
      "sayty":   [{"zagolovok", "url", "istochnik", "zvezda": bool, "idy": [...]}],             # до 10
      "x":       [{"zagolovok", "url", "akkaunt", "zvezda": bool, "idy": [...]}],               # до 10
      "dengi":   [{"zagolovok", "url", "istochnik" | "akkaunt", "zvezda": bool, "idy": [...]}], # до 5
      "github":  [{"zagolovok", "url", "idy": [...]}]
    }
У пункта денег ровно одно из istochnik и akkaunt: akkaunt — у поста X, строка тогда как в списке X.
Файл сбоя: {"prichina": "<одна из PRICHINY_FAYLA_SBOYA>"}.
"""
from __future__ import annotations

import html
import http.client
import json
import re
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import nastrojki, progon, sostoyanie, tokenbota
from .vremya import iz_stroki, mestnoe_vremya, seychas, v_stroku
from .vremya import slot as chas_slota

HOST_API = "api.telegram.org"
POPYTOK_429 = 5
POPYTOK_PODKLYUCHENIYA = 3
PAUZA_PODKLYUCHENIYA = 5
POVTOROV_POSLE_OTPRAVKI = 1
ZAGOLOVOK_ZNAKOV = 300
ABZACEV = (5, 8)
IMYA_GITHUB = "GitHub Claude Code"
NESOHRANENO = "состояние не сохранено — следующий выпуск может повторить новости"
POMETKA_OBOROTY = "⭐ набирает обороты"
ZAYAVKA_NE_PRISHLA = "заявка агента не пришла"
OKNO_SLOTA_MINUT = 40
SBOR_UPAL = "сбор упал"
NIKTO_NE_OTVETIL = "не ответил ни один источник"
PROVERKA_NE_PROSHLA = "проверка выпуска не прошла"
PRICHINY_FAYLA_SBOYA = (SBOR_UPAL, NIKTO_NE_OTVETIL, PROVERKA_NE_PROSHLA)
SBOR_PUBLIKATORA_NE_UDALSYA = "сбор публикатора не удался"
ZAYAVKA_NE_PROSHLA = "заявка не прошла проверку:"
OBRYV_CHAST = "обрыв связи, часть выпуска ушла"
OBRYV_NICHEGO = "обрыв связи на первом сообщении"
OTKAZ_CHAST = "отказ Bot API, часть выпуска ушла"
OTKAZ_NICHEGO = "отказ Bot API на первом сообщении"
NE_SOBRAN = "не собран"
SLOVA_SBOYA = {
    SBOR_UPAL: "не собран: сбор новостей упал",
    NIKTO_NE_OTVETIL: "не собран: ни один источник не ответил",
    PROVERKA_NE_PROSHLA: "не собран: выпуск не прошёл проверку",
    SBOR_PUBLIKATORA_NE_UDALSYA: "не собран: не удалось перепроверить новости перед публикацией",
    ZAYAVKA_NE_PRISHLA: "не собран: агент не прислал выпуск",
    OBRYV_CHAST: "отправлен не полностью — Telegram оборвал связь",
    OBRYV_NICHEGO: "не отправлен — Telegram оборвал связь",
    OTKAZ_CHAST: "отправлен не полностью — Telegram не принял сообщение",
    OTKAZ_NICHEGO: "не отправлен — Telegram не принял сообщение",
}
SLOVA_ZAYAVKA_NE_PROSHLA = "не собран: выпуск не прошёл проверку"
PODPISI_SPISKOV = {"sayty": "🌐 Блоги компаний", "x": "𝕏 Посты в X", "dengi": "💰 Деньги и сделки",
                   "github": "🛠 GitHub Claude Code"}
SBOR_NACHALO = "ℹ️ Сбор: "
SBOR_PUSTO = "ℹ️ Новых новостей нет · "
SBOR_NE_OTOBRANO = "ℹ️ В выпуск ничего не отобрано · "

RAZDELY = (
    ("krupnye", 3, ("zagolovok", "abzacy", "url", "oboroty", "idy")),
    ("sayty", 10, ("zagolovok", "url", "istochnik", "zvezda", "idy")),
    ("x", 10, ("zagolovok", "url", "akkaunt", "zvezda", "idy")),
    ("dengi", 5, ("zagolovok", "url", "istochnik", "akkaunt", "zvezda", "idy")),
    ("github", None, ("zagolovok", "url", "idy")),
)
ODNO_IZ = {"dengi": ("istochnik", "akkaunt")}
NAZVANIYA = {"krupnye": "крупная новость", "sayty": "список сайтов", "x": "список X",
             "dengi": "список денег и сделок", "github": "список GitHub", "sbor": "служебная строка сбора"}
VID_PUNKTA = {"krupnye": "krupnaya", "sayty": "sayty", "x": "x", "dengi": "dengi", "github": "github"}

_UPRAVLYAYUSHCHIE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_AKKAUNT = re.compile(r"^[A-Za-z0-9_]{1,15}$")

Otpravitel = Callable[[str, dict], dict]


class OtkazVypuska(Exception):
    """Выпуск не годится к отправке — ничего не отправлено."""


OBRYV, OTKAZ = "obryv", "otkaz"


class OshibkaTelegram(Exception):
    """Отправка не удалась: vid — OBRYV (повторы по виду обрыва исчерпаны) или OTKAZ (ответ ok: false
    не 429, 429 сверх ожиданий, ответ не JSON, негодный запрос); текст — для лога, без токена."""

    def __init__(self, tekst: str, vid: str = OTKAZ):
        super().__init__(tekst)
        self.vid = vid


def slot(kogda: datetime) -> str:
    """Слот момента kogda — начало слота по Бангкоку в UTC, '2026-11-11T02:00:00Z' для 09:00.

    Это значение пишется в posledniy_slot состояния.
    """
    mestnoe = kogda.astimezone(timezone(timedelta(hours=nastrojki.SDVIG_BANGKOKA_CHASOV)))
    chas, minuta = (int(c) for c in chas_slota(kogda).split(":"))
    nachalo = mestnoe.replace(hour=chas, minute=minuta, second=0, microsecond=0)
    if nachalo > mestnoe:
        nachalo -= timedelta(days=1)
    return v_stroku(nachalo)


def v_okne_slota(kogda: datetime, nachalo_slota: datetime | None = None) -> bool:
    """Момент kogda — в окне слота [начало, начало + OKNO_SLOTA_MINUT); слот по умолчанию — свой."""
    nachalo = nachalo_slota if nachalo_slota is not None else iz_stroki(slot(kogda))
    return nachalo <= kogda < nachalo + timedelta(minutes=OKNO_SLOTA_MINUT)


def sudimyy_slot(kogda: datetime) -> str:
    """Слот, который судит сторож, запущенный в kogda (Р70): последний слот, чьё окно закрылось.

    Слот момента запуска, если запуск не раньше его начала плюс OKNO_SLOTA_MINUT; иначе — предыдущий
    (на 8 часов раньше, через полночь — по slot()) [техника].
    """
    nachalo = iz_stroki(slot(kogda))
    if kogda < nachalo + timedelta(minutes=OKNO_SLOTA_MINUT):
        nachalo = iz_stroki(slot(nachalo - timedelta(seconds=1)))
    return v_stroku(nachalo)


def _ekranirovat_tekst(s: str) -> str:
    """Экранировать текст для parse_mode HTML."""
    return html.escape(s, quote=False)


def _ekranirovat_atribut(s: str) -> str:
    """Экранировать значение атрибута href."""
    return html.escape(s, quote=True)


def dlina(tekst: str) -> int:
    """Длина так, как её считает Telegram: в кодовых единицах UTF-16."""
    return len(tekst.encode("utf-16-le")) // 2


def _stroka(znachenie, gde: str, pole: str) -> str:
    if not isinstance(znachenie, str) or not znachenie.strip():
        raise OtkazVypuska(f"{gde}: нет поля {pole}")
    if _UPRAVLYAYUSHCHIE.search(znachenie):
        raise OtkazVypuska(f"{gde}: в поле {pole} управляющие символы")
    return znachenie.strip()


def _punkt(razdel: str, polya: tuple, punkt, nomer: int) -> dict:
    """Проверить пункт по схеме раздела и вернуть его поля, очищенные от краевых пробелов."""
    gde = f"{NAZVANIYA[razdel]}, пункт {nomer}"
    if not isinstance(punkt, dict):
        raise OtkazVypuska(f"{gde}: не объект JSON")
    lishnie = sorted(set(punkt) - set(polya))
    if lishnie:
        raise OtkazVypuska(f"{gde}: лишние поля {', '.join(lishnie)}")
    odno_iz = ODNO_IZ.get(razdel, ())
    if odno_iz and sum(pole in punkt for pole in odno_iz) != 1:
        raise OtkazVypuska(f"{gde}: нужно ровно одно поле из {' и '.join(odno_iz)}")
    p = {}
    for pole in polya:
        if pole in odno_iz and pole not in punkt:
            continue
        if pole not in punkt:
            raise OtkazVypuska(f"{gde}: нет поля {pole}")
        z = punkt[pole]
        if pole in ("oboroty", "zvezda"):
            if not isinstance(z, bool):
                raise OtkazVypuska(f"{gde}: {pole} — не true/false")
            p[pole] = z
        elif pole == "abzacy":
            if not isinstance(z, list) or not ABZACEV[0] <= len(z) <= ABZACEV[1]:
                raise OtkazVypuska(f"{gde}: abzacy — список из {ABZACEV[0]}–{ABZACEV[1]} строк")
            p[pole] = [_stroka(a, gde, "abzacy") for a in z]
        elif pole == "idy":
            if not isinstance(z, list) or not z:
                raise OtkazVypuska(f"{gde}: idy — непустой список идентификаторов")
            p[pole] = [_stroka(i, gde, "idy") for i in z]
        else:
            p[pole] = _stroka(z, gde, pole)
    if len(p["zagolovok"]) > ZAGOLOVOK_ZNAKOV:
        raise OtkazVypuska(f"{gde}: заголовок длиннее {ZAGOLOVOK_ZNAKOV} знаков")
    if not p["url"].lower().startswith(("http://", "https://")):
        raise OtkazVypuska(f"{gde}: адрес не http(s)")
    if "akkaunt" in p and not _AKKAUNT.fullmatch(p["akkaunt"]):
        raise OtkazVypuska(f"{gde}: akkaunt — имя аккаунта X без @")
    return p


def _ssylka(p: dict) -> str:
    return f'<a href="{_ekranirovat_atribut(p["url"])}">{_ekranirovat_tekst(p["zagolovok"])}</a>'


def _zapis(razdel: str, p: dict) -> dict:
    """Что записать в состояние после отправки пункта."""
    return {"zagolovok": p["zagolovok"], "url": p["url"], "idy": p["idy"],
            "vid": VID_PUNKTA[razdel], "oboroty": p.get("oboroty", False)}


def _krupnaya(p: dict) -> dict:
    zagolovok = f"<b>{_ssylka(p)}</b>"
    if p["oboroty"]:
        zagolovok = f"{POMETKA_OBOROTY}\n{zagolovok}"
    tekst = "\n\n".join([zagolovok, *(_ekranirovat_tekst(a) for a in p["abzacy"])])
    return {"vid": "krupnye", "tekst": tekst, "prevyu": p["url"], "punkty": [_zapis("krupnye", p)]}


def _stroka_spiska(razdel: str, n: int, p: dict, podpisi: dict) -> str:
    zvezda = "⭐ " if p.get("zvezda") else ""
    if "akkaunt" in p:
        podpis = podpisi.get(p["akkaunt"].lower()) or f"@{p['akkaunt']}"
        return f"{n}. {zvezda}{_ekranirovat_tekst(podpis)}: {_ssylka(p)}"
    if razdel == "github":
        return f"{n}. {_ssylka(p)} ({_ekranirovat_tekst(IMYA_GITHUB)}) · скоро"
    return f"{n}. {zvezda}{_ssylka(p)} ({_ekranirovat_tekst(p['istochnik'])})"


def _spisok(razdel: str, punkty: list[dict], podpisi: dict) -> dict:
    stroki = [_ekranirovat_tekst(PODPISI_SPISKOV[razdel])]
    stroki += [_stroka_spiska(razdel, n, p, podpisi) for n, p in enumerate(punkty, 1)]
    return {"vid": razdel, "tekst": "\n".join(stroki), "prevyu": None,
            "punkty": [_zapis(razdel, p) for p in punkty]}


def _iz_nastroek(funkciya: Callable[[], dict]) -> dict:
    """Справочник из файла настроек источников; файл не читается — OtkazVypuska [техника]."""
    try:
        return funkciya()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise OtkazVypuska(f"файл настроек источников не читается: {type(e).__name__}") from None


def soobshcheniya(vypusk: dict) -> list[dict]:
    """Файл выпуска → сообщения по порядку Р49: крупные, сайты, X, деньги, GitHub.

    Сообщение: vid (krupnye, sayty, x, dengi, github; у служебной строки — sbor), tekst (HTML),
    prevyu (адрес превью или None — выключено), punkty (что записать в состояние после отправки).
    Подписи X — из файла настроек источников (nastrojki). Пустой выпуск — пустой список; служебную
    строку сбора добавляет soobshchenie_sbora. Негодный выпуск — OtkazVypuska.
    """
    if not isinstance(vypusk, dict):
        raise OtkazVypuska("файл выпуска — не объект JSON")
    lishnie = sorted(set(vypusk) - {r for r, _, _ in RAZDELY})
    if lishnie:
        raise OtkazVypuska(f"в файле выпуска лишние поля: {', '.join(lishnie)}")

    razdely = {}
    for razdel, predel, polya in RAZDELY:
        punkty = vypusk.get(razdel, [])
        if not isinstance(punkty, list):
            raise OtkazVypuska(f"{razdel} — не список")
        if predel is not None and len(punkty) > predel:
            raise OtkazVypuska(f"{NAZVANIYA[razdel]}: пунктов {len(punkty)}, можно до {predel}")
        razdely[razdel] = [_punkt(razdel, polya, p, n) for n, p in enumerate(punkty, 1)]
    if not any(razdely.values()):
        return []

    nuzhny_podpisi = any("akkaunt" in p for r in ("x", "dengi") for p in razdely[r])
    podpisi = _iz_nastroek(nastrojki.podpisi_x) if nuzhny_podpisi else {}
    vyhod = [_krupnaya(p) for p in razdely["krupnye"]]
    for razdel, _, _ in RAZDELY[1:]:
        if razdely[razdel]:
            vyhod.append(_spisok(razdel, razdely[razdel], podpisi))
    _proverit_dlinu(vyhod)
    return vyhod


def _proverit_dlinu(vyhod: list[dict]) -> None:
    for n, s in enumerate(vyhod, 1):
        if dlina(s["tekst"]) > nastrojki.LIMIT_SOOBSHCHENIYA:
            raise OtkazVypuska(f"сообщение {n} ({NAZVANIYA[s['vid']]}) длиннее "
                               f"{nastrojki.LIMIT_SOOBSHCHENIYA} знаков: {dlina(s['tekst'])} — сократите текст")


def _imena(znachenie) -> list[str]:
    """Непустые строки списка без повторов, в прежнем порядке; не список — пусто [техника]."""
    if not isinstance(znachenie, (list, tuple)):
        return []
    return list(dict.fromkeys(i.strip() for i in znachenie if isinstance(i, str) and i.strip()))


def _ne_otvetil(imena: list[str], vse: bool) -> str:
    """« (не ответил: A)» / « (не ответили: A, B)»; не ответил ни один из нескольких — так и пишем."""
    if not imena:
        return ""
    if vse and len(imena) > 1:
        return " (не ответил ни один)"
    return f" ({'не ответил' if len(imena) == 1 else 'не ответили'}: {', '.join(imena)})"


def stroka_sbora(dannye_progona: dict, v_vypuske: int) -> str:
    """Служебная строка сбора (Р74) обычным текстом, по файлу прогона — сбору публикатора.

    `ℹ️ Сбор: источники 13 из 14 (не ответил: Techmeme) · X 27 из 30 (не ответили: @a, @b, @c) ·
    найдено 40, новых 9, в выпуске 5`; X ответил весь — `X 30 из 30 аккаунтов`; выпуск пустой —
    `ℹ️ Новых новостей нет · источники 14 из 14 · X 30 из 30 · найдено 25, новых 0`.
    Источники — istochniki прогона (все, кроме X), не ответившие — из ne_otvetili, названия — как в
    канале (v_kanale или имя). X — по iks прогона. Файл прогона без istochniki — источники из
    настроек; без iks — X только когда X целиком в не ответивших (0 из N по настройкам) [техника].
    найдено — кандидаты прогона, новых — с novyy, в выпуске — пункты опубликованного выпуска.
    """
    if not isinstance(dannye_progona, dict):
        raise OtkazVypuska("файл прогона — не объект JSON")
    ne_otvetili = _imena(dannye_progona.get("ne_otvetili"))
    istochniki = dannye_progona.get("istochniki")
    iks = dannye_progona.get("iks")
    nastroyki = None
    if not isinstance(istochniki, list) or "iks" not in dannye_progona:
        nastroyki = _iz_nastroek(nastrojki.istochniki_dlya_sbora)
    istochniki = _imena(istochniki) if isinstance(istochniki, list) else nastroyki["istochniki"]
    nazvaniya = _iz_nastroek(nastrojki.nazvaniya_v_kanale) if ne_otvetili else {}

    upali = [i for i in istochniki if i in ne_otvetili]
    slova = list(dict.fromkeys(nazvaniya.get(i) or i for i in upali))
    chasti = [f"источники {len(istochniki) - len(upali)} из {len(istochniki)}"
              + _ne_otvetil(slova, vse=bool(upali) and len(upali) == len(istochniki))]

    pusto = v_vypuske == 0
    if isinstance(iks, dict):
        vsego = iks.get("akkauntov")
        molchat = _imena(iks.get("ne_otvetili"))
        if isinstance(vsego, int) and not isinstance(vsego, bool) and vsego >= len(molchat):
            stroka = f"X {vsego - len(molchat)} из {vsego}"
            if molchat:
                stroka += _ne_otvetil([f"@{a.lstrip('@')}" for a in molchat], vse=len(molchat) == vsego)
            elif not pusto:
                stroka += " аккаунтов"
            chasti.append(stroka)
    elif nastroyki is not None and nastroyki["iks"] is not None:
        imya_x, vsego = nastroyki["iks"]
        if imya_x in ne_otvetili:
            chasti.append(f"X 0 из {vsego}")

    kandidaty = [k for k in dannye_progona.get("kandidaty") or [] if isinstance(k, dict)]
    novyh = sum(1 for k in kandidaty if k.get("novyy", True) is not False)
    schet = f"найдено {len(kandidaty)}, новых {novyh}"
    if not pusto or novyh:
        schet += f", в выпуске {v_vypuske}"
    chasti.append(schet)
    if not pusto:
        nachalo = SBOR_NACHALO
    else:
        nachalo = SBOR_NE_OTOBRANO if novyh else SBOR_PUSTO
    return nachalo + " · ".join(chasti)


def soobshchenie_sbora(dannye_progona: dict, soobshch: list[dict]) -> dict:
    """Служебная строка сбора сообщением (Р74): HTML с экранированием, превью выключено, в состояние
    ничего; «в выпуске» — пункты сообщений soobshch (крупные и строки списков)."""
    v_vypuske = sum(len(s["punkty"]) for s in soobshch)
    s = {"vid": "sbor", "tekst": _ekranirovat_tekst(stroka_sbora(dannye_progona, v_vypuske)),
         "prevyu": None, "punkty": []}
    _proverit_dlinu([s])
    return s


def zapros(soobshchenie: dict) -> dict:
    """Параметры sendMessage для сообщения. Звук не задаётся (Р27)."""
    parametry = {"chat_id": nastrojki.KANAL_ID, "text": soobshchenie["tekst"], "parse_mode": "HTML"}
    if soobshchenie["prevyu"]:
        parametry["link_preview_options"] = {"url": soobshchenie["prevyu"]}
    else:
        parametry["link_preview_options"] = {"is_disabled": True}
    return parametry


def _soedinenie() -> http.client.HTTPSConnection:
    """Новое (ещё не открытое) HTTPS-соединение с Bot API; тест подменяет эту функцию [техника]."""
    return http.client.HTTPSConnection(HOST_API, timeout=nastrojki.TAYMAUT_SEKUND,
                                       context=ssl.create_default_context())


def _pauza(sekund: float) -> None:
    """Пауза между попытками; тест подменяет эту функцию [техника]."""
    time.sleep(sekund)


class _Sboj(Exception):
    """Сбой сети на одном запросе: etap — DO_OTPRAVKI или POSLE_OTPRAVKI, isklyuchenie — исходное."""

    def __init__(self, etap: str, isklyuchenie: BaseException):
        super().__init__(etap)
        self.etap, self.isklyuchenie = etap, isklyuchenie


DO_OTPRAVKI, POSLE_OTPRAVKI = "do", "posle"
_SETEVYE = (OSError, http.client.HTTPException)


class BotApi:
    """Отправитель через Bot API (раздел спеки «Отправка», Р67): одно постоянное HTTPS-соединение на
    всю команду; вызов (метод, параметры) → поле result ответа; ошибка — OshibkaTelegram с видом.

    Повторы — на одно сообщение: сбой до отправки (подключение, TLS, запись запроса) — новое
    соединение, пауза, всего до POPYTOK_PODKLYUCHENIYA попыток; сбой после отправки (ответ не
    прочитан) — новое соединение и POVTOROV_POSLE_OTPRAVKI повтор; 429 — ждать retry_after на том же
    соединении до POPYTOK_429 раз. Токен — только в пути запроса, ни в одном тексте ошибки.
    """

    def __init__(self, token: str, soedinenie: Callable[[], http.client.HTTPConnection] | None = None,
                 pauza: Callable[[float], None] | None = None):
        self._token = token
        self._fabrika = soedinenie
        self._pauza = pauza
        self._soed = None
        self.schetchiki = {"soedineniy": 0, "sboev_do_otpravki": 0, "sboev_posle_otpravki": 0,
                           "povtorov": 0, "ozhidaniy_429": 0}

    def _skryt(self, tekst: str) -> str:
        return tekst.replace(self._token, "***") if self._token else tekst

    def zakryt(self) -> None:
        """Закрыть соединение; следующий запрос откроет новое."""
        soed, self._soed = self._soed, None
        if soed is not None:
            try:
                soed.close()
            except Exception:
                pass

    def _podklyuchit(self):
        """Открытое соединение: прежнее, если сервер его не закрыл, иначе новое с явным connect()."""
        if self._soed is not None and self._soed.sock is None:
            self.zakryt()
        if self._soed is None:
            self._soed = (self._fabrika or _soedinenie)()
            self.schetchiki["soedineniy"] += 1
            try:
                self._soed.connect()
            except _SETEVYE as e:
                raise _Sboj(DO_OTPRAVKI, e) from None
        return self._soed

    def _zapros(self, put: str, telo: bytes) -> tuple[int, bytes]:
        """Один запрос на постоянном соединении → (статус, тело ответа целиком); сеть — _Sboj."""
        soed = self._podklyuchit()
        try:
            soed.request("POST", put, body=telo, headers={"Content-Type": "application/json"})
        except http.client.InvalidURL:
            raise
        except _SETEVYE as e:
            raise _Sboj(DO_OTPRAVKI, e) from None
        try:
            otvet = soed.getresponse()
            status, syroe = otvet.status, otvet.read()
        except http.client.InvalidURL:
            raise
        except _SETEVYE as e:
            raise _Sboj(POSLE_OTPRAVKI, e) from None
        if otvet.will_close:
            self.zakryt()
        return status, syroe

    def _otpravit(self, metod: str, parametry: dict) -> dict:
        put = f"/bot{self._token}/{metod}"
        telo = json.dumps(parametry, ensure_ascii=False).encode("utf-8")
        sboev_do, povtorov_posle, ozhidaniy = 0, 0, 0
        pauza = self._pauza or _pauza
        while True:
            try:
                status, syroe = self._zapros(put, telo)
            except _Sboj as s:
                self.zakryt()
                if s.etap == DO_OTPRAVKI:
                    sboev_do += 1
                    self.schetchiki["sboev_do_otpravki"] += 1
                    if sboev_do >= POPYTOK_PODKLYUCHENIYA:
                        raise OshibkaTelegram(f"{metod}: нет связи с Bot API после {sboev_do} попыток: "
                                              f"{type(s.isklyuchenie).__name__}: {s.isklyuchenie}", OBRYV) from None
                    pauza(PAUZA_PODKLYUCHENIYA)
                else:
                    self.schetchiki["sboev_posle_otpravki"] += 1
                    if povtorov_posle >= POVTOROV_POSLE_OTPRAVKI:
                        raise OshibkaTelegram(f"{metod}: нет ответа Bot API после повтора: "
                                              f"{type(s.isklyuchenie).__name__}: {s.isklyuchenie}", OBRYV) from None
                    povtorov_posle += 1
                self.schetchiki["povtorov"] += 1
                continue
            try:
                dannye = json.loads(syroe.decode("utf-8"))
            except ValueError:
                raise OshibkaTelegram(f"{metod}: ответ не JSON, HTTP {status}", OTKAZ) from None
            if not isinstance(dannye, dict):
                raise OshibkaTelegram(f"{metod}: ответ не объект JSON, HTTP {status}", OTKAZ)
            if dannye.get("ok"):
                return dannye.get("result") or {}
            kod = dannye.get("error_code")
            zhdat = (dannye.get("parameters") or {}).get("retry_after")
            if kod == 429 and isinstance(zhdat, (int, float)) and ozhidaniy < POPYTOK_429:
                ozhidaniy += 1
                self.schetchiki["ozhidaniy_429"] += 1
                pauza(zhdat)
                continue
            raise OshibkaTelegram(f"{metod}: {kod} {dannye.get('description', '')}".strip(), OTKAZ)

    def __call__(self, metod: str, parametry: dict) -> dict:
        try:
            return self._otpravit(metod, parametry)
        except OshibkaTelegram as e:
            tekst, vid = self._skryt(str(e)), e.vid
        except Exception as e:
            self.zakryt()
            tekst, vid = self._skryt(f"{metod}: {type(e).__name__}: {e}"), OTKAZ
        raise OshibkaTelegram(tekst, vid) from None


def bot_api(token: str, soedinenie: Callable[[], http.client.HTTPConnection] | None = None,
            pauza: Callable[[float], None] | None = None) -> BotApi:
    """Отправитель Bot API с одним соединением; фабрика соединения и пауза — для теста [техника]."""
    return BotApi(token, soedinenie, pauza)


def _otpravitel_po_tokenu() -> BotApi:
    return bot_api(tokenbota.zagruzit_token())


def _zakryt(otpravitel) -> None:
    """Закрыть соединение отправителя в конце команды, если оно у него есть."""
    zakryt = getattr(otpravitel, "zakryt", None)
    if callable(zakryt):
        zakryt()


def _pechat_zaprosov(zaprosy: list[dict]) -> None:
    """Напечатать запросы к Bot API вместо отправки; токен не нужен."""
    for n, z in enumerate(zaprosy, 1):
        print(f"--- запрос {n} из {len(zaprosy)}: sendMessage")
        print(json.dumps(z, ensure_ascii=False, indent=2))


def _do_zapasa(kandidat: dict, granica: datetime) -> bool:
    """Дата кандидата раньше начала запаса; неразбираемая дата — нет (придёт снова, не потеряется)."""
    try:
        return iz_stroki(kandidat.get("data")) < granica
    except (TypeError, ValueError, AttributeError):
        return False


def _idy_progona(dannye: dict, konec_perioda: datetime) -> list[str]:
    """Что запомнить увиденным: кандидаты с novyy (нет признака — новый) с датой раньше начала
    запаса, кроме issue GitHub, и tolko_v_vidennye. Кандидат внутри запаса агент мог не видеть — он
    придёт снова. Issue GitHub — только вышедшим пунктом (zapisat_punkt): агент мог его не видеть
    (файл issue собран раньше), тогда он выйдет следующим выпуском."""
    granica = sostoyanie.nachalo_zapasa(konec_perioda)
    idy = [k.get("id") for k in dannye.get("kandidaty") or []
           if isinstance(k, dict) and k.get("novyy", True) is not False and _do_zapasa(k, granica)
           and k.get("skoro") is not True and not sostoyanie.eto_issue_github(str(k.get("id") or ""))]
    idy += list(dannye.get("tolko_v_vidennye") or [])
    return [i for i in idy if isinstance(i, str) and i]


def opublikovat(suhoj: bool = False, otpravitel: Otpravitel | None = None) -> int:
    """Команда `opublikovat [--suhoj]`; вернуть код выхода.

    Есть файл сбоя — строка сбоя и posledniy_slot, период не сдвигается. Иначе файл выпуска: всё
    проверяется до первой отправки; отправленный пункт сразу пишется в состояние; период
    сдвигается только после всего выпуска. Обрыв отправки — строка сбоя, код 1. --suhoj печатает
    запросы и состояние не трогает. В режиме проверки (Р72) posledniy_slot не пишется, остальное
    состояние — как всегда.
    """
    put_sboya, put_vypuska = nastrojki.fayl_sboya(), nastrojki.fayl_vypuska()
    if put_sboya.exists():
        if put_vypuska.exists():
            return _otkaz("есть и файл выпуска, и файл сбоя — неясно, что публиковать")
        return _opublikovat_sboj(put_sboya, suhoj, otpravitel)

    try:
        dannye_progona = progon.prochitat()
    except (OSError, ValueError) as e:
        return _otkaz(f"файл прогона не читается: {e}")
    if not isinstance(dannye_progona, dict):
        return _otkaz("файл прогона — не объект JSON")
    try:
        vypusk = json.loads(Path(put_vypuska).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return _otkaz(f"файл выпуска не читается: {e}")
    try:
        soobshch = soobshcheniya(vypusk)
        pustoy = not soobshch
        soobshch.append(soobshchenie_sbora(dannye_progona, soobshch))
    except OtkazVypuska as e:
        return _otkaz(str(e))

    if suhoj:
        _pechat_zaprosov([zapros(s) for s in soobshch])
        if pustoy:
            print("выпуск пустой — только служебная строка сбора")
        return 0

    try:
        nachalo_progona = iz_stroki(dannye_progona["nachalo_progona"])
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return _otkaz(f"в файле прогона нет nachalo_progona: {e}")
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        return _otkaz(str(e))
    svoy = otpravitel is None
    if svoy:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            return _otkaz(str(e))

    rezhim = nastrojki.rezhim_proverki()
    slot_zayavki = None if rezhim else slot(seychas())
    if rezhim:
        print("newsai opublikovat: режим проверки — слот не отмечается", file=sys.stderr)
    try:
        for n, s in enumerate(soobshch, 1):
            try:
                otpravitel("sendMessage", zapros(s))
            except OshibkaTelegram as e:
                print(f"newsai opublikovat: отправлено {n - 1} из {len(soobshch)}, ошибка: {e}",
                      file=sys.stderr)
                if slot_zayavki is not None:
                    sostoyanie.zapisat_slot(sost, slot_zayavki)
                if e.vid == OBRYV:
                    prichina = OBRYV_CHAST if n > 1 else OBRYV_NICHEGO
                else:
                    prichina = OTKAZ_CHAST if n > 1 else OTKAZ_NICHEGO
                _poslat_sboj(otpravitel, prichina, "opublikovat")
                return 1
            for p in s["punkty"]:
                sostoyanie.zapisat_punkt(sost, p["zagolovok"], p["url"], seychas(), idy=p["idy"],
                                         vid=p["vid"], oboroty=p["oboroty"])
    finally:
        if svoy:
            _zakryt(otpravitel)

    sostoyanie.zavershit_progon(sost, nachalo_progona, _idy_progona(dannye_progona, nachalo_progona), seychas(),
                                slot=slot_zayavki,
                                github_obhod=dannye_progona.get("github_polnyy_obhod") is True)
    print("выпуск пустой — отправлена служебная строка сбора" if pustoy
          else f"отправлено сообщений: {len(soobshch)}")
    return 0


def _opublikovat_sboj(put: Path, suhoj: bool, otpravitel: Otpravitel | None) -> int:
    """Файл сбоя → строка сбоя в канал и posledniy_slot (в режиме проверки — без него); период не
    сдвигается."""
    try:
        prichina = prichina_iz_fayla_sboya(put)
    except OtkazVypuska as e:
        return _otkaz(str(e))
    print(f"newsai opublikovat: файл сбоя, причина: {prichina}", file=sys.stderr)
    parametry = zapros_sboya(prichina)
    if suhoj:
        _pechat_zaprosov([parametry])
        return 0
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        return _otkaz(str(e))
    svoy = otpravitel is None
    if svoy:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            return _otkaz(str(e))
    kod = 0
    try:
        otpravitel("sendMessage", parametry)
    except OshibkaTelegram as e:
        print(f"newsai opublikovat: строка сбоя не отправлена: {e}", file=sys.stderr)
        kod = 1
    finally:
        if svoy:
            _zakryt(otpravitel)
    if nastrojki.rezhim_proverki():
        print("newsai opublikovat: режим проверки — слот не отмечается", file=sys.stderr)
    else:
        sostoyanie.zapisat_slot(sost, slot(seychas()))
    if not kod:
        print("отправлена строка сбоя")
    return kod


def prichina_iz_fayla_sboya(put: Path) -> str:
    """Файл сбоя {"prichina": "<фраза набора>"} → фраза из PRICHINY_FAYLA_SBOYA; негодный — OtkazVypuska
    без текста файла в сообщении."""
    try:
        dannye = json.loads(Path(put).read_text(encoding="utf-8"))
    except OSError as e:
        raise OtkazVypuska(f"файл сбоя не читается: {e}") from None
    except ValueError as e:
        raise OtkazVypuska(f"файл сбоя не читается: {type(e).__name__}") from None
    if not isinstance(dannye, dict) or set(dannye) != {"prichina"}:
        raise OtkazVypuska("файл сбоя: ждём объект с одним полем prichina")
    prichina = dannye["prichina"]
    for fraza in PRICHINY_FAYLA_SBOYA:
        if isinstance(prichina, str) and prichina.strip() == fraza:
            return fraza
    raise OtkazVypuska("файл сбоя: причина не из набора причин публикатора")


def _otkaz(prichina: str) -> int:
    print(f"newsai opublikovat: отказ, ничего не отправлено: {prichina}", file=sys.stderr)
    return 1


def slova_sboya(prichina: str) -> str:
    """Причина из набора (техника) → слова для канала по таблице строк сбоя (Р66). Сравнение после
    схлопывания пробелов: по точной фразе, у «заявка не прошла проверку:» — по началу; названия
    проверок в канал не идут. Неизвестная причина — «не собран» [техника]."""
    prichina = " ".join(prichina.split())
    if prichina in SLOVA_SBOYA:
        return SLOVA_SBOYA[prichina]
    if prichina.startswith(ZAYAVKA_NE_PROSHLA):
        return SLOVA_ZAYAVKA_NE_PROSHLA
    return NE_SOBRAN


def tekst_sboya(prichina: str, kogda=None) -> str:
    """`⚠️ Выпуск <слот> <слова по таблице>`, обрезано до лимита Telegram.

    В режиме проверки (Р72) — `⚠️ Тестовый выпуск <ЧЧ:ММ> …`: время самого момента kogda (открытие
    заявки или часы запуска) по Бангкоку, а не слот расписания."""
    kogda = kogda or seychas()
    if nastrojki.rezhim_proverki():
        nachalo = f"⚠️ Тестовый выпуск {mestnoe_vremya(kogda)}"
    else:
        nachalo = f"⚠️ Выпуск {chas_slota(kogda)}"
    tekst = f"{nachalo} {slova_sboya(prichina)}"
    while dlina(tekst) > nastrojki.LIMIT_SOOBSHCHENIYA:
        tekst = tekst[:-1]
    return tekst


def zapros_sboya(prichina: str, kogda=None) -> dict:
    """Параметры sendMessage строки сбоя: обычный текст, без parse_mode (Р29)."""
    return {"chat_id": nastrojki.KANAL_ID, "text": tekst_sboya(prichina, kogda)}


def _poslat_sboj(otpravitel: Otpravitel, prichina: str, komanda: str, kogda: datetime | None = None) -> int:
    print(f"newsai {komanda}: причина строки сбоя: {prichina}", file=sys.stderr)
    try:
        otpravitel("sendMessage", zapros_sboya(prichina, kogda))
    except OshibkaTelegram as e:
        print(f"newsai {komanda}: строка сбоя не отправлена: {e}", file=sys.stderr)
        return 1
    return 0


def prichina_iz_fayla(put: Path) -> str:
    """`sboj --prichina-iz <файл>`: первая строка файла, без управляющих символов, до 200 знаков."""
    with open(put, encoding="utf-8", errors="replace") as f:
        stroka = f.readline(64 * 1024)
    stroka = " ".join(_UPRAVLYAYUSHCHIE.sub(" ", stroka).split())
    return stroka[: nastrojki.PRICHINA_SBOYA_ZNAKOV]


def sboj(prichina: str, otpravitel: Otpravitel | None = None, kogda: datetime | None = None) -> int:
    """Строка сбоя `⚠️ Выпуск <слот> …` — перевод причины (slova_sboya); причина — в stderr; вернуть
    код выхода. Слот — момента kogda (у сторожа — начало судимого слота), по умолчанию — часов."""
    if not prichina.strip():
        print("newsai sboj: пустая причина", file=sys.stderr)
        return 2
    if otpravitel is not None:
        return _poslat_sboj(otpravitel, prichina, "sboj", kogda)
    try:
        otpravitel = _otpravitel_po_tokenu()
    except tokenbota.NetTokena as e:
        print(f"newsai sboj: {e}", file=sys.stderr)
        return 1
    try:
        return _poslat_sboj(otpravitel, prichina, "sboj", kogda)
    finally:
        _zakryt(otpravitel)


def otkrytie_zayavki(put_sobytiya: Path) -> datetime | None:
    """Момент открытия заявки (pull_request.created_at) из файла события; нет или не читается — None."""
    try:
        sobytie = json.loads(Path(put_sobytiya).read_text(encoding="utf-8"))
        return iz_stroki(sobytie["pull_request"]["created_at"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def sboj_iz_fayla(put: Path, otpravitel: Otpravitel | None = None, sobytie: Path | None = None) -> int:
    """Команда `sboj --prichina-iz <файл> [--slot-iz-zayavki <event.json>]`.

    Причина — не аргумент shell, а первая строка файла. С событием заявки — слот её открытия идёт в
    строку и в posledniy_slot состояния, posledniy_sbor не трогается: вторая заявка того же слота
    закроется молча. Событие без времени открытия — строка уходит, слот не пишется [техника]. В режиме
    проверки (Р72) в строке — «Тестовый выпуск» со временем открытия заявки, состояние не читается и
    не пишется [техника].
    """
    try:
        prichina = prichina_iz_fayla(put)
    except OSError as e:
        print(f"newsai sboj: файл причины не читается: {e}", file=sys.stderr)
        return 2
    if sobytie is None:
        return sboj(prichina, otpravitel)
    otkryta = otkrytie_zayavki(sobytie)
    if otkryta is None:
        print("newsai sboj: в событии заявки нет времени открытия — слот не записан", file=sys.stderr)
        return sboj(prichina, otpravitel)
    if not prichina.strip():
        print("newsai sboj: пустая причина", file=sys.stderr)
        return 2
    rezhim = nastrojki.rezhim_proverki()
    sost = None
    if rezhim:
        print("newsai sboj: режим проверки — слот не записан", file=sys.stderr)
    else:
        try:
            sost = sostoyanie.zagruzit()
        except sostoyanie.OshibkaSostoyaniya as e:
            print(f"newsai sboj: {e} — слот не записан", file=sys.stderr)
    svoy = otpravitel is None
    if svoy:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            print(f"newsai sboj: {e}", file=sys.stderr)
            return 1
    print(f"newsai sboj: причина строки сбоя: {prichina}", file=sys.stderr)
    try:
        otpravitel("sendMessage", zapros_sboya(prichina, otkryta))
        kod = 0
    except OshibkaTelegram as e:
        print(f"newsai sboj: строка сбоя не отправлена: {e}", file=sys.stderr)
        kod = 1
    finally:
        if svoy:
            _zakryt(otpravitel)
    if rezhim:
        return kod
    if sost is None:
        return 1
    sostoyanie.zapisat_slot(sost, slot(otkryta))
    return kod


def nesohraneno(otpravitel: Otpravitel | None = None) -> int:
    """Команда `nesohraneno`: строка Р40 отдельным сообщением, обычным текстом."""
    svoy = otpravitel is None
    if svoy:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            print(f"newsai nesohraneno: {e}", file=sys.stderr)
            return 1
    try:
        otpravitel("sendMessage", {"chat_id": nastrojki.KANAL_ID, "text": NESOHRANENO})
    except OshibkaTelegram as e:
        print(f"newsai nesohraneno: {e}", file=sys.stderr)
        return 1
    finally:
        if svoy:
            _zakryt(otpravitel)
    return 0


def zayavka_k_slotu(zayavki: list, nachalo_slota: datetime) -> bool:
    """Есть ли заявка агента — ветка claude/ этого же репозитория, открыта в окне слота.

    zayavki — ответ REST GitHub `pulls?state=all`: заявка могла быть уже закрыта публикатором.
    Заявка после окна сторожа не заглушает: её публикатор закроет молча (проверка «слот»).
    """
    for z in zayavki:
        if not isinstance(z, dict):
            continue
        golova, baza = z.get("head") or {}, z.get("base") or {}
        if not str(golova.get("ref") or "").startswith("claude/"):
            continue
        svoy = (baza.get("repo") or {}).get("full_name")
        if not svoy or (golova.get("repo") or {}).get("full_name") != svoy:
            continue
        try:
            sozdana = iz_stroki(str(z.get("created_at") or ""))
        except ValueError:
            continue
        if v_okne_slota(sozdana, nachalo_slota):
            return True
    return False


def storozh(put_zayavok: Path, otpravitel: Otpravitel | None = None) -> int:
    """Команда `storozh --zayavki <файл>`: слот не обработан и заявки к нему нет — строка сбоя (Р29)
    с причиной ZAYAVKA_NE_PRISHLA, в канале — её перевод (Р66).

    Судит последний слот, чьё окно закрылось к запуску (sudimyy_slot, Р70): расписание GitHub
    опаздывает, и запуск в открытом окне иначе судил бы слот, к которому агент ещё не успел. Час в
    строке сбоя — этого слота. В режиме проверки (Р72) — тишина: состояние и заявки не читаются, код 0.
    Файл — список заявок из REST GitHub; его достаёт workflow сторожа, токен GitHub сюда не идёт.
    """
    if nastrojki.rezhim_proverki():
        print("режим проверки — сторож молчит")
        return 0
    tekushchiy = sudimyy_slot(seychas())
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        print(f"newsai storozh: состояние не читается ({e}) — смотрю только заявки", file=sys.stderr)
        sost = {}
    if sost.get("posledniy_slot") == tekushchiy:
        print(f"слот {tekushchiy} обработан публикатором")
        return 0
    try:
        zayavki = json.loads(Path(put_zayavok).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"newsai storozh: список заявок не читается: {e}", file=sys.stderr)
        return 2
    if not isinstance(zayavki, list):
        print("newsai storozh: список заявок — не список JSON", file=sys.stderr)
        return 2
    if zayavka_k_slotu(zayavki, iz_stroki(tekushchiy)):
        print(f"заявка к слоту {tekushchiy} есть — её обрабатывает публикатор")
        return 0
    return sboj(ZAYAVKA_NE_PRISHLA, otpravitel, iz_stroki(tekushchiy))
