"""Состояние выпусков — файл в репозитории (Р18), спека «Период, окно охвата и состояние».

Вид файла:
    {
      "posledniy_sbor": "2026-11-11T01:07:00Z" | null,   # конец периода вышедшего выпуска минус запас, UTC
      "posledniy_slot": "2026-11-11T02:00:00Z" | null,   # слот последней обработанной заявки (начало, UTC)
      "github_obhod": true | false,                      # полный обход issue GitHub уже был
      "vidennye": {"<id кандидата>": "<когда увиден, UTC>"},
      "opublikovannye": [{"zagolovok", "url", "data": "<UTC>",
                          "vid": "krupnaya|sayty|x|dengi|github", "oboroty": bool}]
    }

Кто что вызывает:
- сбор: zagruzit → period / pervyy_progon / videno, отметка github_obhod;
- публикация: zapisat_punkt сразу после каждого отправленного сообщения (файл пишется тут же),
  zavershit_progon в конце — в том числе при пустом выпуске; zapisat_slot — при сбое.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from . import nastrojki
from .vremya import iz_stroki, v_stroku

_ISSUE_GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/[^/]+/[^/]+/issues/\d+/?$", re.IGNORECASE)

VIDY = ("krupnaya", "sayty", "x", "dengi", "github")


class OshibkaSostoyaniya(Exception):
    """Файл состояния есть, но не читается — молча начинать с нуля нельзя: выпуск повторит старое."""


def pustoe() -> dict:
    return {"posledniy_sbor": None, "posledniy_slot": None, "github_obhod": False,
            "vidennye": {}, "opublikovannye": []}


def zagruzit(put: Path | None = None) -> dict:
    """Прочитать состояние; файла нет — пустое состояние (первый прогон)."""
    put = Path(put) if put else nastrojki.fayl_sostoyaniya()
    if not put.exists():
        return pustoe()
    try:
        dannye = json.loads(put.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OshibkaSostoyaniya(f"файл состояния не читается: {e}") from e
    if not isinstance(dannye, dict):
        raise OshibkaSostoyaniya("файл состояния не читается: ожидался объект JSON")
    sost = pustoe()
    sost["posledniy_sbor"] = dannye.get("posledniy_sbor")
    sost["posledniy_slot"] = dannye.get("posledniy_slot")
    sost["github_obhod"] = bool(dannye.get("github_obhod"))
    sost["vidennye"] = dict(dannye.get("vidennye") or {})
    sost["opublikovannye"] = list(dannye.get("opublikovannye") or [])
    return sost


def sohranit(sost: dict, put: Path | None = None) -> None:
    """Записать состояние целиком; через временный файл, чтобы обрыв не оставил половину."""
    put = Path(put) if put else nastrojki.fayl_sostoyaniya()
    put.parent.mkdir(parents=True, exist_ok=True)
    vremennyy = put.with_name(put.name + ".tmp")
    tekst = json.dumps(sost, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    vremennyy.write_text(tekst, encoding="utf-8", newline="\n")
    os.replace(vremennyy, put)


def pervyy_progon(sost: dict) -> bool:
    """Первый прогон — выпусков ещё не было."""
    return not sost.get("posledniy_sbor")


def period(sost: dict, nachalo_progona: datetime) -> tuple[datetime, datetime]:
    """Период выпуска: от posledniy_sbor до начала текущего прогона; первый прогон — последние 24 часа."""
    if pervyy_progon(sost):
        return nachalo_progona - timedelta(hours=nastrojki.PERVYY_PERIOD_CHASOV), nachalo_progona
    return iz_stroki(sost["posledniy_sbor"]), nachalo_progona


def eto_issue_github(identifikator: str) -> bool:
    return bool(_ISSUE_GITHUB.match(identifikator or ""))


def videno(sost: dict, identifikator: str) -> bool:
    return identifikator in sost["vidennye"]


def novye(sost: dict, kandidaty: Iterable[dict]) -> list[dict]:
    """Кандидаты, которых ещё нет в vidennye (ключ — поле id)."""
    return [k for k in kandidaty if not videno(sost, k["id"])]


def zapomnit(sost: dict, identifikator: str, kogda: datetime) -> None:
    """Отметить кандидата увиденным; дата первой встречи не перезаписывается."""
    sost["vidennye"].setdefault(identifikator, v_stroku(kogda))


def pochistit(sost: dict, seychas: datetime) -> None:
    """Удалить vidennye старше 30 дней (кроме issue GitHub) и opublikovannye старше 14 дней."""
    granica_v = seychas - timedelta(days=nastrojki.VIDENNYE_DNEY)
    sost["vidennye"] = {
        i: d for i, d in sost["vidennye"].items()
        if eto_issue_github(i) or iz_stroki(d) >= granica_v
    }
    granica_o = seychas - timedelta(days=nastrojki.OPUBLIKOVANNYE_DNEY)
    sost["opublikovannye"] = [p for p in sost["opublikovannye"] if iz_stroki(p["data"]) >= granica_o]


def zapisat_punkt(sost: dict, zagolovok: str, url: str, kogda: datetime,
                  idy: Iterable[str] = (), put: Path | None = None, *,
                  vid: str, oboroty: bool = False) -> None:
    """Отправленный пункт — сразу в opublikovannye и vidennye, и сразу в файл.

    idy — идентификаторы склеенных в пункт кандидатов; адрес пункта отмечается увиденным всегда.
    Крупная с oboroty помечает и прежние записи с тем же адресом: строкой без oboroty новость
    больше не числится, «набирает обороты» второй раз не выйдет (Р46).
    """
    if vid not in VIDY:
        raise ValueError(f"неизвестный вид пункта: {vid}")
    oboroty = bool(oboroty) and vid == "krupnaya"
    if oboroty:
        for p in sost["opublikovannye"]:
            if p.get("url") == url:
                p["oboroty"] = True
    sost["opublikovannye"].append({"zagolovok": zagolovok, "url": url, "data": v_stroku(kogda),
                                   "vid": vid, "oboroty": oboroty})
    for i in {url, *idy}:
        zapomnit(sost, i, kogda)
    sohranit(sost, put)


def zapisat_slot(sost: dict, slot: str, put: Path | None = None) -> None:
    """Заявка слота обработана (выпуск, пустой выпуск или сбой) — отметить и записать в файл."""
    sost["posledniy_slot"] = slot
    sohranit(sost, put)


def nachalo_zapasa(konec_perioda: datetime) -> datetime:
    """Начало запаса: конец периода минус ZAPAS_MINUT. Это и есть новый posledniy_sbor."""
    return konec_perioda - timedelta(minutes=nastrojki.ZAPAS_MINUT)


def zavershit_progon(sost: dict, nachalo_progona: datetime, idy: Iterable[str], kogda: datetime,
                     put: Path | None = None, *, slot: str | None = None,
                     github_obhod: bool = False) -> None:
    """Конец прогона, в том числе пустого: сдвинуть период, запомнить увиденное, почистить, записать.

    nachalo_progona — конец периода; posledniy_sbor становится им минус запас (nachalo_zapasa).
    slot — слот обработанной заявки; github_obhod — сбор отметил удачный полный обход issue.
    """
    sost["posledniy_sbor"] = v_stroku(nachalo_zapasa(nachalo_progona))
    if slot is not None:
        sost["posledniy_slot"] = slot
    if github_obhod:
        sost["github_obhod"] = True
    for i in idy:
        zapomnit(sost, i, kogda)
    pochistit(sost, kogda)
    sohranit(sost, put)
