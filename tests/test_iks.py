"""Шов «Сбор X»: бережный сбор с подменённым HTTP, подменёнными паузой и часами.

Настоящего ожидания нет: пауза пишет секунды в список и двигает подменённые часы. Записанные ответы
FxTwitter 13.11.2026 (живые запросы по одному, пауза 5 секунд): fx_posty_404.json — посты
несуществующего аккаунта, fx_profil_404.json — его профиль, fx_profil_200.json — профиль gdb;
fx_200.json — ответ 11.11.2026 (см. test_sbor); fx_bez_since_200.json — ответ 14.11.2026 без since
(проход 2, Р71). Правок образцов нет.
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
    """Правила одного прохода: повторные проходы выключены (PROHODOV = 1), их ловит PovtornyeProhody."""

    def setUp(self):
        self.vremya = Vremya()
        for p in (mock.patch.object(iks, "klyuch_twitterapi", return_value=None),
                  mock.patch.object(iks, "PROHODOV", 1)):
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
        self.assertEqual((posty, otchet), ([], {"puti": {"FxTwitter": 3}, "prohody": {}, "molchat": {}}))

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
                self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {}, "molchat": {}})

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
        self.assertEqual((posty, otchet), ([], {"puti": {"FxTwitter": 1}, "prohody": {}, "molchat": {}}))

    def test_404_povtor_dal_200_posty_est(self):
        set_ = Ochered(self, [(FX + "AnthropicAI/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "AnthropicAI/statuses", zapis("fx_200.json"))])
        posty, otchet = self.sobrat(set_, "AnthropicAI")
        self.assertEqual(self.vremya.pauzy, [5])
        self.assertEqual(len(posty), 3)
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {}, "molchat": {}})

    def test_404_i_profil_404_akkaunt_ne_nayden(self):
        set_ = Ochered(self, [(FX + "net/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "net/statuses", zapis("fx_posty_404.json", 404)),
                              (FX + "net", zapis("fx_profil_404.json", 404)),
                              (FX + "b/statuses", Otvet(204))])
        _, otchet = self.sobrat(set_, "net", "b")
        self.assertEqual(set_.zaprosy[2], "https://api.fxtwitter.com/2/profile/net")
        self.assertFalse([u for u in set_.zaprosy if "syndication" in u])
        self.assertEqual(self.vremya.pauzy, [5, 2, 2])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {},
                                  "molchat": {"net": "FxTwitter: профиль 404 один раз"}})

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

    def test_obshchiy_srok_8_minut(self):
        set_ = Ochered(self, [(FX + a + "/statuses", Otvet(204)) for a in "abcde"], self.vremya, dlitsya=100)
        _, otchet = self.sobrat(set_, "a", "b", "c", "d", "e", "f", "g")
        self.assertEqual(len(set_.zaprosy), 5)
        self.assertEqual(self.vremya.pauzy, [2, 2, 2, 2])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 5}, "prohody": {},
                                  "molchat": {"f": "не успели: общий срок X", "g": "не успели: общий срок X"}})

    def test_ozhidanie_ne_vyhodit_za_ostatok_sroka(self):
        set_ = Ochered(self, [(FX + "a/statuses", Otvet(429, b"", {"retry-after": "60"}))],
                       self.vremya, dlitsya=460)
        _, otchet = self.sobrat(set_, "a", "b")
        self.assertEqual(self.vremya.pauzy, [])
        self.assertEqual(len(set_.zaprosy), 1)
        self.assertEqual(otchet, {"puti": {}, "prohody": {}, "molchat": {"a": "не успели: общий срок X",
                                                          "b": "не успели: общий срок X"}})

    def test_summa_ozhidaniy_v_predelah_sroka(self):
        akkaunty = [f"a{i}" for i in range(16)]
        otvety = [(FX + "a0/", Otvet(429)), (FX + "a0/", Otvet(429)), (LENTA + "a0", Otvet(429)),
                  (LENTA + "a0", Otvet(429))]
        for a in akkaunty[1:]:
            otvety += [(FX + a + "/", Otvet(429)), (FX + a + "/", Otvet(429))]
        set_ = Ochered(self, otvety)
        _, otchet = self.sobrat(set_, *akkaunty)
        self.assertEqual(self.vremya.pauzy, [30, 2, 30] + [2, 30] * 13)
        self.assertEqual(sum(self.vremya.pauzy), 478)
        self.assertEqual([a for a, p in otchet["molchat"].items() if p == "не успели: общий срок X"],
                         ["a14", "a15"])


SINCE = "https://api.fxtwitter.com/2/profile/{}/statuses?since=1793923200"
BEZ_SINCE = "https://api.fxtwitter.com/2/profile/{}/statuses"
PROFIL = "https://api.fxtwitter.com/2/profile/{}"
SYND = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{}"


class PovtornyeProhody(unittest.TestCase):
    def setUp(self):
        self.vremya = Vremya()
        p = mock.patch.object(iks, "klyuch_twitterapi", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def sobrat(self, set_, *akkaunty):
        return iks.sobrat(ist(*akkaunty), OKNO, set_, self.vremya.spat, self.vremya.chasy)

    def test_404_v_prohode_1_i_200_v_prohode_2_bez_since(self):
        set_ = Ochered(self, [(FX + "mikeyk/statuses?since", zapis("fx_posty_404.json", 404)),
                              (FX + "mikeyk/statuses?since", zapis("fx_posty_404.json", 404)),
                              (FX + "mikeyk", zapis("fx_profil_200.json")),
                              (LENTA + "mikeyk", Otvet(429)), (LENTA + "mikeyk", Otvet(429)),
                              (FX + "mikeyk/statuses", zapis("fx_bez_since_200.json"))])
        posty, otchet = self.sobrat(set_, "mikeyk")
        self.assertEqual(set_.zaprosy, [SINCE.format("mikeyk"), SINCE.format("mikeyk"), PROFIL.format("mikeyk"),
                                        SYND.format("mikeyk"), SYND.format("mikeyk"), BEZ_SINCE.format("mikeyk")])
        self.assertEqual(self.vremya.pauzy, [5, 2, 2, 30, 60])
        self.assertEqual([(k.url, k.data.isoformat(), k.istochnik, k.akkaunt) for k in posty],
                         [("https://x.com/mikeyk/status/2120254420105806188", "2026-11-10T20:37:00+00:00", "X", "mikeyk"),
                          ("https://x.com/mikeyk/status/2119868632570462674", "2026-11-09T19:04:01+00:00", "X", "mikeyk")])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {"FxTwitter": {2: 1}}, "molchat": {}})

    def test_tri_prohoda_otvetivshiy_ne_zaprashivaetsya(self):
        set_ = Ochered(self, [(FX + "a/", Otvet(204)), (FX + "b/", Otvet(503)), (LENTA + "b", Otvet(503)),
                              (FX + "b/", Otvet(503)), (LENTA + "b", Otvet(503)),
                              (FX + "b/", OshibkaZaprosa("timed out")), (LENTA + "b", Otvet(503))])
        posty, otchet = self.sobrat(set_, "a", "b")
        self.assertEqual(set_.zaprosy, [SINCE.format("a"), SINCE.format("b"), SYND.format("b"),
                                        BEZ_SINCE.format("b"), SYND.format("b"),
                                        SINCE.format("b"), SYND.format("b")])
        self.assertEqual(self.vremya.pauzy, [2, 2, 60, 2, 60, 2])
        self.assertEqual((posty, otchet["puti"], otchet["prohody"]), ([], {"FxTwitter": 1}, {}))
        self.assertEqual(otchet["molchat"], {"b": "проход 1: FxTwitter: FxTwitter 503; лента X: лента X 503"
                                                  " | проход 2: FxTwitter: FxTwitter 503; лента X: лента X 503"
                                                  " | проход 3: FxTwitter: timed out; лента X: лента X 503"})

    def test_lenta_vyklyuchena_i_otvet_s_prohoda_3(self):
        set_ = Ochered(self, [(FX + "a/", Otvet(503)), (LENTA + "a", Otvet(429)), (LENTA + "a", Otvet(429)),
                              (FX + "a/", Otvet(503)), (FX + "a/", Otvet(204))])
        _, otchet = self.sobrat(set_, "a")
        self.assertEqual(set_.zaprosy, [SINCE.format("a"), SYND.format("a"), SYND.format("a"),
                                        BEZ_SINCE.format("a"), SINCE.format("a")])
        self.assertEqual(self.vremya.pauzy, [2, 30, 60, 60])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {"FxTwitter": {3: 1}}, "molchat": {}})

    def test_profil_404_v_dvuh_prohodah_akkaunt_ne_nayden(self):
        set_ = Ochered(self, [(FX + "net/", zapis("fx_posty_404.json", 404)),
                              (FX + "net/", zapis("fx_posty_404.json", 404)),
                              (FX + "net", zapis("fx_profil_404.json", 404)),
                              (FX + "net/", zapis("fx_posty_404.json", 404)),
                              (FX + "net/", zapis("fx_posty_404.json", 404)),
                              (FX + "net", zapis("fx_profil_404.json", 404))])
        _, otchet = self.sobrat(set_, "net")
        self.assertEqual(set_.zaprosy, [SINCE.format("net"), SINCE.format("net"), PROFIL.format("net"),
                                        BEZ_SINCE.format("net"), BEZ_SINCE.format("net"), PROFIL.format("net")])
        self.assertEqual(self.vremya.pauzy, [5, 2, 60, 5, 2])
        self.assertEqual(otchet, {"puti": {}, "prohody": {}, "molchat": {
            "net": "проход 1: FxTwitter: профиль 404 | проход 2: FxTwitter: аккаунт не найден"}})

    def test_profil_404_odin_raz_eto_sboy(self):
        set_ = Ochered(self, [(FX + "c/", Otvet(404)), (FX + "c/", Otvet(404)), (FX + "c", Otvet(404)),
                              (FX + "c/", Otvet(404)), (FX + "c/", Otvet(404)),
                              (FX + "c", zapis("fx_profil_200.json")), (LENTA + "c", Otvet(503)),
                              (FX + "c/", Otvet(503)), (LENTA + "c", Otvet(503))])
        _, otchet = self.sobrat(set_, "c")
        self.assertEqual(set_.zaprosy, [SINCE.format("c"), SINCE.format("c"), PROFIL.format("c"),
                                        BEZ_SINCE.format("c"), BEZ_SINCE.format("c"), PROFIL.format("c"),
                                        SYND.format("c"), SINCE.format("c"), SYND.format("c")])
        self.assertEqual(self.vremya.pauzy, [5, 2, 60, 5, 2, 2, 60, 2])
        self.assertEqual(otchet["molchat"], {"c": (
            "проход 1: FxTwitter: профиль 404 один раз"
            " | проход 2: FxTwitter: FxTwitter 404 на постах после повтора, профиль 200; лента X: лента X 503"
            " | проход 3: FxTwitter: FxTwitter 503; лента X: лента X 503")})

    def test_net_sroka_na_pauzu_prohoda(self):
        set_ = Ochered(self, [(FX + "a/", Otvet(503)), (LENTA + "a", Otvet(503)), (FX + "b/", Otvet(204))],
                       self.vremya, dlitsya=140)
        _, otchet = self.sobrat(set_, "a", "b")
        self.assertEqual(len(set_.zaprosy), 3)
        self.assertEqual(self.vremya.pauzy, [2, 2])
        self.assertEqual(otchet, {"puti": {"FxTwitter": 1}, "prohody": {}, "molchat": {
            "a": "FxTwitter: FxTwitter 503; лента X: лента X 503 | не успели: общий срок X"}})

    def test_srok_vyshel_posredi_prohoda_2(self):
        set_ = Ochered(self, [(FX + "a/", Otvet(503)), (LENTA + "a", Otvet(503)),
                              (FX + "b/", Otvet(503)), (LENTA + "b", Otvet(503)),
                              (FX + "a/", Otvet(503)), (LENTA + "a", Otvet(503))], self.vremya, dlitsya=70)
        _, otchet = self.sobrat(set_, "a", "b")
        self.assertEqual(set_.zaprosy[4], BEZ_SINCE.format("a"))
        self.assertEqual(self.vremya.pauzy, [2, 2, 2, 60, 2])
        self.assertEqual(otchet["molchat"], {
            "a": "проход 1: FxTwitter: FxTwitter 503; лента X: лента X 503"
                 " | проход 2: FxTwitter: FxTwitter 503; лента X: лента X 503 | не успели: общий срок X",
            "b": "FxTwitter: FxTwitter 503; лента X: лента X 503 | не успели: общий срок X"})


if __name__ == "__main__":
    unittest.main()
