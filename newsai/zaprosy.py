"""HTTP для сбора и `tekst` (спека «Источники»).

Таймаут 20 секунд и один повтор; общий срок на запрос — 60 секунд, держит всё ожидание ответа: к сроку
сторож закрывает сокет, что бы ни читалось — строка статуса, заголовки, строка размера chunked, тело.
Ответ больше 10 МБ — при чтении или после распаковки — отказ; переадресации идут здесь, по одной, и на
другом хосте теряют ключи.

Шов для тестов: функции сбора принимают `poluchit(url, zagolovki=None) -> Otvet`; сам `poluchit`
проверяется с подменённым `zaprosy.urlopen`.
"""
from __future__ import annotations

import http.client
import socket
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from . import nastrojki

BRAUZER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

PREDEL_BAYT = 10 * 1024 * 1024
OBSHCHIY_SROK_SEKUND = 60
PEREADRESACIY = 10
KUSOK_BAYT = 64 * 1024
KLYUCHI = ("authorization", "x-api-key")


class OshibkaZaprosa(Exception):
    """Сеть не ответила и после повтора, ответ сверх предела или адрес не прошёл проверку."""


@dataclass
class Otvet:
    status: int
    telo: bytes = b""
    zagolovki: dict = field(default_factory=dict)

    def tekst(self) -> str:
        kodirovka = "utf-8"
        tip = self.zagolovki.get("content-type", "")
        if "charset=" in tip:
            kodirovka = tip.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.telo.decode(kodirovka, errors="replace")
        except LookupError:
            return self.telo.decode("utf-8", errors="replace")


class _BezPereadresacii(urllib.request.HTTPRedirectHandler):
    """Переадресацию urllib не выполняет: 3xx приходит как ответ, дальше решает poluchit."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _istek() -> OshibkaZaprosa:
    return OshibkaZaprosa(f"общий срок запроса {OBSHCHIY_SROK_SEKUND} с истёк")


def _zakryt(sock: socket.socket) -> None:
    """Закрыть сокет из чужого потока. [техника]

    На Windows shutdown не будит ждущее чтение, будит только закрытие; обычный close при открытом
    makefile откладывается — поэтому detach (объект сокета сразу недействителен, в том числе для TLS)
    и закрытие дескриптора.
    """
    try:
        fd = socket.socket.detach(sock)
    except OSError:
        return
    if fd is not None and fd >= 0:
        try:
            socket.close(fd)
        except OSError:
            pass


class _Storozh:
    """Таймер к общему сроку: закрывает сокеты запроса, что бы в этот момент ни читалось. [техника]"""

    def __init__(self, cherez: float):
        self._zamok = threading.Lock()
        self._sokety: list[socket.socket] = []
        self._snyat = False
        self.istek = False
        self._taymer = threading.Timer(cherez, self._srabotat)
        self._taymer.daemon = True
        self._taymer.start()

    def zapomnit(self, sock: socket.socket) -> None:
        with self._zamok:
            if self.istek:
                _zakryt(sock)
            elif not self._snyat:
                self._sokety.append(sock)

    def _srabotat(self) -> None:
        with self._zamok:
            if self._snyat:
                return
            self.istek = True
            for sock in self._sokety:
                _zakryt(sock)
            self._sokety.clear()

    def snyat(self) -> bool:
        """Снять таймер и дождаться его потока; вернуть, истёк ли срок."""
        with self._zamok:
            self._snyat = True
            self._sokety.clear()
        self._taymer.cancel()
        self._taymer.join()
        return self.istek


_MESTNOE = threading.local()


def _zapomnit(sock: socket.socket) -> None:
    storozh = getattr(_MESTNOE, "storozh", None)
    if storozh is not None:
        storozh.zapomnit(sock)


def _soedinit(adres, timeout, source_address=None, *a, **kw):
    """socket.create_connection, сокет сразу под сторожем — до ответа прокси и до TLS."""
    sock = socket.create_connection(adres, timeout, source_address, *a, **kw)
    _zapomnit(sock)
    return sock


class _HTTP(http.client.HTTPConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _soedinit


class _HTTPS(http.client.HTTPSConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _soedinit

    def connect(self):
        """Как у HTTPSConnection, но TLS-сокет под сторожем ещё до рукопожатия."""
        http.client.HTTPConnection.connect(self)
        imya = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=imya,
                                              do_handshake_on_connect=False)
        _zapomnit(self.sock)
        self.sock.do_handshake()


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTP, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPS, req, context=self._context)


_OTKRYVATEL = urllib.request.build_opener(_BezPereadresacii, _HTTPHandler, _HTTPSHandler)


def urlopen(zapros, timeout):
    """Точка подмены в тестах."""
    return _OTKRYVATEL.open(zapros, timeout=timeout)


def _ostalos(srok: float) -> float:
    ostalos = srok - time.monotonic()
    if ostalos <= 0:
        raise _istek()
    return ostalos


def _soket(potok) -> socket.socket | None:
    """Сокет ответа: HTTPResponse.fp.raw._sock; у HTTPError — через его fp. Нет — None. [техника]"""
    o = potok
    for _ in range(3):
        if o is None:
            return None
        sock = getattr(getattr(getattr(o, "fp", None), "raw", None), "_sock", None)
        if isinstance(sock, socket.socket):
            return sock
        o = getattr(o, "fp", None)
    return None


def _prochitat(potok, srok: float) -> bytes:
    """Тело кусками, не больше PREDEL_BAYT; каждое чтение — не дольше остатка общего срока.

    Кусок — одно чтение сокета (read1), а не полный буфер: сервер, отдающий по байту, срок не продлевает.
    """
    chitat = getattr(potok, "read1", None) or potok.read
    sock = _soket(potok)
    chasti, vsego = [], 0
    while True:
        ostalos = _ostalos(srok)
        taymaut = min(nastrojki.TAYMAUT_SEKUND, ostalos)
        if sock is not None and sock.fileno() != -1:
            sock.settimeout(taymaut)
        try:
            kusok = chitat(KUSOK_BAYT)
        except (socket.timeout, TimeoutError):
            if taymaut >= ostalos:
                raise _istek() from None
            raise OshibkaZaprosa(f"таймаут чтения ответа {nastrojki.TAYMAUT_SEKUND} с") from None
        if not kusok:
            return b"".join(chasti)
        vsego += len(kusok)
        if vsego > PREDEL_BAYT:
            raise OshibkaZaprosa(f"ответ больше {PREDEL_BAYT // (1024 * 1024)} МБ")
        chasti.append(kusok)


GZIP = bytes([0x1F, 0x8B])


def _potokom(telo: bytes, wbits: int) -> bytes:
    """Потоковая распаковка с пределом; несколько gzip-членов подряд — тоже."""
    vyhod, vsego, ostatok = [], 0, telo
    while ostatok:
        d = zlib.decompressobj(wbits)
        dannye = ostatok
        while dannye:
            chast = d.decompress(dannye, PREDEL_BAYT + 1 - vsego)
            vsego += len(chast)
            if vsego > PREDEL_BAYT:
                raise OshibkaZaprosa(f"ответ после распаковки больше {PREDEL_BAYT // (1024 * 1024)} МБ")
            vyhod.append(chast)
            dannye = d.unconsumed_tail
        if not d.eof:
            raise zlib.error("поток оборван")
        ostatok = d.unused_data if d.unused_data[:2] == GZIP else b""
    return b"".join(vyhod)


def _raspakovat(telo: bytes, kodirovanie: str) -> bytes:
    """Часть серверов шлёт gzip, даже когда его не просили (DeepMind); битый поток — тело как есть."""
    kodirovanie = (kodirovanie or "").lower()
    try:
        if "gzip" in kodirovanie or telo[:2] == GZIP:
            return _potokom(telo, 16 + zlib.MAX_WBITS)
        if "deflate" in kodirovanie:
            try:
                return _potokom(telo, zlib.MAX_WBITS)
            except zlib.error:
                return _potokom(telo, -zlib.MAX_WBITS)
    except zlib.error:
        return telo
    return telo


def _odin(url: str, zagolovki: dict, srok: float) -> Otvet:
    """Одна попытка под сторожем: к сроку сокет закрыт, любой исход после срока — отказ по сроку."""
    ostalos = _ostalos(srok)
    storozh = _Storozh(ostalos)
    _MESTNOE.storozh = storozh
    try:
        otvet = _odin_bez_storozha(url, zagolovki, srok, ostalos)
    except Exception:
        if storozh.snyat():
            raise _istek() from None
        raise
    finally:
        _MESTNOE.storozh = None
        storozh.snyat()
    if storozh.istek:
        raise _istek()
    return otvet


def _odin_bez_storozha(url: str, zagolovki: dict, srok: float, ostalos: float) -> Otvet:
    zapros = urllib.request.Request(url, headers=zagolovki)
    try:
        with urlopen(zapros, timeout=min(nastrojki.TAYMAUT_SEKUND, ostalos)) as o:
            status = getattr(o, "status", None)
            if not isinstance(status, int):
                raise OshibkaZaprosa(f"{url}: ответ без кода статуса")
            z = {k.lower(): v for k, v in o.headers.items()}
            return Otvet(status, _raspakovat(_prochitat(o, srok), z.get("content-encoding", "")), z)
    except urllib.error.HTTPError as e:
        z = {k.lower(): v for k, v in (e.headers or {}).items()}
        try:
            telo = _prochitat(e, srok)
        except OshibkaZaprosa:
            raise
        except Exception:
            telo = b""
        finally:
            e.close()
        return Otvet(e.code, _raspakovat(telo, z.get("content-encoding", "")), z)


def _shema(url: str) -> None:
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        raise OshibkaZaprosa(f"адрес не http(s): {url[:200]}")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _odin_s_povtorom(url: str, z: dict, srok: float) -> Otvet:
    """Повтор при сетевой ошибке или 5xx; 4xx, 3xx и 204 возвращаются как есть."""
    posledniy: Exception | None = None
    for popytka in range(1 + nastrojki.POVTOROV):
        if popytka:
            time.sleep(min(2, _ostalos(srok)))
        try:
            otvet = _odin(url, z, srok)
        except OshibkaZaprosa:
            raise
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            posledniy = e
            continue
        if otvet.status >= 500 and popytka < nastrojki.POVTOROV:
            continue
        return otvet
    raise OshibkaZaprosa(f"{url}: {posledniy}")


def poluchit(url: str, zagolovki: dict | None = None, proverka=None) -> Otvet:
    """GET с повтором и переадресациями.

    proverka(url) — зовётся перед каждым запросом, в том числе после каждой переадресации;
    отказ — исключение OshibkaZaprosa.
    """
    z = {"User-Agent": BRAUZER, "Accept": "*/*"}
    z.update(zagolovki or {})
    srok = time.monotonic() + OBSHCHIY_SROK_SEKUND
    for _ in range(PEREADRESACIY + 1):
        _shema(url)
        if proverka:
            proverka(url)
        otvet = _odin_s_povtorom(url, z, srok)
        kuda = otvet.zagolovki.get("location")
        if otvet.status not in (301, 302, 303, 307, 308) or not kuda:
            return otvet
        novyy = urljoin(url, kuda)
        if _host(novyy) != _host(url):
            z = {k: v for k, v in z.items() if k.lower() not in KLYUCHI}
        url = novyy
    raise OshibkaZaprosa(f"{url}: больше {PEREADRESACIY} переадресаций")
