"""Команда `tekst <url>` — текст страницы без разметки, до 20 000 знаков (для пересказа крупной).

Открывает только http/https на публичные адреса: хост после резолва не приватный, не loopback,
не link-local, не reserved и не multicast; так же проверяется каждая переадресация.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from . import nastrojki
from .razbor import html_v_tekst, tekst_stranicy
from .zaprosy import OshibkaZaprosa, poluchit as poluchit_po_seti


def _zakrytyy(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global)


def proverit_adres(url: str) -> None:
    """Отказ — OshibkaZaprosa с причиной."""
    chasti = urlsplit(url)
    if chasti.scheme.lower() not in ("http", "https"):
        raise OshibkaZaprosa(f"адрес не http(s): {url[:200]}")
    host = chasti.hostname
    if not host:
        raise OshibkaZaprosa(f"в адресе нет хоста: {url[:200]}")
    try:
        port = chasti.port or (443 if chasti.scheme.lower() == "https" else 80)
        adresa = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError) as e:
        raise OshibkaZaprosa(f"{host}: не резолвится ({e})")
    if not adresa:
        raise OshibkaZaprosa(f"{host}: не резолвится")
    for *_, sockaddr in adresa:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if _zakrytyy(ip):
            raise OshibkaZaprosa(f"{host}: не публичный адрес {ip}")


def tekst(url: str, poluchit=None) -> str:
    """Вернуть текст страницы по адресу, не длиннее nastrojki.TEKST_STRANICY_ZNAKOV.

    Страница не открылась или адрес не прошёл проверку — SystemExit с причиной: агент берёт текст
    кандидата.
    """
    poluchit = poluchit or poluchit_po_seti
    try:
        otvet = poluchit(url, proverka=proverit_adres)
    except OshibkaZaprosa as e:
        raise SystemExit(f"newsai tekst: страница не открылась: {e}")
    if otvet.status != 200:
        raise SystemExit(f"newsai tekst: страница не открылась: ответ {otvet.status}")
    tip = otvet.zagolovki.get("content-type", "").lower()
    syroe = otvet.tekst()
    if "html" in tip or syroe.lstrip()[:200].lower().startswith(("<!doctype html", "<html")):
        vyhod = tekst_stranicy(syroe)
    elif "xml" in tip:
        vyhod = html_v_tekst(syroe)
    elif tip.startswith("text/") or "json" in tip or not tip:
        vyhod = syroe
    else:
        raise SystemExit(f"newsai tekst: не текстовая страница ({tip})")
    return vyhod[: nastrojki.TEKST_STRANICY_ZNAKOV]
