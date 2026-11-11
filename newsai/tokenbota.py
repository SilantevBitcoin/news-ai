"""Токен бота: чтение — sekrety (окружение, затем файл секретов), здесь — проверка вида.

Токен никуда не печатается и не пишется; в сообщениях об ошибке — только имя переменной.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import nastrojki, sekrety


_VID_TOKENA = re.compile(r"^\d+:[A-Za-z0-9_-]+$")


class NetTokena(Exception):
    """Токена нет или он не того вида; значение токена в сообщение не попадает."""


def zagruzit_token(fayl: Path | None = None) -> str:
    """Вернуть токен бота; нет нигде или не того вида — NetTokena без значения.

    Окружение важнее файла: если переменная задана, файл не читается.
    """
    imya = nastrojki.PEREMENNAYA_TOKENA
    token, otkuda = sekrety.nayti(imya, fayl)
    if not token:
        raise NetTokena(f"нет {imya}: задайте переменную окружения или строку в файле секретов проекта")
    if not _VID_TOKENA.fullmatch(token):
        raise NetTokena(f"{imya} {otkuda} не того вида: ждём <цифры>:<буквы, цифры, _ и ->")
    return token
