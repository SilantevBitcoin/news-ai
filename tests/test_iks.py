"""Шов «Сбор X»: бережный сбор с подменённым HTTP, подменёнными паузой и часами.

Настоящего ожидания нет: пауза пишет секунды в список и двигает подменённые часы. Записанные ответы
FxTwitter 13.11.2026 (живые запросы по одному, пауза 5 секунд): fx_posty_404.json — посты
несуществующего аккаунта, fx_profil_404.json — его профиль, fx_profil_200.json — профиль gdb;
fx_200.json — ответ 11.11.2026 (см. test_sbor). Правок образцов нет.
"""
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from newsai import iks
from newsai.progon import Period
from newsai.zaprosy import OshibkaZaprosa, Otvet

UTC = timezone.utc
ZAPISI = Path(__file__).parent / "zapisi"
OKNO = Period(datetime(2026, 11, 6, tzinfo=UTC), datetime(2026, 11, 11, 2, 7, tzinfo=UTC))
FX = "api.fxtwitter.com/2/profile/"
LENTA = "syndication.twitter.com/srv/timeline-profile/screen-name/"


def zapis(imya, status=200):
    return Otvet(status, (ZAPISI / imya).read_bytes(), {"content-type": "application/json"})


class Vremya:
    """Подменённые часы и пауза: пауза копит секунды и двигает часы."""

    def __init__(self):
        self.t = 1000.0
        self.pauzy = []

    def chasy(self):
        return self.t

    def spat(self, sekundy):
        self.pauzy.append(sekundy)
        self.t += sekundy


class Ochered:
    """Подменённый HTTP: ответы строго по порядку; каждый запрос сверяется с ожидаемой подстрокой адреса."""

    def __init__(self, test, otvety, vremya=None, dlitsya=0):
        self.test = test
        self.otvety = list(otvety)
        self.zaprosy = []
        self.potoki = set()
        self.vremya = vremya
        self.dlitsya = dlitsya

    def __call__(self, url, zagolovki=None):
        self.zaprosy.append(url)
        self.potoki.add(threading.get_ident())
        self.test.assertTrue(self.otvety, f"лишний запрос: {url}")
        chast, o = self.otvety.pop(0)
        self.test.assertIn(chast, url)
        if self.vremya:
            self.vremya.t += self.dlitsya
        if isinstance(o, Exception):
            raise o
        return o


def ist(*akkaunty):
    return {"imya": "X", "vid": "x", "akkaunty": list(akkaunty)}


class BerezhnyySbor(unittest.TestCase):
    def setUp(self):
        self.vremya = Vremya()
        p = mock.patch.object(iks, "klyuch_twitterapi", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def sobrat(self, set_, *akkaunty, okno=OKNO):
        return iks.sobrat(ist(*akkaunty), okno, set_, self.vremya.spat, self.vremya.chasy)

    def test_po_odnomu_v_poryadke_bez_potokov(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(204)), (FX + "b/statuses", Otvet(204)),
                              (FX + "c/statuses", Otvet(204))])
        posty, otchet = self.sobrat(set_, "a", "b", "c")
        self.assertEqual([u.split("?")[0] for u in set_.zaprosy],
                         ["https://api.fxtwitter.com/2/profile/a/statuses",
                          "https://api.fxtwitter.com/2/profile/b/statuses",
                          "https://api.fxtwitter.com/2/profile/c/statuses"])
        self.assertEqual(set_.potoki, {threading.get_ident()})
        self.assertEqual(self.vremya.pauzy, [2, 2])
        self.assertEqual((posty, otchet), ([], {"puti": {"FxTwitter": 3}, "molchat": {}}))

    def test_pauza_mezhdu_stranicami_fx(self):
        okno = Period(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 11, 2, 7, tzinfo=UTC))
        set_ = Ochered(self, [(FX + "AnthropicAI/statuses", zapis("fx_200.json")),
                              (FX + "AnthropicAI/statuses", Otvet(204))])
        posty, otchet = self.sobrat(set_, "AnthropicAI", okno=okno)
        self.assertTrue(set_.zaprosy[1].endswith("&cursor=b"))
        self.assertEqual(self.vremya.pauzy, [2])
        self.assertEqual(len(posty), 4)
        self.assertEqual(otchet["puti"], {"FxTwitter": 1})

    def test_429_retry_after_i_odin_povtor(self):
        for zagolovki, zhdat in (({"retry-after": "7"}, 7), ({}, 30), ({"retry-after": "600"}, 60),
                                 ({"retry-after": "0"}, 2), ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, 30)):
            with self.subTest(zagolovki=zagolovki):
                self.vremya = Vremya()
                set_ = Ochered(self, [(FX + "a/statuses", Otvet(429, b"", zagolovki)),
                                      (FX + "a/statuses", Otvet(204))])
                _, otchet = self.sobrat(set_, "a")
                self.assertEqual(self.vremya.pauzy, [zhdat])
                self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "molchat": {}})

    def test_vtoroy_429_otkaz_puti(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(429)), (FX + "a/statuses", Otvet(429)),
                              (LENTA + "a", Otvet(503))])
        _, otchet = self.sobrat(set_, "a")
        self.assertEqual(self.vremya.pauzy, [30, 2])
        self.assertEqual(otchet["molchat"],
                         {"a": "FxTwitter: FxTwitter 429 и после повтора; лента X: лента X 503"})

    def test_429_lenty_vyklyuchaet_eyo_do_konca_sbora(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(503)), (LENTA + "a", Otvet(429)),
                              (LENTA + "a", Otvet(429)), (FX + "b/statuses", Otvet(503)),
                              (FX + "c/statuses", Otvet(204))])
        _, otchet = self.sobrat(set_, "a", "b", "c")
        self.assertFalse([u for u in set_.zaprosy[3:] if "syndication" in u])
        self.assertEqual(self.vremya.pauzy, [2, 30, 2, 2])
        self.assertEqual(otchet["molchat"], {
            "a": "FxTwitter: FxTwitter 503; лента X: лента X 429 и после повтора — выключена до конца сбора",
            "b": "FxTwitter: FxTwitter 503; лента X: пропущена — 429 раньше в этом сборе"})
        self.assertEqual(otchet["puti"], {"FxTwitter": 1})

    def test_429_lenty_povtor_200_lenta_ostayotsya(self):
        html = (ZAPISI / "syndication.html").read_bytes()
        set_ = Ochered(self, [(FX + "AnthropicAI/statuses", Otvet(503)), (LENTA + "AnthropicAI", Otvet(429)),
                              (LENTA + "AnthropicAI", Otvet(200, html, {"content-type": "text/html"})),
                              (FX + "b/statuses", Otvet(503)), (LENTA + "b", Otvet(503))])
        _, otchet = self.sobrat(set_, "AnthropicAI", "b")
        self.assertEqual(self.vremya.pauzy, [2, 30, 2, 2])
        self.assertEqual(otchet["puti"], {"лента X": 1})
        self.assertEqual(otchet["molchat"], {"b": "FxTwitter: FxTwitter 503; лента X: лента X 503"})

    def test_429_twitterapi_tozhe_s_povtorom(self):
        tvity = (b'{"status":"success","data":{"tweets":[{"id":"777","text":"proba",'
                 b'"createdAt":"Tue Nov 10 11:33:49 +0000 2026"}]}}')
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(503)), (LENTA + "a", Otvet(503)),
                              ("api.twitterapi.io", Otvet(429, b"", {"retry-after": "5"})),
                              ("api.twitterapi.io", Otvet(200, tvity, {"content-type": "application/json"}))])
        with mock.patch.object(iks, "klyuch_twitterapi", return_value="k"):
            posty, otchet = self.sobrat(set_, "a")
        self.assertEqual(self.vremya.pauzy, [2, 2, 5])
        self.assertEqual([k.url for k in posty], ["https://x.com/a/status/777"])
        self.assertEqual(otchet["puti"], {"twitterapi.io": 1})

    def test_404_povtor_dal_204_sboya_net(self):
        set_ = Ochered(self, [(FX + "gdb/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "gdb/statuses", Otvet(204))])
        posty, otchet = self.sobrat(set_, "gdb")
        self.assertEqual(self.vremya.pauzy, [5])
        self.assertEqual((posty, otchet), ([], {"puti": {"FxTwitter": 1}, "molchat": {}}))

    def test_404_povtor_dal_200_posty_est(self):
        set_ = Ochered(self, [(FX + "AnthropicAI/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "AnthropicAI/statuses", zapis("fx_200.json"))])
        posty, otchet = self.sobrat(set_, "AnthropicAI")
        self.assertEqual(self.vremya.pauzy, [5])
        self.assertEqual(len(posty), 3)
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "molchat": {}})

    def test_404_i_profil_404_akkaunt_ne_nayden(self):
        set_ = Ochered(self, [(FX + "net/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "net/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "net", zapis("fx_profil_404.json", 404)),
                              (FX + "b/statuses", Otvet(204))])
        _, otchet = self.sobrat(set_, "net", "b")
        self.assertEqual(set_.zaprosy[2], "https://api.fxtwitter.com/2/profile/net")
        self.assertFalse([u for u in set_.zaprosy if "syndication" in u])
        self.assertEqual(self.vremya.pauzy, [5, 2, 2])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "molchat": {"net": "FxTwitter: аккаунт не найден"}})

    def test_404_i_profil_200_dalshe_po_cepochke(self):
        set_ = Ochered(self, [(FX + "gdb/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "gdb/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "gdb", zapis("fx_profil_200.json")),
                              (LENTA + "gdb", Otvet(503))])
        _, otchet = self.sobrat(set_, "gdb")
        self.assertEqual(self.vremya.pauzy, [5, 2, 2])
        self.assertEqual(otchet["molchat"], {"gdb": "FxTwitter: FxTwitter 404 на постах после повтора, "
                                                    "профиль 200; лента X: лента X 503"})

    def test_404_i_profil_oshibka_dalshe_po_cepochke(self):
        set_ = Ochered(self, [(FX + "gdb/statuses", Otvet(404)), (FX + "gdb/statuses", Otvet(404)),
                              (FX + "gdb", OshibkaZaprosa("timed out")), (LENTA + "gdb", Otvet(503))])
        _, otchet = self.sobrat(set_, "gdb")
        self.assertEqual(otchet["molchat"], {"gdb": "FxTwitter: FxTwitter 404 на постах после повтора, "
                                                    "профиль ошибка (timed out); лента X: лента X 503"})

    def test_obshchiy_srok_5_minut(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(204)), (FX + "b/statuses", Otvet(204)),
                              (FX + "c/statuses", Otvet(204))], self.vremya, dlitsya=100)
        _, otchet = self.sobrat(set_, "a", "b", "c", "d", "e")
        self.assertEqual(len(set_.zaprosy), 3)
        self.assertEqual(self.vremya.pauzy, [2, 2])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 3},
                                  "molchat": {"d": "не успели: общий срок X", "e": "не успели: общий срок X"}})

    def test_ozhidanie_ne_vyhodit_za_ostatok_sroka(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(429, b"", {"retry-after": "60"}))],
                       self.vremya, dlitsya=280)
        _, otchet = self.sobrat(set_, "a", "b")
        self.assertEqual(self.vremya.pauzy, [])
        self.assertEqual(len(set_.zaprosy), 1)
        self.assertEqual(otchet, {"puti": {}, "molchat": {"a": "не успели: общий срок X",
                                                          "b": "не успели: общий срок X"}})

    def test_summa_ozhidaniy_v_predelah_sroka(self):
        akkaunty = [f"a{i}" for i in range(10)]
        otvety = [(FX + "a0/", Otvet(429)), (FX + "a0/", Otvet(429)), (LENTA + "a0", Otvet(429)),
                  (LENTA + "a0", Otvet(429))]
        for a in akkaunty[1:]:
            otvety += [(FX + a + "/", Otvet(429)), (FX + a + "/", Otvet(429))]
        set_ = Ochered(self, otvety)
        _, otchet = self.sobrat(set_, *akkaunty)
        self.assertEqual(self.vremya.pauzy, [30, 2, 30] + [2, 30] * 7 + [2])
        self.assertEqual(sum(self.vremya.pauzy), 288)
        self.assertEqual([a for a, p in otchet["molchat"].items() if p == "не успели: общий срок X"],
                         ["a8", "a9"])


if __name__ == "__main__":
    unittest.main()
