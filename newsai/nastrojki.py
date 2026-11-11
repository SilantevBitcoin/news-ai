"""Настройки проекта: канал, бот, пути к файлам, лимиты.

Файлы данных — в папке dannye/ репозитория. Пути к файлам состояния, прогона, выпуска и сбоя
можно подменить переменными окружения NEWSAI_SOSTOYANIE, NEWSAI_PROGON, NEWSAI_VYPUSK, NEWSAI_SBOJ —
для тестов и для публикатора, который читает файлы заявки из своей папки.
"""
from __future__ import annotations

import os
from pathlib import Path

KOREN = Path(__file__).resolve().parents[1]

KANAL_ID = -1004434993234
BOT = "newsai_egor_bot"
PEREMENNAYA_TOKENA = "TELEGRAM_BOT_TOKEN"
FAYL_SEKRETOV = KOREN / ".env"

FAYL_ISTOCHNIKOV = KOREN / "istochniki.json"

SLOTY = ("01:00", "09:00", "17:00")
SDVIG_BANGKOKA_CHASOV = 7

PERVYY_PERIOD_CHASOV = 24
VIDENNYE_DNEY = 30
OPUBLIKOVANNYE_DNEY = 14
ZAPAS_MINUT = 60

TAYMAUT_SEKUND = 20
POVTOROV = 1
TEKST_KANDIDATA_ZNAKOV = 3000
ZAGOLOVOK_X_ZNAKOV = 120
TEKST_STRANICY_ZNAKOV = 20000

LIMIT_SOOBSHCHENIYA = 4096
PRICHINA_SBOYA_ZNAKOV = 200

PAPKA_DANNYH = KOREN / "dannye"


def _put(peremennaya: str, po_umolchaniyu: Path) -> Path:
    znachenie = os.environ.get(peremennaya)
    return Path(znachenie) if znachenie else po_umolchaniyu


def fayl_sostoyaniya() -> Path:
    """Файл состояния: пишет только публикатор, лежит в основной ветке (Р18)."""
    return _put("NEWSAI_SOSTOYANIE", PAPKA_DANNYH / "sostoyanie.json")


def fayl_progona() -> Path:
    """Файл прогона сбора: кандидаты и не ответившие. Агент коммитит его в свою ветку."""
    return _put("NEWSAI_PROGON", PAPKA_DANNYH / "progon.json")


def fayl_vypuska() -> Path:
    """Файл выпуска, который пишет агент после отбора. Агент коммитит его в свою ветку."""
    return _put("NEWSAI_VYPUSK", PAPKA_DANNYH / "vypusk.json")


def fayl_sboya() -> Path:
    """Файл сбоя {"prichina": ...} — вместо файла выпуска, когда выпуск не собрать."""
    return _put("NEWSAI_SBOJ", PAPKA_DANNYH / "sboj.json")
