"""Шов «Функция запроса с подменённым urlopen»: zaprosy.poluchit без сети.

Ловит: предел размера при чтении и распаковке, gzip без заголовка, ответ без статуса, снятие ключей
при переадресации на другой хост, общий срок — и на потоке по байту за чтение.
Шов «Проба срока на локальном сервере»: loopback-сервер по капле, срок 3 с — тело с Content-Length,
chunked с медленной строкой размера, медленные строка статуса и заголовки, 404 с медленным телом.
"""
import gzip
import io
import itertools
import socketserver
import sys
import threading
import time
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from newsai import zaprosy
from newsai.zaprosy import OshibkaZaprosa

MB = 1024 * 1024


def zagolovki(**z):
    m = Message()
    for k, v in z.items():
        m[k.replace("_", "-")] = v
    return m


class Otklik(io.BytesIO):
    """Ответ urlopen: тело, status и headers."""

    def __init__(self, telo=b"", status=200, **z):
        super().__init__(telo)
        self.status = status
        self.headers = zagolovki(**z)


class Nuli:
    """Поток из нулей нужной длины, без держания в памяти; читается и как ответ, и как тело ошибки."""

    def __init__(self, dlina, status=200):
        self.ostalos, self.status, self.headers = dlina, status, zagolovki()

    def read(self, n=-1):
        n = self.ostalos if n is None or n < 0 else min(n, self.ostalos)
        self.ostalos -= n
        return b"\0" * n

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def pereadresaciya(url, kuda, kod=302):
    return urllib.error.HTTPError(url, kod, "Found", zagolovki(Location=kuda), io.BytesIO(b""))


class Podmena:
    """urlopen по очереди отдаёт ответы из списка; исключение — бросает; запросы копятся."""

    def __init__(self, *otvety):
        self.otvety = list(otvety)
        self.zaprosy = []

    def __call__(self, zapros, timeout):
        self.zaprosy.append(zapros)
        o = self.otvety.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def zapusk(podmena, url="https://primer.example/lenta", **kw):
    with mock.patch.object(zaprosy, "urlopen", podmena), mock.patch.object(zaprosy.time, "sleep"):
        return zaprosy.poluchit(url, **kw)


class Telo(unittest.TestCase):
    def test_gzip_bez_zagolovka(self):
        o = zapusk(Podmena(Otklik(gzip.compress(b"<rss>lenta</rss>"), content_type="text/xml")))
        self.assertEqual((o.status, o.telo), (200, b"<rss>lenta</rss>"))

    def test_gzip_po_zagolovku_i_dva_chlena(self):
        telo = gzip.compress(b"<rss>") + gzip.compress(b"</rss>")
        o = zapusk(Podmena(Otklik(telo, content_encoding="gzip")))
        self.assertEqual(o.telo, b"<rss></rss>")

    def test_ne_szhatoe_kak_est(self):
        self.assertEqual(zapusk(Podmena(Otklik(b"plain"))).telo, b"plain")

    def test_bityy_gzip_kak_est(self):
        self.assertEqual(zapusk(Podmena(Otklik(b"\x1f\x8bne gzip"))).telo, b"\x1f\x8bne gzip")

    def test_rovno_10_mb_chitaetsya(self):
        self.assertEqual(len(zapusk(Podmena(Nuli(10 * MB))).telo), 10485760)

    def test_bolshe_10_mb_otkaz(self):
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(Podmena(Nuli(10 * MB + 1)))
        self.assertEqual(str(e.exception), "ответ больше 10 МБ")

    def test_bomba_pri_raspakovke_otkaz(self):
        bomba = gzip.compress(b"\0" * (11 * MB))
        self.assertLess(len(bomba), 100 * 1024)
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(Podmena(Otklik(bomba, content_encoding="gzip")))
        self.assertEqual(str(e.exception), "ответ после распаковки больше 10 МБ")

    def test_telo_oshibki_http_tozhe_s_predelom(self):
        oshibka = urllib.error.HTTPError("https://primer.example/lenta", 404, "Not Found", zagolovki(),
                                         Nuli(11 * MB))
        with self.assertRaises(OshibkaZaprosa):
            zapusk(Podmena(oshibka))

    def test_oshibka_http_kak_otvet(self):
        oshibka = urllib.error.HTTPError("https://primer.example/lenta", 404, "Not Found", zagolovki(),
                                         io.BytesIO(b"net"))
        o = zapusk(Podmena(oshibka))
        self.assertEqual((o.status, o.telo), (404, b"net"))


class Status(unittest.TestCase):
    def test_otvet_bez_statusa_otkaz(self):
        podmena = Podmena(Otklik(b"secret", status=None))
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(podmena)
        self.assertEqual(str(e.exception), "https://primer.example/lenta: ответ без кода статуса")
        self.assertEqual(len(podmena.zaprosy), 1)

    def test_ne_http_shema_otkaz_do_zaprosa(self):
        podmena = Podmena()
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(podmena, "file:///etc/passwd")
        self.assertEqual(str(e.exception), "адрес не http(s): file:///etc/passwd")
        self.assertEqual(podmena.zaprosy, [])

    def test_5xx_povtor_odin_raz(self):
        podmena = Podmena(Otklik(b"", status=503), Otklik(b"ok"))
        self.assertEqual(zapusk(podmena).telo, b"ok")

    def test_204_kak_est(self):
        self.assertEqual(zapusk(Podmena(Otklik(b"", status=204))).status, 204)

    def test_obshchiy_srok_60_sekund(self):
        with mock.patch.object(zaprosy.time, "monotonic", side_effect=itertools.count(0, 30)):
            with self.assertRaises(OshibkaZaprosa) as e:
                zapusk(Podmena(Otklik(b"medlenno")))
        self.assertEqual(str(e.exception), "общий срок запроса 60 с истёк")

    def test_srok_derzhit_potok_po_baytu(self):
        potok = PoBaytu()
        with mock.patch.object(zaprosy.time, "monotonic", side_effect=itertools.count(0, 1)):
            with self.assertRaises(OshibkaZaprosa) as e:
                zapusk(Podmena(potok))
        self.assertEqual(str(e.exception), "общий срок запроса 60 с истёк")
        self.assertEqual(potok.chteniy, 58)


class PoBaytu:
    """Ответ, у которого есть read1 по байту; полный read звать нельзя — он ждал бы весь кусок."""

    status = 200

    def __init__(self):
        self.headers, self.chteniy = zagolovki(), 0

    def read1(self, n=-1):
        self.chteniy += 1
        return b"x"

    def read(self, n=-1):
        raise AssertionError("read вместо read1")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


STATUS_OK = b"HTTP/1.1 200 OK\r\n"

VIDY = {
    "telo": (STATUS_OK + b"Content-Length: 1000\r\n\r\n", b"x"),
    "chunked": (STATUS_OK + b"Transfer-Encoding: chunked\r\n\r\n", b"0"),
    "status": (b"", None),
    "zagolovki": (STATUS_OK, b"X-Kaplya: a\r\n"),
    "404": (b"HTTP/1.1 404 Not Found\r\nContent-Length: 1000\r\n\r\n", b"x"),
}


class Kapelnica(socketserver.BaseRequestHandler):
    """Читает запрос и отдаёт ответ вида server.vid по капле, пока тест не погасит сервер."""

    def handle(self):
        nachalo, kaplya = VIDY[self.server.vid]
        kapli = itertools.cycle(b"HTTP/1.1 200 OK") if kaplya is None else itertools.repeat(kaplya)
        try:
            self.request.recv(65536)
            self.request.sendall(nachalo)
            for k in kapli:
                self.request.sendall(bytes([k]) if isinstance(k, int) else k)
                if self.server.stop.wait(0.5):
                    return
        except OSError:
            pass


class ProbaSroka(unittest.TestCase):
    """Шов «Проба срока на локальном сервере»: срок 3 с обрывает запрос за 3–4 с по настоящим часам
    при каждом виде ответа."""

    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Kapelnica)
        self.server.daemon_threads = True
        self.server.stop = threading.Event()
        self.potok = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.potok.start()

    def tearDown(self):
        self.server.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.potok.join(5)

    def obryv(self, vid):
        self.server.vid = vid
        url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        nachalo = time.monotonic()
        with mock.patch.object(zaprosy, "OBSHCHIY_SROK_SEKUND", 3):
            with self.assertRaises(OshibkaZaprosa) as e:
                zaprosy.poluchit(url)
        proshlo = time.monotonic() - nachalo
        self.assertEqual(str(e.exception), "общий срок запроса 3 с истёк")
        self.assertGreaterEqual(proshlo, 2.9)
        self.assertLessEqual(proshlo, 4.0)
        self.assertEqual([t for t in threading.enumerate() if isinstance(t, threading.Timer)], [])
        print(f"\nпроба срока, {vid}: обрыв через {proshlo:.2f} с", file=sys.stderr)

    def test_telo_s_content_length_po_baytu(self):
        self.obryv("telo")

    def test_chunked_medlennaya_stroka_razmera(self):
        self.obryv("chunked")

    def test_medlennaya_stroka_statusa(self):
        self.obryv("status")

    def test_medlennye_zagolovki(self):
        self.obryv("zagolovki")

    def test_404_medlennoe_telo(self):
        self.obryv("404")


class Celyy(socketserver.BaseRequestHandler):
    """Отдаёт server.otvet целиком и закрывает соединение."""

    def handle(self):
        try:
            self.request.recv(65536)
            self.request.sendall(self.server.otvet)
        except OSError:
            pass


class PolnyyOtvet(unittest.TestCase):
    """Полный ответ по настоящему сокету: тело с Content-Length дочитано — http.client сам закрыл
    сокет, следующий круг чтения не должен трогать его (живой сбор, WinError 10038, починка-3)."""

    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Celyy)
        self.server.daemon_threads = True
        self.potok = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.potok.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.potok.join(5)

    def poluchit(self, otvet):
        self.server.otvet = otvet
        return zaprosy.poluchit(f"http://127.0.0.1:{self.server.server_address[1]}/")

    def test_content_length(self):
        o = self.poluchit(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nprivet")
        self.assertEqual((o.status, o.telo), (200, b"prive"))

    def test_chunked(self):
        o = self.poluchit(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nprive\r\n0\r\n\r\n")
        self.assertEqual((o.status, o.telo), (200, b"prive"))

    def test_404_s_content_length(self):
        o = self.poluchit(b"HTTP/1.1 404 Not Found\r\nContent-Length: 3\r\n\r\nnet")
        self.assertEqual((o.status, o.telo), (404, b"net"))


class Pereadresaciya(unittest.TestCase):
    KLYUCHI = {"Authorization": "Bearer proba", "X-API-Key": "proba2"}

    def test_drugoy_host_snimaet_klyuchi(self):
        podmena = Podmena(pereadresaciya("https://api.github.com/a", "https://drugoy.example/b"), Otklik(b"ok"))
        o = zapusk(podmena, "https://api.github.com/a", zagolovki=dict(self.KLYUCHI))
        self.assertEqual(o.telo, b"ok")
        vtoroy = podmena.zaprosy[1]
        self.assertEqual(vtoroy.full_url, "https://drugoy.example/b")
        self.assertIsNone(vtoroy.get_header("Authorization"))
        self.assertIsNone(vtoroy.get_header("X-api-key"))
        self.assertEqual(vtoroy.get_header("User-agent"), zaprosy.BRAUZER)

    def test_tot_zhe_host_klyuchi_ostayutsya(self):
        podmena = Podmena(pereadresaciya("https://api.github.com/a", "/b", 301), Otklik(b"ok"))
        zapusk(podmena, "https://api.github.com/a", zagolovki=dict(self.KLYUCHI))
        vtoroy = podmena.zaprosy[1]
        self.assertEqual(vtoroy.full_url, "https://api.github.com/b")
        self.assertEqual(vtoroy.get_header("Authorization"), "Bearer proba")
        self.assertEqual(vtoroy.get_header("X-api-key"), "proba2")

    def test_pereadresaciya_na_file_otkaz(self):
        podmena = Podmena(pereadresaciya("https://primer.example/lenta", "file:///etc/passwd"))
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(podmena)
        self.assertEqual(str(e.exception), "адрес не http(s): file:///etc/passwd")

    def test_bolshe_10_pereadresaciy_otkaz(self):
        podmena = Podmena(*[pereadresaciya("https://primer.example/lenta", "/lenta") for _ in range(11)])
        with self.assertRaises(OshibkaZaprosa) as e:
            zapusk(podmena)
        self.assertEqual(str(e.exception), "https://primer.example/lenta: больше 10 переадресаций")

    def test_proverka_zovetsya_na_kazhdyy_adres(self):
        vidennye = []
        podmena = Podmena(pereadresaciya("https://primer.example/a", "https://drugoy.example/b"), Otklik(b"ok"))
        zapusk(podmena, "https://primer.example/a", proverka=vidennye.append)
        self.assertEqual(vidennye, ["https://primer.example/a", "https://drugoy.example/b"])


if __name__ == "__main__":
    unittest.main()
