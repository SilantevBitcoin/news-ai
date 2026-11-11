"""Команда `proverit` — проверка заявки агента (спека «Проверка заявки»).

Режим агента (без `--zayavka`): схема, длина, ссылки и свежесть файла выпуска или сбоя против своего
файла прогона и состояния — самопроверка до заявки.

Режим публикатора (`--zayavka <event.json> --fayly <список>`): файлу прогона из заявки публикатор не
верит ни в чём и не читает его. Две формы вызова:
- без `--progon` — заявка, слот, файлы, схема, длина, `oboroty` и свежесть по открытию заявки; так
  чужая заявка и заявка не своего слота отсекаются до сбора публикатора [техника];
- с `--progon <сбор публикатора> --vyhod <файл>` — то же и сверка со сбором публикатора: пункт, чьей
  ссылки нет в сборе или чей адрес уже выходил (кроме крупной с `oboroty`), отбрасывается, чужие
  `idy` убираются, очищенный выпуск — в `--vyhod`.

Вывод: нарушение — строка `<название>: <пояснение>`; отброшенное — `отброшено: <раздел>, пункт N:
<причина>`; в конце — `проверки не прошли: <названия>` или `проверка пройдена`. Названия и причины —
только из кода, пояснения — без управляющих символов, так что строка вывода не начинается с данных
заявки.

Код выхода: 0 — прошла; 1 — нарушения своей заявки; 2 — сбор публикатора не читается (не вина
заявки); 3 — чужая заявка, заявка вне окна своего слота или по обработанному слоту (закрыть молча, в
канал ничего): на слот — одна публикация или одна строка сбоя.

Список файлов заявки — по строке `<status>\t<имя>`, как отдаёт REST `pulls/{n}/files`.
"""
from __future__ import annotations

import functools
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import nastrojki, publikaciya, sostoyanie
from .vremya import iz_stroki, seychas as _seychas

NAZVANIYA = ("заявка", "слот", "файлы", "схема", "длина", "ссылки", "свежесть")
KOD_SBOR = 2
KOD_CHUZHAYA = 3
ZAKRYT_CHUZHUYU = "чужая заявка: закрыть без публикации"
ZAKRYT_NE_K_SLOTU = "заявка не к своему слоту: закрыть без публикации"
PREDEL_FAYLA = 2 * 1024 * 1024
SVEZHEST_CHASOV = 3
DOPUSK_BUDUSHCHEGO_MINUT = 10

PROGON, VYPUSK, SBOJ = "dannye/progon.json", "dannye/vypusk.json", "dannye/sboj.json"
STATUSY_FAYLA = ("added", "modified")

_VETKA = re.compile(r"^claude/[A-Za-z0-9._/-]{1,200}$")
_UPRAVLYAYUSHCHIE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_DLINA = re.compile(r"^сообщение \d+ \(.+\) длиннее ")


class OshibkaSbora(Exception):
    """Сбор публикатора не читается — это сбой публикатора, а не нарушение заявки."""


_SHEMA = re.compile(r"://")
_WWW = re.compile(r"www\.", re.IGNORECASE)
_TELEGRAM = re.compile(r"(?:t|telegram)\.me/|telegram\.dog/|(?<![^\W_])tg:", re.IGNORECASE)
_UPOMINANIE = re.compile(r"@\w")
_DOMEN = re.compile(r"[\w-]+(?:\.[\w-]+)+")
_TOCHKI = str.maketrans({"。": ".", "｡": ".", "．": "."})


@functools.lru_cache(maxsize=1)
def _zony() -> frozenset[str]:
    """Зоны верхнего уровня из списка IANA в коде, с IDN-зонами и в виде xn--, и буквами (рф, москва)."""
    zony = set(_ZONY_IANA.split())
    zony.update(z.encode("ascii").decode("idna") for z in list(zony) if z.startswith("xn--"))
    return frozenset(zony)


def _ipv4(metki: list[str]) -> bool:
    chisla = [m.isascii() and m.isdigit() and int(m) <= 255 for m in metki]
    return any(all(chisla[i:i + 4]) for i in range(len(chisla) - 3))


def adres_v_tekste(tekst: str) -> bool:
    """Есть ли в тексте адрес, который Telegram сделал бы ссылкой.

    Ловит схему `…://`, `www.`, `t.me/`, `telegram.me/`, `telegram.dog/`, `tg:`, домен `имя.зона` с
    зоной из списка IANA, адрес IPv4, `@` перед буквой, цифрой или `_`. `Next.js`, `GPT-5.5`, `v2.1`,
    `Claude 4.5` — не адрес. Перед поиском — NFKC и без невидимых символов формата [техника].
    """
    tekst = unicodedata.normalize("NFKC", str(tekst)).translate(_TOCHKI)
    tekst = "".join(c for c in tekst if unicodedata.category(c) != "Cf")
    if _SHEMA.search(tekst) or _WWW.search(tekst) or _TELEGRAM.search(tekst) or _UPOMINANIE.search(tekst):
        return True
    zony = _zony()
    for m in _DOMEN.finditer(tekst):
        metki = m.group().split(".")
        if any(metka.strip("-_").casefold() in zony for metka in metki[1:]) or _ipv4(metki):
            return True
    return False


def _imena_istochnikov() -> frozenset[str]:
    return frozenset(i["imya"] for i in _json(nastrojki.FAYL_ISTOCHNIKOV)["istochniki"])


def _adresa_v_tekste(p: dict) -> list[str]:
    """Причины из кода: где в тексте пункта адрес. Имя источника из настроек (claude.dev) — не адрес:
    это имя из основной ветки, а не текст агента [техника]."""
    prichiny = []
    if adres_v_tekste(p["zagolovok"]):
        prichiny.append("адрес или упоминание в заголовке")
    for i, abzac in enumerate(p.get("abzacy", []), 1):
        if adres_v_tekste(abzac):
            prichiny.append(f"адрес или упоминание в абзаце {i}")
    ist = p.get("istochnik")
    if ist is not None and ist.strip() not in _imena_istochnikov() and adres_v_tekste(ist):
        prichiny.append("адрес или упоминание в имени источника")
    return prichiny


def _istochnik_ne_tot(p: dict, po_adresu: list[dict]) -> bool:
    """У сайтов и денег istochnik — имя источника кандидата с адресом пункта, как есть."""
    return "istochnik" in p and p["istochnik"].strip() not in {k["istochnik"] for k in po_adresu}


def _chisto(tekst: str, predel: int = 300) -> str:
    """Пояснение для вывода: без управляющих символов и не длиннее предела."""
    tekst = " ".join(_UPRAVLYAYUSHCHIE.sub(" ", str(tekst)).split())
    return tekst if len(tekst) <= predel else tekst[: predel - 1] + "…"


class Narusheniya:
    def __init__(self):
        self.spisok: list[tuple[str, str]] = []

    def dobavit(self, nazvanie: str, poyasnenie: str) -> None:
        assert nazvanie in NAZVANIYA
        self.spisok.append((nazvanie, _chisto(poyasnenie)))

    def est(self, nazvanie: str | None = None) -> bool:
        return any(n == nazvanie or nazvanie is None for n, _ in self.spisok)

    def nazvaniya(self) -> list[str]:
        bylo = {n for n, _ in self.spisok}
        return [n for n in NAZVANIYA if n in bylo]


def _json(put: Path):
    return json.loads(Path(put).read_text(encoding="utf-8"))


def _pole(d, *klyuchi):
    """Вложенное поле события; нет — None."""
    for k in klyuchi:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def proverit_zayavku(sobytie, n: Narusheniya) -> datetime | None:
    """Событие pull_request_target → нарушения «заявка»; вернуть момент открытия заявки."""
    if not isinstance(sobytie, dict) or not isinstance(sobytie.get("pull_request"), dict):
        n.dobavit("заявка", "событие — не заявка на слияние")
        return None
    repo = _pole(sobytie, "repository", "full_name")
    if sobytie.get("action") != "opened":
        n.dobavit("заявка", "событие — не открытие заявки")
    if not repo or _pole(sobytie, "pull_request", "head", "repo", "full_name") != repo \
            or _pole(sobytie, "pull_request", "head", "repo", "fork") is not False:
        n.dobavit("заявка", "ветка заявки не из этого репозитория")
    vetka = _pole(sobytie, "pull_request", "head", "ref")
    if not isinstance(vetka, str) or not _VETKA.fullmatch(vetka) or ".." in vetka:
        n.dobavit("заявка", "имя ветки не claude/…")
    osnovnaya = _pole(sobytie, "repository", "default_branch")
    if not osnovnaya or _pole(sobytie, "pull_request", "base", "ref") != osnovnaya \
            or _pole(sobytie, "pull_request", "base", "repo", "full_name") != repo:
        n.dobavit("заявка", "база заявки — не основная ветка")
    vladelec = _pole(sobytie, "repository", "owner", "login")
    if not vladelec or _pole(sobytie, "pull_request", "user", "login") != vladelec:
        n.dobavit("заявка", "автор заявки — не владелец репозитория")
    try:
        return iz_stroki(_pole(sobytie, "pull_request", "created_at"))
    except (TypeError, ValueError, AttributeError):
        n.dobavit("заявка", "нет времени открытия заявки")
        return None


def proverit_slot(otkryta: datetime, sost: dict | None, n: Narusheniya) -> None:
    """Публикатор: заявка открыта в окне своего слота и слот ещё не обработан → иначе «слот».

    Состояние не читается — только окно; сам отказ по состоянию даст «свежесть» [техника].
    """
    if not publikaciya.v_okne_slota(otkryta):
        n.dobavit("слот", f"заявка открыта вне окна слота: от начала слота до плюс "
                          f"{publikaciya.OKNO_SLOTA_MINUT} минут")
    if sost is not None and sost.get("posledniy_slot") == publikaciya.slot(otkryta):
        n.dobavit("слот", "слот заявки уже обработан")


def proverit_fayly(spisok: str, lokalnye: dict[str, Path], n: Narusheniya) -> None:
    """Список файлов заявки и размеры извлечённых файлов → нарушения «файлы».

    Файл прогона заявке разрешён, но не обязателен: публикатор его не извлекает и не читает.
    """
    imena = set()
    for stroka in spisok.splitlines():
        if not stroka.strip():
            continue
        status, _, imya = stroka.partition("\t")
        if imya not in (PROGON, VYPUSK, SBOJ):
            n.dobavit("файлы", f"лишний файл в заявке: {imya}")
        elif status not in STATUSY_FAYLA:
            n.dobavit("файлы", f"{imya}: изменение вида «{status}», можно только добавить или изменить")
        else:
            imena.add(imya)
    if not imena:
        n.dobavit("файлы", "в заявке нет файлов данных")
    if VYPUSK in imena and SBOJ in imena:
        n.dobavit("файлы", "в заявке и файл выпуска, и файл сбоя")
    for imya, put in lokalnye.items():
        if put.exists() and put.stat().st_size > PREDEL_FAYLA:
            n.dobavit("файлы", f"{imya} больше 2 МБ")


def _shema_progona(dannye, n: Narusheniya) -> bool:
    """Файл прогона: только то, на что опираются проверка и публикация."""
    if not isinstance(dannye, dict):
        n.dobavit("схема", "файл прогона — не объект JSON")
        return False
    ok = True
    try:
        iz_stroki(dannye["nachalo_progona"])
    except (KeyError, TypeError, ValueError, AttributeError):
        n.dobavit("схема", "файл прогона: нет nachalo_progona")
        ok = False
    kandidaty = dannye.get("kandidaty")
    if not isinstance(kandidaty, list):
        n.dobavit("схема", "файл прогона: kandidaty — не список")
        ok = False
    else:
        for i, k in enumerate(kandidaty, 1):
            if not isinstance(k, dict) or not all(isinstance(k.get(p), str) for p in ("id", "url", "istochnik")) \
                    or not isinstance(k.get("skoro", False), bool) \
                    or not isinstance(k.get("akkaunt", ""), str):
                n.dobavit("схема", f"файл прогона: кандидат {i} не по виду")
                ok = False
    imena = {i["imya"] for i in _json(nastrojki.FAYL_ISTOCHNIKOV)["istochniki"]}
    ne_otvetili = dannye.get("ne_otvetili", [])
    if not isinstance(ne_otvetili, list) or not all(isinstance(i, str) and i in imena for i in ne_otvetili):
        n.dobavit("схема", "файл прогона: в ne_otvetili не только имена источников из настроек")
        ok = False
    tolko = dannye.get("tolko_v_vidennye", [])
    if not isinstance(tolko, list) or not all(isinstance(i, str) for i in tolko):
        n.dobavit("схема", "файл прогона: tolko_v_vidennye — не список строк")
        ok = False
    if not isinstance(dannye.get("github_polnyy_obhod", False), bool):
        n.dobavit("схема", "файл прогона: github_polnyy_obhod — не true/false")
        ok = False
    return ok


def _punkty(vypusk: dict):
    """(раздел, номер, «где» для вывода, пункт) по порядку Р49."""
    for razdel, _, _ in publikaciya.RAZDELY:
        for nomer, p in enumerate(vypusk.get(razdel, []), 1):
            yield razdel, nomer, f"{publikaciya.NAZVANIYA[razdel]}, пункт {nomer}", p


def _po_adresu(razdel: str, p: dict, kandidaty: list[dict]) -> list[dict]:
    """Кандидаты с адресом пункта: у строки X — посты X того же akkaunt, у GitHub — issue со skoro."""
    url = p["url"].strip()
    po_adresu = [k for k in kandidaty if k["url"] == url]
    if razdel == "x":
        return [k for k in po_adresu if k.get("akkaunt") == p["akkaunt"].strip()]
    if razdel == "github":
        return [k for k in po_adresu if k.get("skoro") is True]
    return po_adresu


def _uzhe_vyhodil(razdel: str, p: dict, sost: dict | None) -> bool:
    """Адрес пункта уже в opublikovannye; крупная с oboroty — не повтор (её проверяет _oboroty)."""
    if sost is None or (razdel == "krupnye" and p.get("oboroty") is True):
        return False
    url = p["url"].strip()
    return any(isinstance(o, dict) and o.get("url") == url for o in sost.get("opublikovannye") or [])


def _oboroty(p: dict, sost: dict | None, gde: str, n: Narusheniya) -> None:
    """oboroty — только у новости, которая выходила строкой без oboroty."""
    if p.get("oboroty") is True and sost is not None:
        url = p["url"].strip()
        stroki = [o for o in sost["opublikovannye"] if o.get("url") == url and o.get("vid") != "krupnaya"]
        if not stroki or any(o.get("oboroty") for o in stroki):
            n.dobavit("ссылки", f"{gde}: oboroty у новости, которая не выходила строкой без oboroty")


def proverit_ssylki(vypusk: dict, dannye_progona: dict, sost: dict | None, n: Narusheniya) -> None:
    """Агент: каждый адрес и idy выпуска — из кандидатов прогона; в тексте нет адреса; istochnik —
    кандидата с этим адресом; адрес ещё не выходил, кроме крупной с oboroty; oboroty — только у
    вышедшей строки."""
    kandidaty = dannye_progona["kandidaty"]
    idy = {k["id"] for k in kandidaty}
    for razdel, _, gde, p in _punkty(vypusk):
        for prichina in _adresa_v_tekste(p):
            n.dobavit("ссылки", f"{gde}: {prichina}")
        if not p["url"].strip().lower().startswith(("http://", "https://")):
            n.dobavit("ссылки", f"{gde}: адрес не http(s)")
            continue
        po_adresu = _po_adresu(razdel, p, kandidaty)
        if not po_adresu:
            if razdel == "x":
                n.dobavit("ссылки", f"{gde}: адреса нет среди постов X этого аккаунта")
            elif razdel == "github":
                n.dobavit("ссылки", f"{gde}: адреса нет среди issue со skoro")
            else:
                n.dobavit("ссылки", f"{gde}: адреса нет среди кандидатов прогона")
        elif _istochnik_ne_tot(p, po_adresu):
            n.dobavit("ссылки", f"{gde}: имя источника — не источник кандидата с этим адресом")
        if any(i.strip() not in idy for i in p["idy"]):
            n.dobavit("ссылки", f"{gde}: в idy не идентификатор кандидата прогона")
        if _uzhe_vyhodil(razdel, p, sost):
            n.dobavit("ссылки", f"{gde}: адрес уже выходил в канал")
        _oboroty(p, sost, gde, n)


def proverit_svezhest(dannye_progona: dict, sost: dict, moment: datetime, n: Narusheniya) -> None:
    """Агент: начало своего прогона позже posledniy_sbor и не старше 3 часов к проверке."""
    nachalo = iz_stroki(dannye_progona["nachalo_progona"])
    if sost.get("posledniy_sbor") and nachalo <= iz_stroki(sost["posledniy_sbor"]):
        n.dobavit("свежесть", "начало прогона не позже прошлого выпуска")
    if nachalo < moment - timedelta(hours=SVEZHEST_CHASOV):
        n.dobavit("свежесть", f"прогон старше {SVEZHEST_CHASOV} часов к открытию заявки")
    if nachalo > moment + timedelta(minutes=DOPUSK_BUDUSHCHEGO_MINUT):
        n.dobavit("свежесть", "начало прогона позже открытия заявки")


def proverit_svezhest_zayavki(otkryta: datetime, sost: dict, moment: datetime, n: Narusheniya) -> None:
    """Публикатор: заявка открыта позже posledniy_sbor + запас (позже открытия прошлой вышедшей) и не
    раньше чем за 3 часа до запуска публикатора — повтор той же заявки и старая заявка не проходят."""
    if sost.get("posledniy_sbor") and \
            otkryta <= iz_stroki(sost["posledniy_sbor"]) + timedelta(minutes=nastrojki.ZAPAS_MINUT):
        n.dobavit("свежесть", "заявка открыта не позже прошлого выпуска")
    if otkryta < moment - timedelta(hours=SVEZHEST_CHASOV):
        n.dobavit("свежесть", f"заявка открыта больше {SVEZHEST_CHASOV} часов назад")


def _vypusk(put_vypuska: Path, ne_otvetili: list, n: Narusheniya):
    """Схема и длина файла выпуска; вернуть выпуск или None."""
    try:
        vypusk = _json(put_vypuska)
    except (OSError, ValueError):
        n.dobavit("схема", "файл выпуска не читается")
        return None
    try:
        publikaciya.soobshcheniya(vypusk, ne_otvetili)
    except publikaciya.OtkazVypuska as e:
        n.dobavit("длина" if _DLINA.match(str(e)) else "схема", str(e))
        return None
    return vypusk


def _vypusk_i_progon(put_vypuska: Path, put_progona: Path, n: Narusheniya):
    """Агент: схема прогона, схема и длина выпуска; вернуть (выпуск, прогон) или None."""
    try:
        dannye_progona = _json(put_progona)
    except (OSError, ValueError):
        n.dobavit("схема", "файл прогона не читается")
        return None
    if not _shema_progona(dannye_progona, n):
        return None
    vypusk = _vypusk(put_vypuska, dannye_progona.get("ne_otvetili") or [], n)
    return (vypusk, dannye_progona) if vypusk is not None else None


def ochistit(vypusk: dict, sbor_dannye: dict, sost: dict | None) -> tuple[dict, list[str]]:
    """Выпуск → (очищенный выпуск, отброшенное строками «<раздел>, пункт N: <причина>»).

    Отбрасывается пункт, чьего адреса нет среди кандидатов сбора публикатора; пункт с адресом или
    упоминанием в заголовке, абзаце или имени источника; пункт сайтов или денег, чей istochnik — не имя
    источника кандидата сбора с этим адресом; пункт, чей адрес уже в opublikovannye состояния, кроме
    крупной с oboroty. Остальные — в прежнем порядке. Из idy уходят
    идентификаторы не из сбора; опустевший idy — идентификаторы кандидатов с адресом пункта [техника].
    Причины — только из кода: данных заявки в строках нет.
    """
    kandidaty = sbor_dannye["kandidaty"]
    idy_sbora = {k["id"] for k in kandidaty}
    prichiny = {"x": "адреса нет среди постов X этого аккаунта в сборе публикатора",
                "github": "адреса нет среди issue со skoro в сборе публикатора"}
    chistyy = {razdel: [] for razdel, _, _ in publikaciya.RAZDELY}
    otbrosheno = []
    for razdel, _, gde, p in _punkty(vypusk):
        po_adresu = _po_adresu(razdel, p, kandidaty)
        if not po_adresu:
            otbrosheno.append(f"{gde}: {prichiny.get(razdel, 'адреса нет среди кандидатов сбора публикатора')}")
            continue
        v_tekste = _adresa_v_tekste(p)
        if v_tekste:
            otbrosheno.append(f"{gde}: {v_tekste[0]}")
            continue
        if _istochnik_ne_tot(p, po_adresu):
            otbrosheno.append(f"{gde}: имя источника — не источник кандидата сбора публикатора с этим адресом")
            continue
        if _uzhe_vyhodil(razdel, p, sost):
            otbrosheno.append(f"{gde}: адрес уже выходил в канал")
            continue
        idy =[i for i in p["idy"] if i.strip() in idy_sbora]
        if len(idy) < len(p["idy"]):
            otbrosheno.append(f"{gde}: из idy убрано не из сбора публикатора: {len(p['idy']) - len(idy)}")
        chistyy[razdel].append({**p, "idy": idy or [k["id"] for k in po_adresu]})
    return chistyy, otbrosheno


def _sbor_publikatora(put: Path) -> dict:
    try:
        dannye = _json(put)
    except (OSError, ValueError) as e:
        raise OshibkaSbora(f"сбор публикатора не читается: {type(e).__name__}") from None
    n = Narusheniya()
    if not _shema_progona(dannye, n):
        raise OshibkaSbora(f"сбор публикатора не по виду: {n.spisok[0][1]}")
    return dannye


def proverit(vypusk: Path | None = None, sboj: Path | None = None, progon: Path | None = None,
             sostoyanie_put: Path | None = None, zayavka: Path | None = None, fayly: Path | None = None,
             seychas: datetime | None = None, vyhod: Path | None = None) -> int:
    """Команда `proverit`; печатает нарушения по строке и возвращает код выхода."""
    put_vypuska = Path(vypusk) if vypusk else nastrojki.fayl_vypuska()
    put_sboya = Path(sboj) if sboj else nastrojki.fayl_sboya()
    put_sost = Path(sostoyanie_put) if sostoyanie_put else nastrojki.fayl_sostoyaniya()
    moment = seychas or _seychas()
    if zayavka is not None:
        return _publikator(put_vypuska, put_sboya, put_sost, Path(zayavka), fayly,
                           Path(progon) if progon else None, moment, Path(vyhod) if vyhod else None)
    return _agent(put_vypuska, put_sboya, Path(progon) if progon else nastrojki.fayl_progona(), put_sost, moment)


def _vypusk_ili_sboj(put_vypuska: Path, put_sboya: Path, n: Narusheniya) -> str | None:
    """'vypusk', 'sboj' или None — нет ни того, ни другого, или есть оба."""
    est_vypusk, est_sboj = put_vypuska.exists(), put_sboya.exists()
    if est_vypusk and est_sboj:
        n.dobavit("схема", "есть и файл выпуска, и файл сбоя")
        return None
    if not est_vypusk and not est_sboj:
        n.dobavit("схема", "нет ни файла выпуска, ни файла сбоя")
        return None
    return "vypusk" if est_vypusk else "sboj"


def _prichina_sboya(put_sboya: Path, n: Narusheniya) -> None:
    try:
        publikaciya.prichina_iz_fayla_sboya(put_sboya)
    except publikaciya.OtkazVypuska as e:
        n.dobavit("схема", str(e))


def _agent(put_vypuska: Path, put_sboya: Path, put_progona: Path, put_sost: Path, moment: datetime) -> int:
    """Самопроверка агента против своего файла прогона."""
    n = Narusheniya()
    vid = _vypusk_ili_sboj(put_vypuska, put_sboya, n)
    if vid is None:
        return _itog(n)
    if vid == "sboj":
        _prichina_sboya(put_sboya, n)
        dannye_progona = None
        if put_progona.exists():
            try:
                dannye_progona = _json(put_progona)
            except (OSError, ValueError):
                n.dobavit("схема", "файл прогона не читается")
            if dannye_progona is not None and not _shema_progona(dannye_progona, n):
                dannye_progona = None
        vypusk_dannye = None
    else:
        rez = _vypusk_i_progon(put_vypuska, put_progona, n)
        vypusk_dannye, dannye_progona = rez if rez else (None, None)

    if dannye_progona is not None:
        try:
            sost = sostoyanie.zagruzit(put_sost)
        except sostoyanie.OshibkaSostoyaniya:
            n.dobavit("свежесть", "файл состояния не читается")
            sost = None
        if vypusk_dannye is not None:
            proverit_ssylki(vypusk_dannye, dannye_progona, sost, n)
        if sost is not None:
            proverit_svezhest(dannye_progona, sost, moment, n)
    return _itog(n)


def _publikator(put_vypuska: Path, put_sboya: Path, put_sost: Path, zayavka: Path, fayly,
                put_sbora: Path | None, moment: datetime, vyhod: Path | None) -> int:
    """Публикатор: заявка, слот и файлы, схема, длина, свежесть по открытию; с put_sbora — сверка со
    своим сбором и очищенный выпуск в vyhod. Файл прогона заявки не читается."""
    n = Narusheniya()
    try:
        sobytie = _json(zayavka)
    except (OSError, ValueError):
        sobytie = None
    otkryta = proverit_zayavku(sobytie, n)
    if n.est("заявка"):
        return _itog(n, zakryt=ZAKRYT_CHUZHUYU)
    try:
        sost = sostoyanie.zagruzit(put_sost)
    except sostoyanie.OshibkaSostoyaniya:
        sost, sost_ne_chitaetsya = None, True
    else:
        sost_ne_chitaetsya = False
    proverit_slot(otkryta, sost, n)
    if n.est("слот"):
        return _itog(n, zakryt=ZAKRYT_NE_K_SLOTU)
    try:
        spisok = Path(fayly).read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError):
        n.dobavit("файлы", "список файлов заявки не читается")
        spisok = ""
    proverit_fayly(spisok, {VYPUSK: put_vypuska, SBOJ: put_sboya}, n)
    if any(p.exists() and p.stat().st_size > PREDEL_FAYLA for p in (put_vypuska, put_sboya)):
        return _itog(n)

    vid = _vypusk_ili_sboj(put_vypuska, put_sboya, n)
    if vid is None:
        return _itog(n)
    if sost_ne_chitaetsya:
        n.dobavit("свежесть", "файл состояния не читается")
    vypusk = None
    if vid == "sboj":
        _prichina_sboya(put_sboya, n)
    else:
        vypusk = _vypusk(put_vypuska, [], n)
        if vypusk is not None:
            for _, _, gde, p in _punkty(vypusk):
                _oboroty(p, sost, gde, n)
    if sost is not None:
        proverit_svezhest_zayavki(otkryta, sost, moment, n)
    if n.spisok or vypusk is None or put_sbora is None:
        return _itog(n)

    sbor_dannye = _sbor_publikatora(put_sbora)
    chistyy, otbrosheno = ochistit(vypusk, sbor_dannye, sost)
    try:
        publikaciya.soobshcheniya(chistyy, sbor_dannye.get("ne_otvetili") or [])
    except publikaciya.OtkazVypuska as e:
        n.dobavit("длина" if _DLINA.match(str(e)) else "схема", str(e))
    if not n.spisok and vyhod is not None:
        vyhod.parent.mkdir(parents=True, exist_ok=True)
        vyhod.write_text(json.dumps(chistyy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                         newline="\n")
    return _itog(n, otbrosheno=otbrosheno)


def _itog(n: Narusheniya, zakryt: str | None = None, otbrosheno: list[str] = ()) -> int:
    """zakryt — строка «… закрыть без публикации»: код 3, в канал ничего."""
    for nazvanie, poyasnenie in n.spisok:
        print(f"{nazvanie}: {poyasnenie}")
    for stroka in otbrosheno:
        print(f"отброшено: {_chisto(stroka)}")
    if zakryt:
        print(zakryt)
        return KOD_CHUZHAYA
    if n.spisok:
        print("проверки не прошли: " + ", ".join(n.nazvaniya()))
        return 1
    print("проверка пройдена")
    return 0


def komanda(args) -> int:
    """Вызов из точки входа: любая неожиданная ошибка разбора — нарушение схемы, а не трассировка."""
    try:
        return proverit(args.vypusk, args.sboj, args.progon, args.sostoyanie, args.zayavka, args.fayly,
                        vyhod=args.vyhod)
    except OshibkaSbora as e:
        print(f"newsai proverit: {_chisto(e)}", file=sys.stderr)
        return KOD_SBOR
    except Exception as e:
        print(f"схема: проверка упала: {type(e).__name__}")
        print("проверки не прошли: схема")
        print(f"newsai proverit: {_chisto(e)}", file=sys.stderr)
        return 1


_ZONY_IANA = """
aaa aarp abb abbott abbvie abc able abogado abudhabi ac academy accenture accountant accountants aco actor ad
ads adult ae aeg aero aetna af afl africa ag agakhan agency ai aig airbus airforce airtel akdn al alibaba
alipay allfinanz allstate ally alsace alstom am amazon americanexpress americanfamily amex amfam amica
amsterdam analytics android anquan anz ao aol apartments app apple aq aquarelle ar arab aramco archi army arpa
art arte as asda asia associates at athleta attorney au auction audi audible audio auspost author auto autos
aw aws ax axa az azure ba baby baidu banamex band bank bar barcelona barclaycard barclays barefoot bargains
baseball basketball bauhaus bayern bb bbc bbt bbva bcg bcn bd be beats beauty beer berlin best bestbuy bet bf
bg bh bharti bi bible bid bike bing bingo bio biz bj black blackfriday blockbuster blog bloomberg blue bm bms
bmw bn bnpparibas bo boats boehringer bofa bom bond boo book booking bosch bostik boston bot boutique box br
bradesco bridgestone broadway broker brother brussels bs bt build builders business buy buzz bv bw by bz bzh
ca cab cafe cal call calvinklein cam camera camp canon capetown capital capitalone car caravan cards care
career careers cars casa case cash casino cat catering catholic cba cbn cbre cc cd center ceo cern cf cfa cfd
cg ch chanel channel charity chase chat cheap chintai christmas chrome church ci cipriani circle cisco citadel
citi citic city ck cl claims cleaning click clinic clinique clothing cloud club clubmed cm cn co coach codes
coffee college cologne com commbank community company compare computer comsec condos construction consulting
contact contractors cooking cool coop corsica country coupon coupons courses cpa cr credit creditcard
creditunion cricket crown crs cruise cruises cu cuisinella cv cw cx cy cymru cyou cz dad dance data date
dating datsun day dclk dds de deal dealer deals degree delivery dell deloitte delta democrat dental dentist
desi design dev dhl diamonds diet digital direct directory discount discover dish diy dj dk dm dnp do docs
doctor dog domains dot download drive dtv dubai dupont durban dvag dvr dz earth eat ec eco edeka edu education
ee eg email emerck energy engineer engineering enterprises epson equipment er ericsson erni es esq estate et
eu eurovision eus events exchange expert exposed express extraspace fage fail fairwinds faith family fan fans
farm farmers fashion fast fedex feedback ferrari ferrero fi fidelity fido film final finance financial fire
firestone firmdale fish fishing fit fitness fj fk flickr flights flir florist flowers fly fm fo foo food
football ford forex forsale forum foundation fox fr free fresenius frl frogans frontier ftr fujitsu fun fund
furniture futbol fyi ga gal gallery gallo gallup game games gap garden gay gb gbiz gd gdn ge gea gent genting
george gf gg ggee gh gi gift gifts gives giving gl glass gle global globo gm gmail gmbh gmo gmx gn godaddy
gold goldpoint golf goodyear goog google gop got gov gp gq gr grainger graphics gratis green gripe grocery
group gs gt gu gucci guge guide guitars guru gw gy hair hamburg hangout haus hbo hdfc hdfcbank health
healthcare help here hermes hiphop hisamitsu hitachi hiv hk hkt hm hn hockey holdings holiday homedepot
homegoods homes homesense honda horse hospital host hosting hot hotels hotmail house how hr hsbc ht hu hughes
hyatt hyundai ibm icbc ice icu id ie ieee ifm ikano il im imamat imdb immo immobilien in inc industries
infiniti info ing ink institute insurance insure int international intuit investments io ipiranga iq ir irish
is ismaili ist istanbul it itau itv jaguar java jcb je jeep jetzt jewelry jio jll jm jmp jnj jo jobs joburg
jot joy jp jpmorgan jprs juegos kaufen kddi ke kerryhotels kerryproperties kfh kg kh ki kia kids kim kindle
kitchen kiwi km kn koeln komatsu kosher kp kpmg kpn kr krd kred kuokgroup kw ky kyoto kz la lacaixa
lamborghini lamer land landrover lanxess lasalle lat latino latrobe law lawyer lb lc lds lease leclerc lefrak
legal lego lexus lgbt li lidl life lifeinsurance lifestyle lighting like lilly limited limo lincoln link live
living lk llc llp loan loans locker locus lol london lotte lotto love lpl lplfinancial lr ls lt ltd ltda lu
lundbeck luxe luxury lv ly ma madrid maif maison makeup man management mango map market marketing markets
marriott marshalls mattel mba mc mckinsey md me med media meet melbourne meme memorial men menu merck merckmsd
mg mh miami microsoft mil mini mint mit mitsubishi mk ml mlb mls mm mma mn mo mobi mobile moda moe moi mom
monash money monster mormon mortgage moscow moto motorcycles mov movie mp mq mr ms msd mt mtn mtr mu museum
music mv mw mx my mz na nab nagoya name navy nba nc ne nec net netbank netflix network neustar new news next
nextdirect nexus nf nfl ng ngo nhk ni nico nike nikon ninja nissan nissay nl no nokia norton now nowruz nowtv
np nr nra nrw ntt nu nyc nz obi observer office okinawa olayan olayangroup ollo om omega one ong onl online
ooo open oracle orange org organic origins osaka otsuka ott ovh pa page panasonic paris pars partners parts
party pay pccw pe pet pf pfizer pg ph pharmacy phd philips phone photo photography photos physio pics pictet
pictures pid pin ping pink pioneer pizza pk pl place play playstation plumbing plus pm pn pnc pohl poker
politie porn post pr praxi press prime pro prod productions prof progressive promo properties property
protection pru prudential ps pt pub pw pwc py qa qpon quebec quest racing radio re read realestate realtor
realty recipes red redumbrella rehab reise reisen reit reliance ren rent rentals repair report republican rest
restaurant review reviews rexroth rich richardli ricoh ril rio rip ro rocks rodeo rogers room rs rsvp ru rugby
ruhr run rw rwe ryukyu sa saarland safe safety sakura sale salon samsclub samsung sandvik sandvikcoromant
sanofi sap sarl sas save saxo sb sbi sbs sc scb schaeffler schmidt scholarships school schule schwarz science
scot sd se search seat secure security seek select sener services seven sew sex sexy sfr sg sh shangrila sharp
shell shia shiksha shoes shop shopping shouji show si silk sina singles site sj sk ski skin sky skype sl sling
sm smart smile sn sncf so soccer social softbank software sohu solar solutions song sony soy spa space sport
spot sr srl ss st stada staples star statebank statefarm stc stcgroup stockholm storage store stream studio
study style su sucks supplies supply support surf surgery suzuki sv swatch swiss sx sy sydney systems sz tab
taipei talk taobao target tatamotors tatar tattoo tax taxi tc tci td tdk team tech technology tel temasek
tennis teva tf tg th thd theater theatre tiaa tickets tienda tips tires tirol tj tjmaxx tjx tk tkmaxx tl tm
tmall tn to today tokyo tools top toray toshiba total tours town toyota toys tr trade trading training travel
travelers travelersinsurance trust trv tt tube tui tunes tushu tv tvs tw tz ua ubank ubs ug uk unicom
university uno uol ups us uy uz va vacations vana vanguard vc ve vegas ventures verisign versicherung vet vg
vi viajes video vig viking villas vin vip virgin visa vision viva vivo vlaanderen vn vodka volvo vote voting
voto voyage vu wales walmart walter wang wanggou watch watches weather weatherchannel web webcam weber website
wed wedding weibo weir wf whoswho wien wiki williamhill win windows wine winners wme woodside work works world
wow ws wtc wtf xbox xerox xihuan xin xn--11b4c3d xn--1ck2e1b xn--1qqw23a xn--2scrj9c xn--30rr7y xn--3bst00m
xn--3ds443g xn--3e0b707e xn--3hcrj9c xn--3pxu8k xn--42c2d9a xn--45br5cyl xn--45brj9c xn--45q11c xn--4dbrk0ce
xn--4gbrim xn--54b7fta0cc xn--55qw42g xn--55qx5d xn--5su34j936bgsg xn--5tzm5g xn--6frz82g xn--6qq986b3xl
xn--80adxhks xn--80ao21a xn--80aqecdr1a xn--80asehdb xn--80aswg xn--8y0a063a xn--90a3ac xn--90ae xn--90ais
xn--9dbq2a xn--9et52u xn--9krt00a xn--b4w605ferd xn--bck1b9a5dre4c xn--c1avg xn--c2br7g xn--cck2b3b
xn--cckwcxetd xn--cg4bki xn--clchc0ea0b2g2a9gcd xn--czr694b xn--czrs0t xn--czru2d xn--d1acj3b xn--d1alf
xn--e1a4c xn--eckvdtc9d xn--efvy88h xn--fct429k xn--fhbei xn--fiq228c5hs xn--fiq64b xn--fiqs8s xn--fiqz9s
xn--fjq720a xn--flw351e xn--fpcrj9c3d xn--fzc2c9e2c xn--fzys8d69uvgm xn--g2xx48c xn--gckr3f0f xn--gecrj9c
xn--gk3at1e xn--h2breg3eve xn--h2brj9c xn--h2brj9c8c xn--hxt814e xn--i1b6b1a6a2e xn--imr513n xn--io0a7i
xn--j1aef xn--j1amh xn--j6w193g xn--jlq480n2rg xn--jvr189m xn--kcrx77d1x4a xn--kprw13d xn--kpry57d xn--kput3i
xn--l1acc xn--lgbbat1ad8j xn--mgb9awbf xn--mgba3a3ejt xn--mgba3a4f16a xn--mgba7c0bbn0a xn--mgbaam7a8h
xn--mgbab2bd xn--mgbah1a3hjkrd xn--mgbai9azgqp6j xn--mgbayh7gpa xn--mgbbh1a xn--mgbbh1a71e xn--mgbc0a9azcg
xn--mgbca7dzdo xn--mgbcpq6gpa1a xn--mgberp4a5d4ar xn--mgbgu82a xn--mgbi4ecexp xn--mgbpl2fh xn--mgbt3dhd
xn--mgbtx2b xn--mgbx4cd0ab xn--mix891f xn--mk1bu44c xn--mxtq1m xn--ngbc5azd xn--ngbe9e0a xn--ngbrx xn--node
xn--nqv7f xn--nqv7fs00ema xn--nyqy26a xn--o3cw4h xn--ogbpf8fl xn--otu796d xn--p1acf xn--p1ai xn--pgbs0dh
xn--pssy2u xn--q7ce6a xn--q9jyb4c xn--qcka1pmc xn--qxa6a xn--qxam xn--rhqv96g xn--rovu88b xn--rvc1e0am3e
xn--s9brj9c xn--ses554g xn--t60b56a xn--tckwe xn--tiq49xqyj xn--unup4y xn--vermgensberater-ctb
xn--vermgensberatung-pwb xn--vhquv xn--vuq861b xn--w4r85el8fhu5dnra xn--w4rs40l xn--wgbh1c xn--wgbl6a
xn--xhq521b xn--xkc2al3hye2a xn--xkc2dl3a5ee0h xn--y9a3aq xn--yfro4i67o xn--ygbi2ammx xn--zfr164b xxx xyz
yachts yahoo yamaxun yandex ye yodobashi yoga yokohama you youtube yt yun za zappos zara zero zip zm zone
zuerich zw
"""
