"""Настройки проекта: канал, бот, пути к файлам, лимиты.

Файлы данных — в папке dannye/ репозитория. Пути к файлам состояния, прогона, выпуска и сбоя
можно подменить переменными окружения NEWSAI_SOSTOYANIE, NEWSAI_PROGON, NEWSAI_VYPUSK, NEWSAI_SBOJ,
NEWSAI_ISSUE — для тестов и для публикатора, который читает файлы заявки из своей папки.
"""
from __future__ import annotations

import json
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

ISSUE_GODEN_CHASOV = 24
ISSUE_POISK_DNEY = 7

LIMIT_SOOBSHCHENIYA = 4096
PRICHINA_SBOYA_ZNAKOV = 200

PAPKA_DANNYH = KOREN / "dannye"

PEREMENNAYA_REZHIMA = "NEWSAI_REZHIM_PROVERKI"


def rezhim_proverki() -> bool:
    """Режим проверки включён: переменная окружения ровно «1»; нет её или другое значение — выключен.

    Читается в момент вызова: тест подменяет окружение [техника]."""
    return os.environ.get(PEREMENNAYA_REZHIMA) == "1"


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


def fayl_issue() -> Path:
    """Файл issue: пишет только команда `sbor-issue` (у публикатора после заявки и у сборщика issue),
    лежит в основной ветке; читает его только `sbor` агента."""
    return _put("NEWSAI_ISSUE", PAPKA_DANNYH / "issue.json")


def _istochniki() -> list:
    return json.loads(FAYL_ISTOCHNIKOV.read_text(encoding="utf-8"))["istochniki"]


def podpisi_x() -> dict:
    """Справочник подписей X (Р65): аккаунт в нижнем регистре → подпись. Поля нет — пустой словарь."""
    for i in _istochniki():
        if i.get("vid") == "x":
            return {akk.lower(): podpis for akk, podpis in (i.get("podpisi") or {}).items()}
    return {}


def nazvaniya_v_kanale() -> dict:
    """Имя источника → как назвать его в строке «Не удалось собрать» (Р64): v_kanale или имя."""
    return {i["imya"]: i.get("v_kanale") or i["imya"] for i in _istochniki()}
