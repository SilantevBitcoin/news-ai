"""Публикация выпуска в Telegram и сообщения о сбое (спека «Сообщения Telegram»).

Читает файл выпуска или файл сбоя (nastrojki.fayl_vypuska / fayl_sboya) и файл прогона
(progon.prochitat); токен — tokenbota.zagruzit_token; канал — nastrojki.KANAL_ID. После каждого
отправленного сообщения — sostoyanie.zapisat_punkt, в конце — sostoyanie.zavershit_progon.

У публикатора файл прогона — его собственный сбор (`sbor --konec-iz-zayavki`), файл выпуска —
очищенный `proverit --vyhod`: «не ответили», конец периода и увиденное — из сбора публикатора.
Обрыв отправки — одна строка сбоя, её шлёт сам opublikovat; вызывающий второй не шлёт.

Файл выпуска (Р47–Р50):
    {
      "krupnye": [{"zagolovok", "abzacy": [5–8 строк], "url", "oboroty": bool, "idy": [...]}],   # до 3
      "sayty":   [{"zagolovok", "url", "istochnik", "zvezda": bool, "idy": [...]}],             # до 10
      "x":       [{"zagolovok", "url", "akkaunt", "zvezda": bool, "idy": [...]}],               # до 10
      "dengi":   [{"zagolovok", "url", "istochnik", "zvezda": bool, "idy": [...]}],             # до 5
      "github":  [{"zagolovok", "url", "idy": [...]}]
    }
Файл сбоя: {"prichina": "<одна из PRICHINY_FAYLA_SBOYA>"}.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import nastrojki, progon, sostoyanie, tokenbota
from .vremya import iz_stroki, seychas, v_stroku
from .vremya import slot as chas_slota

API = "https://api.telegram.org"
POPYTOK_429 = 5
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

RAZDELY = (
    ("krupnye", 3, ("zagolovok", "abzacy", "url", "oboroty", "idy")),
    ("sayty", 10, ("zagolovok", "url", "istochnik", "zvezda", "idy")),
    ("x", 10, ("zagolovok", "url", "akkaunt", "zvezda", "idy")),
    ("dengi", 5, ("zagolovok", "url", "istochnik", "zvezda", "idy")),
    ("github", None, ("zagolovok", "url", "idy")),
)
NAZVANIYA = {"krupnye": "крупная новость", "sayty": "список сайтов", "x": "список X",
             "dengi": "список денег и сделок", "github": "список GitHub", "ne_otvetili": "не ответили"}
VID_PUNKTA = {"krupnye": "krupnaya", "sayty": "sayty", "x": "x", "dengi": "dengi", "github": "github"}

_UPRAVLYAYUSHCHIE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_AKKAUNT = re.compile(r"^[A-Za-z0-9_]{1,15}$")

Otpravitel = Callable[[str, dict], dict]


class OtkazVypuska(Exception):
    """Выпуск не годится к отправке — ничего не отправлено."""


class OshibkaTelegram(Exception):
    """Bot API вернул ошибку или не ответил."""


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
    p = {}
    for pole in polya:
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


def _stroka_spiska(razdel: str, n: int, p: dict) -> str:
    zvezda = "⭐ " if p.get("zvezda") else ""
    if razdel == "x":
        return f"{n}. {zvezda}@{p['akkaunt']}: {_ssylka(p)}"
    if razdel == "github":
        return f"{n}. {_ssylka(p)} ({_ekranirovat_tekst(IMYA_GITHUB)}) · скоро"
    return f"{n}. {zvezda}{_ssylka(p)} ({_ekranirovat_tekst(p['istochnik'])})"


def _spisok(razdel: str, punkty: list[dict]) -> dict:
    stroki = [_stroka_spiska(razdel, n, p) for n, p in enumerate(punkty, 1)]
    return {"vid": razdel, "tekst": "\n".join(stroki), "prevyu": None,
            "punkty": [_zapis(razdel, p) for p in punkty]}


def soobshcheniya(vypusk: dict, ne_otvetili: list[str]) -> list[dict]:
    """Файл выпуска → сообщения по порядку Р49: крупные, сайты, X, деньги, GitHub, «не ответили».

    Сообщение: vid (krupnye, sayty, x, dengi, github, ne_otvetili), tekst (HTML), prevyu (адрес
    превью или None — выключено), punkty (что записать в состояние после отправки). Пустой выпуск —
    пустой список. Негодный выпуск — OtkazVypuska.
    """
    if not isinstance(vypusk, dict):
        raise OtkazVypuska("файл выпуска — не объект JSON")
    lishnie = sorted(set(vypusk) - {r for r, _, _ in RAZDELY})
    if lishnie:
        raise OtkazVypuska(f"в файле выпуска лишние поля: {', '.join(lishnie)}")
    if not isinstance(ne_otvetili, (list, tuple)):
        raise OtkazVypuska("ne_otvetili — не список")

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

    vyhod = [_krupnaya(p) for p in razdely["krupnye"]]
    for razdel, _, _ in RAZDELY[1:]:
        if razdely[razdel]:
            vyhod.append(_spisok(razdel, razdely[razdel]))
    imena = list(dict.fromkeys(i.strip() for i in ne_otvetili if isinstance(i, str) and i.strip()))
    if imena:
        vyhod.append({"vid": "ne_otvetili", "tekst": _ekranirovat_tekst("не ответили: " + ", ".join(imena)),
                      "prevyu": None, "punkty": []})
    for n, s in enumerate(vyhod, 1):
        if dlina(s["tekst"]) > nastrojki.LIMIT_SOOBSHCHENIYA:
            raise OtkazVypuska(f"сообщение {n} ({NAZVANIYA[s['vid']]}) длиннее "
                               f"{nastrojki.LIMIT_SOOBSHCHENIYA} знаков: {dlina(s['tekst'])} — сократите текст")
    return vyhod


def zapros(soobshchenie: dict) -> dict:
    """Параметры sendMessage для сообщения. Звук не задаётся (Р27)."""
    parametry = {"chat_id": nastrojki.KANAL_ID, "text": soobshchenie["tekst"], "parse_mode": "HTML"}
    if soobshchenie["prevyu"]:
        parametry["link_preview_options"] = {"url": soobshchenie["prevyu"]}
    elif soobshchenie["vid"] != "ne_otvetili":
        parametry["link_preview_options"] = {"is_disabled": True}
    return parametry


def bot_api(token: str) -> Otpravitel:
    """Отправитель через Bot API: 429 — ждать retry_after и повторить; прочее — OshibkaTelegram.

    Любое исключение отправки превращается в OshibkaTelegram с текстом без токена.
    """

    def skryt(tekst: str) -> str:
        return tekst.replace(token, "***") if token else tekst

    def _otpravit(metod: str, parametry: dict) -> dict:
        telo = json.dumps(parametry, ensure_ascii=False).encode("utf-8")
        for popytka in range(POPYTOK_429 + 1):
            zapr = urllib.request.Request(f"{API}/bot{token}/{metod}", data=telo, method="POST",
                                          headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(zapr, timeout=nastrojki.TAYMAUT_SEKUND) as otvet:
                    dannye = json.loads(otvet.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                try:
                    dannye = json.loads(e.read().decode("utf-8"))
                except (ValueError, OSError):
                    raise OshibkaTelegram(f"{metod}: HTTP {e.code}") from None
                finally:
                    e.close()
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise OshibkaTelegram(f"{metod}: нет ответа Bot API: {e}") from None
            if dannye.get("ok"):
                return dannye.get("result") or {}
            kod = dannye.get("error_code")
            zhdat = (dannye.get("parameters") or {}).get("retry_after")
            if kod == 429 and isinstance(zhdat, (int, float)) and popytka < POPYTOK_429:
                time.sleep(zhdat)
                continue
            raise OshibkaTelegram(f"{metod}: {kod} {dannye.get('description', '')}".strip())
        raise OshibkaTelegram(f"{metod}: 429 после {POPYTOK_429} ожиданий")

    def otpravit(metod: str, parametry: dict) -> dict:
        try:
            return _otpravit(metod, parametry)
        except OshibkaTelegram as e:
            tekst = skryt(str(e))
        except Exception as e:
            tekst = skryt(f"{metod}: {type(e).__name__}: {e}")
        raise OshibkaTelegram(tekst) from None

    return otpravit


def _otpravitel_po_tokenu() -> Otpravitel:
    return bot_api(tokenbota.zagruzit_token())


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
    запаса и tolko_v_vidennye. Кандидат внутри запаса агент мог не видеть — он придёт снова."""
    granica = sostoyanie.nachalo_zapasa(konec_perioda)
    idy = [k.get("id") for k in dannye.get("kandidaty") or []
           if isinstance(k, dict) and k.get("novyy", True) is not False and _do_zapasa(k, granica)]
    idy += list(dannye.get("tolko_v_vidennye") or [])
    return [i for i in idy if isinstance(i, str) and i]


def opublikovat(suhoj: bool = False, otpravitel: Otpravitel | None = None) -> int:
    """Команда `opublikovat [--suhoj]`; вернуть код выхода.

    Есть файл сбоя — строка сбоя и posledniy_slot, период не сдвигается. Иначе файл выпуска: всё
    проверяется до первой отправки; отправленный пункт сразу пишется в состояние; период
    сдвигается только после всего выпуска. Обрыв отправки — строка сбоя, код 1. --suhoj печатает
    запросы и состояние не трогает.
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
        soobshch = soobshcheniya(vypusk, dannye_progona.get("ne_otvetili") or [])
    except OtkazVypuska as e:
        return _otkaz(str(e))

    if suhoj:
        _pechat_zaprosov([zapros(s) for s in soobshch])
        if not soobshch:
            print("выпуск пустой — запросов нет")
        return 0

    try:
        nachalo_progona = iz_stroki(dannye_progona["nachalo_progona"])
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return _otkaz(f"в файле прогона нет nachalo_progona: {e}")
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        return _otkaz(str(e))
    if soobshch and otpravitel is None:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            return _otkaz(str(e))

    slot_zayavki = slot(seychas())
    for n, s in enumerate(soobshch, 1):
        try:
            otpravitel("sendMessage", zapros(s))
        except OshibkaTelegram as e:
            print(f"newsai opublikovat: отправлено {n - 1} из {len(soobshch)}, ошибка: {e}",
                  file=sys.stderr)
            sostoyanie.zapisat_slot(sost, slot_zayavki)
            prichina = f"часть выпуска не отправлена: {e}" if n > 1 else f"выпуск не отправлен: {e}"
            _poslat_sboj(otpravitel, prichina, "opublikovat")
            return 1
        for p in s["punkty"]:
            sostoyanie.zapisat_punkt(sost, p["zagolovok"], p["url"], seychas(), idy=p["idy"],
                                     vid=p["vid"], oboroty=p["oboroty"])

    sostoyanie.zavershit_progon(sost, nachalo_progona, _idy_progona(dannye_progona, nachalo_progona), seychas(),
                                slot=slot_zayavki,
                                github_obhod=dannye_progona.get("github_polnyy_obhod") is True)
    print(f"отправлено сообщений: {len(soobshch)}" if soobshch else "выпуск пустой — ничего не отправлено")
    return 0


def _opublikovat_sboj(put: Path, suhoj: bool, otpravitel: Otpravitel | None) -> int:
    """Файл сбоя → строка сбоя в канал и posledniy_slot; период не сдвигается."""
    try:
        prichina = prichina_iz_fayla_sboya(put)
    except OtkazVypuska as e:
        return _otkaz(str(e))
    parametry = zapros_sboya(prichina)
    if suhoj:
        _pechat_zaprosov([parametry])
        return 0
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        return _otkaz(str(e))
    if otpravitel is None:
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


def tekst_sboya(prichina: str, kogda=None) -> str:
    """`выпуск <слот> не собран: <причина>`, обрезано до лимита Telegram."""
    tekst = f"выпуск {chas_slota(kogda or seychas())} не собран: {' '.join(prichina.split())}"
    while dlina(tekst) > nastrojki.LIMIT_SOOBSHCHENIYA:
        tekst = tekst[:-1]
    return tekst


def zapros_sboya(prichina: str, kogda=None) -> dict:
    """Параметры sendMessage строки сбоя: обычный текст, без parse_mode (Р29)."""
    return {"chat_id": nastrojki.KANAL_ID, "text": tekst_sboya(prichina, kogda)}


def _poslat_sboj(otpravitel: Otpravitel, prichina: str, komanda: str) -> int:
    try:
        otpravitel("sendMessage", zapros_sboya(prichina))
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


def sboj(prichina: str, otpravitel: Otpravitel | None = None) -> int:
    """Сообщение `выпуск <слот> не собран: <причина>`; вернуть код выхода."""
    if not prichina.strip():
        print("newsai sboj: пустая причина", file=sys.stderr)
        return 2
    if otpravitel is None:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            print(f"newsai sboj: {e}", file=sys.stderr)
            return 1
    return _poslat_sboj(otpravitel, prichina, "sboj")


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
    закроется молча. Событие без времени открытия — строка уходит, слот не пишется [техника].
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
    try:
        sost = sostoyanie.zagruzit()
    except sostoyanie.OshibkaSostoyaniya as e:
        sost = None
        print(f"newsai sboj: {e} — слот не записан", file=sys.stderr)
    if otpravitel is None:
        try:
            otpravitel = _otpravitel_po_tokenu()
        except tokenbota.NetTokena as e:
            print(f"newsai sboj: {e}", file=sys.stderr)
            return 1
    try:
        otpravitel("sendMessage", zapros_sboya(prichina, otkryta))
        kod = 0
    except OshibkaTelegram as e:
        print(f"newsai sboj: строка сбоя не отправлена: {e}", file=sys.stderr)
        kod = 1
    if sost is None:
        return 1
    sostoyanie.zapisat_slot(sost, slot(otkryta))
    return kod


def nesohraneno(otpravitel: Otpravitel | None = None) -> int:
    """Команда `nesohraneno`: строка Р40 отдельным сообщением, обычным текстом."""
    if otpravitel is None:
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
    """Команда `storozh --zayavki <файл>`: слот не обработан и заявки к нему нет — строка сбоя (Р29).

    Файл — список заявок из REST GitHub; его достаёт workflow сторожа, токен GitHub сюда не идёт.
    """
    tekushchiy = slot(seychas())
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
    return sboj(ZAYAVKA_NE_PRISHLA, otpravitel)
