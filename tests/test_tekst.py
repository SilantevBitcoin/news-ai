"""Шов «`tekst` с подменённым резолвом и urlopen».

Ловит: отказ не-http(s), приватных, loopback, link-local, reserved и multicast адресов, в том числе
после переадресации; ответ без статуса; снятие разметки; предел 20 000 знаков.
"""
import io
import ipaddress
import socket
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from newsai import tekst, zaprosy


def zagolovki(**z):
    m = Message()
    for k, v in z.items():
        m[k.replace("_", "-")] = v
    return m


class Otklik(io.BytesIO):
    def __init__(self, telo=b"", status=200, **z):
        super().__init__(telo)
        self.status = status
        self.headers = zagolovki(**z)


class Podmena:
    def __init__(self, *otvety):
        self.otvety = list(otvety)
        self.adresa = []

    def __call__(self, zapros, timeout):
        self.adresa.append(zapros.full_url)
        o = self.otvety.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def rezolv(karta):
    """Подмена socket.getaddrinfo: хост → IP из карты."""
    def getaddrinfo(host, port, *a, **kw):
        if host not in karta:
            raise socket.gaierror("не найден")
        ip = karta[host]
        semeystvo = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(semeystvo, socket.SOCK_STREAM, 6, "", (ip, port))]
    return getaddrinfo


PUBLICHNYY = {"primer.example": "93.184.216.34"}


def zapusk(url, podmena, karta=None):
    with mock.patch.object(zaprosy, "urlopen", podmena), \
            mock.patch.object(socket, "getaddrinfo", rezolv(karta or PUBLICHNYY)):
        return tekst.tekst(url)


def html(telo):
    return Otklik(telo, content_type="text/html; charset=utf-8")


class Tekst(unittest.TestCase):
    def test_razmetka_snimaetsya(self):
        stranica = (b"<html><head><title>t</title><script>var a=1;</script></head><body><nav>menu</nav>"
                    b"<article><h1>Title</h1><p>" + b"Body text. " * 40 + b"</p></article></body></html>")
        t = zapusk("https://primer.example/b", Podmena(html(stranica)))
        self.assertTrue(t.startswith("Title\n\nBody text. Body text."))
        self.assertNotIn("menu", t)
        self.assertNotIn("var a", t)

    def test_limit_20000(self):
        stranica = b"<html><body><p>" + b"x" * 30000 + b"</p></body></html>"
        self.assertEqual(len(zapusk("https://primer.example/b", Podmena(html(stranica)))), 20000)

    def test_ne_otkrylas(self):
        oshibka = urllib.error.HTTPError("https://primer.example/b", 404, "Not Found", zagolovki(), io.BytesIO())
        with self.assertRaises(SystemExit) as e:
            zapusk("https://primer.example/b", Podmena(oshibka))
        self.assertEqual(str(e.exception), "newsai tekst: страница не открылась: ответ 404")

    def test_otvet_bez_statusa_otkaz(self):
        with self.assertRaises(SystemExit) as e:
            zapusk("https://primer.example/b", Podmena(Otklik(b"x", status=None)))
        self.assertEqual(str(e.exception),
                         "newsai tekst: страница не открылась: https://primer.example/b: ответ без кода статуса")


class Adresa(unittest.TestCase):
    def otkaz(self, url, karta=None):
        podmena = Podmena(html(b"<p>tayna</p>"))
        with self.assertRaises(SystemExit) as e:
            zapusk(url, podmena, karta)
        self.assertEqual(podmena.adresa, [])
        return str(e.exception)

    def test_ne_http_shemy(self):
        for url in ("file:///etc/passwd", "ftp://primer.example/a", "data:text/plain,hello"):
            self.assertEqual(self.otkaz(url), f"newsai tekst: страница не открылась: адрес не http(s): {url}")

    def test_zakrytye_adresa(self):
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "224.0.0.1",
                   "240.0.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1"):
            self.assertEqual(self.otkaz("http://vnutri.example/", {"vnutri.example": ip}),
                             f"newsai tekst: страница не открылась: vnutri.example: не публичный адрес "
                             f"{ipaddress.ip_address(ip)}", ip)

    def test_ip_v_adrese(self):
        self.assertEqual(self.otkaz("http://127.0.0.1:8080/", {"127.0.0.1": "127.0.0.1"}),
                         "newsai tekst: страница не открылась: 127.0.0.1: не публичный адрес 127.0.0.1")

    def test_ne_rezolvitsya(self):
        self.assertEqual(self.otkaz("https://net-takogo.example/"),
                         "newsai tekst: страница не открылась: net-takogo.example: не резолвится (не найден)")

    def test_pereadresaciya_na_vnutrenniy_adres(self):
        perehod = urllib.error.HTTPError("https://primer.example/a", 302, "Found",
                                         zagolovki(Location="http://vnutri.example/admin"), io.BytesIO())
        podmena = Podmena(perehod, html(b"<p>tayna</p>"))
        with self.assertRaises(SystemExit) as e:
            zapusk("https://primer.example/a", podmena, {**PUBLICHNYY, "vnutri.example": "192.168.0.10"})
        self.assertEqual(str(e.exception),
                         "newsai tekst: страница не открылась: vnutri.example: не публичный адрес 192.168.0.10")
        self.assertEqual(podmena.adresa, ["https://primer.example/a"])

    def test_pereadresaciya_na_publichnyy_adres(self):
        perehod = urllib.error.HTTPError("https://primer.example/a", 301, "Moved",
                                         zagolovki(Location="https://drugoy.example/b"), io.BytesIO())
        podmena = Podmena(perehod, Otklik(b"plain text", content_type="text/plain"))
        t = zapusk("https://primer.example/a", podmena, {**PUBLICHNYY, "drugoy.example": "8.8.8.8"})
        self.assertEqual(t, "plain text")
        self.assertEqual(podmena.adresa, ["https://primer.example/a", "https://drugoy.example/b"])


if __name__ == "__main__":
    unittest.main()
