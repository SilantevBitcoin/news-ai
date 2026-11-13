"""Файл прогона сбора — договор между сбором (пишет), отбором агента и публикатором (читают).

У агента — его сбор, у публикатора — свой сбор `sbor --konec-iz-zayavki`; файл прогона из заявки
агента публикатор не читает.

Вид файла:
    {
      "nachalo_progona": "<UTC>",      # конец периода; после публикации posledniy_sbor — он минус запас
      "nachalo_perioda": "<UTC>",      # период выпуска: от posledniy_sbor до начала прогона
      "nachalo_okna": "<UTC>",         # окно охвата: 24 часа до начала прогона или весь период (Р46)
      "pervyy_progon": true | false,
      "kandidaty": [<кандидат>],       # все кандидаты окна охвата
      "ne_otvetili": ["X", "OpenAI", ...],   # имена источников, как в istochniki.json
      "tolko_v_vidennye": ["<id>", ...],     # отметить увиденным без выпуска (issue полного обхода)
      "github_polnyy_obhod": true | false,   # полный обход issue удался — публикатор ставит github_obhod
      "istochniki": ["OpenAI", ...],         # все опрошенные источники, кроме X (служебная строка, Р74)
      "iks": {"akkauntov": 30, "ne_otvetili": ["mikeyk", ...]} | null   # аккаунты X без постов (Р74)
    }
Кандидат: id (адрес записи; для X — адрес поста; для OpenAI API — адрес, дата и заголовок), istochnik,
zagolovok, url, data (UTC), tekst (до 3000 знаков), skoro (issue GitHub), novyy (нет в vidennye),
akkaunt — только у постов X, имя без @.

В памяти кандидат — Kandidat, период и окно — Period; в JSON они превращаются только в zapisat.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import nastrojki
from .vremya import v_stroku


@dataclass(frozen=True)
class Period:
    """Промежуток времени [nachalo, konec): период выпуска или окно охвата."""
    nachalo: datetime
    konec: datetime

    def __contains__(self, kogda: datetime) -> bool:
        return self.nachalo <= kogda < self.konec


@dataclass
class Kandidat:
    """Кандидат в выпуск; data — всегда datetime, в строку — только при записи файла."""
    identifikator: str
    istochnik: str
    zagolovok: str
    url: str
    data: datetime
    tekst: str = ""
    skoro: bool = False
    novyy: bool = True
    akkaunt: str | None = None        # только у постов X

    def __post_init__(self):
        if not isinstance(self.data, datetime):
            raise TypeError(f"кандидат {self.identifikator}: data — не datetime, а {type(self.data).__name__}")
        self.tekst = (self.tekst or "")[: nastrojki.TEKST_KANDIDATA_ZNAKOV]
        self.skoro, self.novyy = bool(self.skoro), bool(self.novyy)
        if self.akkaunt is not None:
            self.akkaunt = self.akkaunt.lstrip("@")

    def v_slovar(self) -> dict:
        """Кандидат в виде файла прогона."""
        k = {
            "id": self.identifikator,
            "istochnik": self.istochnik,
            "zagolovok": self.zagolovok,
            "url": self.url,
            "data": v_stroku(self.data),
            "tekst": self.tekst,
            "skoro": self.skoro,
            "novyy": self.novyy,
        }
        if self.akkaunt is not None:
            k["akkaunt"] = self.akkaunt
        return k


def zapisat(period: Period, okno: Period, pervyy_progon: bool, kandidaty: list[Kandidat],
            ne_otvetili: list[str], tolko_v_vidennye: list[str] = (), github_polnyy_obhod: bool = False,
            put: Path | None = None, istochniki: list[str] = (), iks: dict | None = None) -> Path:
    """Записать файл прогона; начало прогона — конец периода."""
    put = Path(put) if put else nastrojki.fayl_progona()
    put.parent.mkdir(parents=True, exist_ok=True)
    dannye = {
        "nachalo_progona": v_stroku(period.konec),
        "nachalo_perioda": v_stroku(period.nachalo),
        "nachalo_okna": v_stroku(okno.nachalo),
        "pervyy_progon": bool(pervyy_progon),
        "kandidaty": [k.v_slovar() for k in kandidaty],
        "ne_otvetili": list(ne_otvetili),
        "tolko_v_vidennye": list(tolko_v_vidennye),
        "github_polnyy_obhod": bool(github_polnyy_obhod),
        "istochniki": list(istochniki),
        "iks": dict(iks) if iks is not None else None,
    }
    put.write_text(json.dumps(dannye, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return put


def prochitat(put: Path | None = None) -> dict:
    put = Path(put) if put else nastrojki.fayl_progona()
    return json.loads(put.read_text(encoding="utf-8"))
