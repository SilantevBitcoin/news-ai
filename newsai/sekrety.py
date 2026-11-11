"""Чтение секрета: сначала переменная окружения, иначе строка ИМЯ=значение в файле секретов проекта.

Одно место для всех секретов пакета: токен бота (tokenbota), ключ twitterapi.io (iks), токен GitHub
(gitkhab). Значение никуда не печатается; вызывающий говорит в ошибках только имя переменной.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import nastrojki

OKRUZHENIE = "в переменной окружения"
FAYL = "в файле секретов проекта"


def _iz_fayla(put: Path, imya: str) -> str | None:
    """Простой разбор строк ИМЯ=значение: комментарии, пустые строки, export и кавычки."""
    try:
        stroki = put.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for stroka in stroki:
        stroka = stroka.strip()
        if not stroka or stroka.startswith("#") or "=" not in stroka:
            continue
        klyuch, _, znachenie = stroka.partition("=")
        klyuch = klyuch.strip()
        if klyuch.startswith("export "):
            klyuch = klyuch[len("export "):].strip()
        if klyuch != imya:
            continue
        znachenie = znachenie.strip()
        if len(znachenie) >= 2 and znachenie[0] == znachenie[-1] and znachenie[0] in "\"'":
            znachenie = znachenie[1:-1]
        elif " #" in znachenie:
            znachenie = znachenie.split(" #", 1)[0].rstrip()
        return znachenie or None
    return None


def nayti(imya: str, fayl: Path | None = None) -> tuple[str | None, str | None]:
    """→ (значение, откуда: OKRUZHENIE или FAYL) или (None, None).

    Окружение важнее файла: переменная задана — файл не читается. Файл по умолчанию —
    nastrojki.FAYL_SEKRETOV на момент вызова (тесты его подменяют).
    """
    znachenie = (os.environ.get(imya) or "").strip()
    if znachenie:
        return znachenie, OKRUZHENIE
    znachenie = _iz_fayla(Path(fayl) if fayl else nastrojki.FAYL_SEKRETOV, imya)
    if znachenie:
        return znachenie, FAYL
    return None, None


def prochitat(imya: str, fayl: Path | None = None) -> str | None:
    """Значение секрета или None."""
    return nayti(imya, fayl)[0]
