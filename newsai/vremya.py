"""Время: всё внутри — UTC; в файлах — строка ISO с «Z»."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import nastrojki


def seychas() -> datetime:
    return datetime.now(timezone.utc)


def v_stroku(dt: datetime) -> str:
    """UTC-время → '2026-11-11T02:07:00Z'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def iz_stroki(s: str) -> datetime:
    """Строка ISO (с «Z», смещением или без пояса) → UTC-время; без пояса считается UTC."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def slot(dt: datetime) -> str:
    """Слот выпуска — последний из 01:00, 09:00, 17:00 по Бангкоку, не позже dt."""
    mestnoe = dt.astimezone(timezone(timedelta(hours=nastrojki.SDVIG_BANGKOKA_CHASOV)))
    seychas_hm = mestnoe.strftime("%H:%M")
    proshedshie = [s for s in nastrojki.SLOTY if s <= seychas_hm]
    return proshedshie[-1] if proshedshie else nastrojki.SLOTY[-1]
