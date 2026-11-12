"""Шаг 1 — сбор кандидатов из источников (спека «Источники»).

Читает istochniki.json, состояние (sostoyanie.zagruzit, period, videno), пишет файл прогона через
progon.zapisat. Состояние не меняет — это делает публикатор.

Агент зовёт `sbor` — конец периода по часам машины. Публикатор зовёт `sbor --konec-iz-zayavki
<event.json>` — конец периода равен открытию заявки, чтобы его сбор шёл за то же окно, что у агента.

Окно охвата — 24 часа до начала прогона, а если период длиннее — весь период (Р46). Сбор отдаёт всех
кандидатов окна: novyy — нет в vidennye; остальные идут только в счёт охвата.

Источник из нескольких лент (`dop_adresa`, Блог Google) — один источник: повтор адреса — один
кандидат, не ответившая лента — источник в не ответивших, записи остальных лент остаются. Лента без
дат (`filtr.daty_iz_karty`, Google Developers Blog) — дата записи из lastmod карты сайта.

Issue GitHub у агента: запрос к API не прошёл (из облака — 403 прокси) — issue из файла сборщика
issue, если он годен (gitkhab). Путь к файлу — параметр fayl_issue; его передаёт только команда `sbor`
агента, сбор публикатора файл не читает.

Шов для тестов: `sobrat(istochniki, sost, nachalo_progona, poluchit, fayl_issue=None)` — без файлов,
HTTP подменяется.
Кандидаты в памяти — progon.Kandidat, период выпуска и окно охвата — progon.Period.
"""
from __future__ import annotations

import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import gitkhab, iks, nastrojki, progon, razbor, sostoyanie
from .progon import Kandidat, Period
from .vremya import iz_stroki, seychas, v_stroku
from .zaprosy import OshibkaZaprosa, poluchit as poluchit_po_seti

POTOKOV = 8
OKNO_CHASOV = 24


class OshibkaIstochnika(Exception):
    """Источник не ответил, вернул ошибку или неразбираемый ответ."""


def zagruzit_istochniki(put: Path | None = None) -> list[dict]:
    put = Path(put) if put else nastrojki.FAYL_ISTOCHNIKOV
    return json.loads(put.read_text(encoding="utf-8"))["istochniki"]


def okno_ohvata(period: Period) -> Period:
    """24 часа до начала прогона, а если период длиннее — весь период (Р46)."""
    return Period(min(period.nachalo, period.konec - timedelta(hours=OKNO_CHASOV)), period.konec)


def v_okne(zapis: razbor.Zapis, okno: Period) -> bool:
    """Точное время — в [начало, конец). День без времени — день не раньше дня до начала:
    такие записи датируются полуночью и днём сайта, а повтор отмечает novyy."""
    if zapis.data is None:
        return False
    if zapis.tolko_data:
        return (okno.nachalo - timedelta(days=1)).date() <= zapis.data.date() <= okno.konec.date()
    return zapis.data in okno


def _tekst_otveta(otvet, imya: str) -> str:
    if otvet.status != 200:
        raise OshibkaIstochnika(f"{imya}: ответ {otvet.status}")
    return otvet.tekst()


def _filtr(istochnik: dict, z: razbor.Zapis) -> razbor.Zapis | None:
    """Фильтры шума из istochniki.json; None — запись не кандидат."""
    f = istochnik.get("filtr") or {}
    if f.get("kategorii") and not set(f["kategorii"]) & set(z.kategorii):
        return None
    if f.get("bez_zagolovka_na") and z.zagolovok.startswith(f["bez_zagolovka_na"]):
        return None
    if f.get("zagolovok_na") and not z.zagolovok.startswith(f["zagolovok_na"]):
        return None
    if f.get("otbrosit_stroki_s"):
        ostatok = razbor.bez_strok(z.html, f["otbrosit_stroki_s"])
        if not ostatok.strip() and f.get("pustoy_ne_kandidat", True):
            return None
        z.tekst = ostatok
    if f.get("original_v_tekst"):
        for m in re.finditer(r'href="(https?://[^"]+)"', z.html, re.I):
            if "techmeme.com" not in m.group(1).lower():
                z.tekst = f"Статья: {html.unescape(m.group(1))}\n{z.tekst}"
                break
    return z


def _odin_adres(istochnik: dict, adres: str, imya: str, poluchit) -> list[razbor.Zapis]:
    vid = istochnik["vid"]
    tekst = _tekst_otveta(poluchit(adres), imya)
    try:
        if vid in ("rss", "atom"):
            return razbor.lenta(tekst)
        if vid == "stranica_statey":
            return razbor.stranica_statey(tekst, adres)
        if vid == "stranica":
            return razbor.stranica_changelog(tekst, adres)
    except razbor.OshibkaRazbora as e:
        raise OshibkaIstochnika(f"{imya}: {e}") from e
    raise OshibkaIstochnika(f"{imya}: неизвестный вид источника {vid}")


def _bez_slesha(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _daty_iz_karty(istochnik: dict, zapisi: list[razbor.Zapis], poluchit) -> None:
    """Дата записи — lastmod её адреса в карте сайта, день без времени (Google Developers Blog).

    Записи нет в карте — даты нет, запись не кандидат; карта не ответила или ни у одной записи нет
    даты — источник не ответил."""
    imya = istochnik["imya"]
    tekst = _tekst_otveta(poluchit(istochnik["filtr"]["daty_iz_karty"]), f"{imya}: карта сайта")
    try:
        karta = {_bez_slesha(a): d for a, d in razbor.karta_sayta(tekst).items()}
    except razbor.OshibkaRazbora as e:
        raise OshibkaIstochnika(f"{imya}: карта сайта: {e}") from e
    for z in zapisi:
        z.data = karta.get(_bez_slesha(z.url))
        z.tolko_data = True
    if zapisi and all(z.data is None for z in zapisi):
        raise OshibkaIstochnika(f"{imya}: ни у одной записи ленты нет даты в карте сайта")


def _zapisi(istochnik: dict, poluchit) -> tuple[list[razbor.Zapis], list[str]]:
    """Записи всех адресов источника (adres, затем dop_adresa) и ошибки адресов, что не ответили.

    Несколько лент — один источник (Блог Google): не ответившая лента — ошибка источника, записи
    остальных остаются (Р29). В ошибке адрес ленты — только когда лент несколько [техника]."""
    imya = istochnik["imya"]
    adresa = [istochnik["adres"], *istochnik.get("dop_adresa", [])]
    neskolko = len(adresa) > 1
    zapisi, oshibki = [], []
    for adres in adresa:
        metka = f"{imya}: {adres}" if neskolko else imya
        try:
            zapisi.extend(_odin_adres(istochnik, adres, metka, poluchit))
        except (OshibkaIstochnika, OshibkaZaprosa) as e:
            s = str(e)
            if neskolko and not s.startswith(imya):
                s = f"{imya}: {s}" if adres in s else f"{metka}: {s}"
            oshibki.append(s)
        except Exception as e:
            if not neskolko:
                raise
            oshibki.append(f"{metka}: {type(e).__name__}: {e}")
    if not oshibki and (istochnik.get("filtr") or {}).get("daty_iz_karty"):
        _daty_iz_karty(istochnik, zapisi, poluchit)
    return zapisi, oshibki


def _otmetit_novyy(kandidaty: list[Kandidat], sost: dict) -> list[Kandidat]:
    """novyy — кандидата нет в vidennye."""
    for k in kandidaty:
        k.novyy = not sostoyanie.videno(sost, k.identifikator)
    return kandidaty


def _odin(istochnik: dict, sost: dict, period: Period, okno: Period, poluchit, fayl_issue=None) -> dict:
    """Один источник → {kandidaty, tolko_v_vidennye, oshibka, otchet, polnyy_obhod}."""
    imya = istochnik["imya"]
    itog = {"imya": imya, "kandidaty": [], "tolko_v_vidennye": [], "oshibka": None, "otchet": "",
            "polnyy_obhod": False}
    try:
        if istochnik["vid"] == "x":
            posty, otchet = iks.sobrat(istochnik, okno, poluchit)
            itog["kandidaty"] = _otmetit_novyy(posty, sost)
            puti = ", ".join(f"{k} — {v}" for k, v in otchet["puti"].items()) or "ни один"
            itog["otchet"] = f"пути: {puti}"
            if otchet["molchat"]:
                itog["oshibka"] = "не ответили аккаунты: " + "; ".join(
                    f"{a} ({p})" for a, p in otchet["molchat"].items())
        elif istochnik["vid"] == "github_issues":
            kandidaty, tolko, oshibka, polnyy, otchet = gitkhab.sobrat(istochnik, sost, period, poluchit,
                                                                      fayl_issue)
            itog["kandidaty"] = kandidaty
            itog["tolko_v_vidennye"] = tolko
            itog["oshibka"] = oshibka
            itog["polnyy_obhod"] = polnyy
            itog["otchet"] = otchet
            if polnyy:
                itog["otchet"] = f"полный обход; уже набравших условия — в увиденные без выпуска: {len(tolko)}"
        else:
            byli = set()
            zapisi, oshibki = _zapisi(istochnik, poluchit)
            if oshibki:
                itog["oshibka"] = "; ".join(oshibki)
            for z in zapisi:
                if not v_okne(z, okno) or z.identifikator in byli:
                    continue
                z = _filtr(istochnik, z)
                if z is None:
                    continue
                byli.add(z.identifikator)
                itog["kandidaty"].append(Kandidat(identifikator=z.identifikator, istochnik=imya,
                                                  zagolovok=z.zagolovok or z.url, url=z.url,
                                                  data=z.data, tekst=z.tekst,
                                                  novyy=not sostoyanie.videno(sost, z.identifikator)))
    except (OshibkaIstochnika, OshibkaZaprosa) as e:
        itog["oshibka"] = str(e)
    except Exception as e:
        itog["oshibka"] = f"{imya}: {type(e).__name__}: {e}"
        itog["polnyy_obhod"] = False
        if istochnik["vid"] == "github_issues" and not sostoyanie.obhod_deystvuet(sost):
            itog["kandidaty"], itog["tolko_v_vidennye"] = [], []
    return itog


def sobrat(istochniki: list[dict], sost: dict, nachalo_progona: datetime, poluchit=None,
           fayl_issue: Path | None = None) -> dict:
    """Все источники → {period, okno, pervyy_progon, kandidaty, ne_otvetili, tolko_v_vidennye,
    github_polnyy_obhod, itogi}; period и okno — Period, kandidaty — список Kandidat.

    fayl_issue — путь к файлу сборщика issue (только сбор агента); None — файл не читается."""
    poluchit = poluchit or poluchit_po_seti
    period = Period(*sostoyanie.period(sost, nachalo_progona))
    okno = okno_ohvata(period)
    pervyy = sostoyanie.pervyy_progon(sost)
    with ThreadPoolExecutor(max_workers=POTOKOV) as pul:
        itogi = list(pul.map(lambda i: _odin(i, sost, period, okno, poluchit, fayl_issue), istochniki))
    kandidaty, ne_otvetili, tolko, byli = [], [], [], set()
    for it in itogi:
        for k in it["kandidaty"]:
            if k.identifikator not in byli:
                byli.add(k.identifikator)
                kandidaty.append(k)
        tolko.extend(it["tolko_v_vidennye"])
        if it["oshibka"]:
            ne_otvetili.append(it["imya"])
    polnyy = any(it["polnyy_obhod"] and not it["oshibka"] for it in itogi)
    return {"period": period, "okno": okno, "pervyy_progon": pervyy,
            "kandidaty": kandidaty, "ne_otvetili": ne_otvetili, "tolko_v_vidennye": tolko,
            "github_polnyy_obhod": polnyy, "itogi": itogi}


KOD_NIKTO_NE_OTVETIL = 4


class OshibkaSobytiya(Exception):
    """В событии GitHub нет разбираемого времени открытия заявки."""


def konec_iz_sobytiya(put: Path) -> datetime:
    """Конец периода у публикатора — pull_request.created_at из файла события GitHub."""
    try:
        sobytie = json.loads(Path(put).read_text(encoding="utf-8"))
        return iz_stroki(sobytie["pull_request"]["created_at"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise OshibkaSobytiya(f"в событии заявки нет времени открытия (pull_request.created_at): "
                              f"{type(e).__name__}") from None


def sbor_publikatora(put_sobytiya: Path, poluchit=None, put_istochnikov: Path | None = None,
                     put_sostoyaniya: Path | None = None, put_progona: Path | None = None) -> int:
    """Команда `sbor --konec-iz-zayavki <event.json>`: тот же сбор, конец периода — открытие заявки.

    Коды: 0 — собрано; 1 — состояние не читается; 2 — событие без времени открытия, файл прогона не
    пишется; 4 — не ответил ни один источник (файл прогона записан, публиковать по нему нельзя).
    """
    try:
        konec = konec_iz_sobytiya(put_sobytiya)
    except OshibkaSobytiya as e:
        print(f"newsai sbor: {e}", file=sys.stderr)
        return 2
    kod, nikto = _sbor(poluchit, konec, put_istochnikov, put_sostoyaniya, put_progona)
    if kod == 0 and nikto:
        return KOD_NIKTO_NE_OTVETIL
    return kod


def sbor(poluchit=None, nachalo_progona: datetime | None = None, put_istochnikov: Path | None = None,
         put_sostoyaniya: Path | None = None, put_progona: Path | None = None,
         put_issue: Path | None = None) -> int:
    """Команда `sbor`: собрать кандидатов и не ответивших в файл прогона; вернуть код выхода.

    put_issue — файл сборщика issue; команда агента передаёт nastrojki.fayl_issue()."""
    return _sbor(poluchit, nachalo_progona or seychas(), put_istochnikov, put_sostoyaniya, put_progona,
                 put_issue)[0]


def _sbor(poluchit, nachalo_progona: datetime, put_istochnikov, put_sostoyaniya,
          put_progona, put_issue=None) -> tuple[int, bool]:
    """Сбор в файл прогона → (код выхода, не ответил ли ни один источник)."""
    try:
        sost = sostoyanie.zagruzit(put_sostoyaniya)
    except sostoyanie.OshibkaSostoyaniya as e:
        print(f"newsai sbor: {e}", file=sys.stderr)
        return 1, False
    rez = sobrat(zagruzit_istochniki(put_istochnikov), sost, nachalo_progona, poluchit, put_issue)
    period, okno = rez["period"], rez["okno"]
    put = progon.zapisat(period, okno, rez["pervyy_progon"], rez["kandidaty"], rez["ne_otvetili"],
                         rez["tolko_v_vidennye"], rez["github_polnyy_obhod"], put_progona)
    print(f"период: {v_stroku(period.nachalo)} — {v_stroku(period.konec)}"
          + (" (первый прогон)" if rez["pervyy_progon"] else ""))
    print(f"окно охвата: {v_stroku(okno.nachalo)} — {v_stroku(okno.konec)}")
    for it in rez["itogi"]:
        novyh = sum(1 for k in it["kandidaty"] if k.novyy)
        stroka = f"{it['imya']}: {len(it['kandidaty'])}, новых {novyh}"
        if it["otchet"]:
            stroka += f" · {it['otchet']}"
        if it["oshibka"]:
            stroka += f" · НЕ ОТВЕТИЛ: {it['oshibka']}"
        print(stroka)
    print(f"кандидатов: {len(rez['kandidaty'])}, новых {sum(1 for k in rez['kandidaty'] if k.novyy)}")
    print("не ответили: " + (", ".join(rez["ne_otvetili"]) or "—"))
    nikto = len(rez["ne_otvetili"]) == len(rez["itogi"])
    if nikto:
        print("не ответил ни один источник")
    print(f"файл прогона: {put}")
    return 0, nikto
