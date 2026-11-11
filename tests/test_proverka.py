"""Шов «`proverit` на записанных файлах прогона, выпуска, сбоя, состояния и события заявки».

Образцы — tests/proverka/; негодные варианты — их копии с одной правкой во временной папке проекта.
Ожидаемый вывод — литералами.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from newsai import __main__ as tochka, nastrojki, proverka, sostoyanie

OBRAZCY = Path(__file__).resolve().parent / "proverka"
SEYCHAS = datetime(2026, 11, 11, 2, 20, tzinfo=timezone.utc)


class SObrazcami(unittest.TestCase):
    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.addCleanup(self._papka.cleanup)
        self.papka = Path(self._papka.name)
        for imya in ("progon.json", "vypusk.json", "sostoyanie.json", "sobytie.json", "fayly.tsv"):
            shutil.copy(OBRAZCY / imya, self.papka / imya)
        self.puti = {"vypusk": self.papka / "vypusk.json", "sboj": self.papka / "sboj.json",
                     "progon": self.papka / "progon.json", "sostoyanie_put": self.papka / "sostoyanie.json"}

    def json(self, imya: str):
        return json.loads((self.papka / imya).read_text(encoding="utf-8"))

    def zapisat(self, imya: str, dannye) -> None:
        tekst = dannye if isinstance(dannye, str) else json.dumps(dannye, ensure_ascii=False)
        (self.papka / imya).write_text(tekst, encoding="utf-8")

    def zapusk(self, zayavka=False, **kw):
        """Без заявки — режим агента со своим progon.json; с заявкой — публикатор: progon — его сбор,
        очищенный выпуск — chistyy.json."""
        argumenty = dict(self.puti, seychas=SEYCHAS)
        if zayavka:
            argumenty.update(zayavka=self.papka / "sobytie.json", fayly=self.papka / "fayly.tsv",
                             progon=OBRAZCY / "sbor-publikatora.json", vyhod=self.papka / "chistyy.json")
        argumenty.update(kw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            kod = proverka.proverit(**argumenty)
        return kod, out.getvalue()


class Prohodit(SObrazcami):
    def test_vypusk_bez_sobytiya(self):
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))

    def test_vypusk_s_sobytiem(self):
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_sboj_bez_fayla_progona(self):
        (self.papka / "vypusk.json").unlink()
        (self.papka / "progon.json").unlink()
        shutil.copy(OBRAZCY / "sboj.json", self.papka / "sboj.json")
        self.zapisat("fayly.tsv", "added\tdannye/sboj.json\n")
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_sboj_s_faylom_progona(self):
        (self.papka / "vypusk.json").unlink()
        shutil.copy(OBRAZCY / "sboj.json", self.papka / "sboj.json")
        shutil.copy(OBRAZCY / "fayly-sboj.tsv", self.papka / "fayly.tsv")
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_pustoy_vypusk(self):
        self.zapisat("vypusk.json", {"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))


class Zayavka(SObrazcami):
    """Чужая заявка — код 3, данные дальше не проверяются."""

    def pravka(self, *put, znachenie):
        s = self.json("sobytie.json")
        d = s
        for k in put[:-1]:
            d = d[k]
        d[put[-1]] = znachenie
        self.zapisat("sobytie.json", s)

    def test_kazhdoe_uslovie(self):
        sluchai = [
            (("pull_request", "head", "repo", "fork"), True, "заявка: ветка заявки не из этого репозитория"),
            (("pull_request", "head", "repo", "full_name"), "chuzhoy/news-ai",
             "заявка: ветка заявки не из этого репозитория"),
            (("pull_request", "head", "repo"), None, "заявка: ветка заявки не из этого репозитория"),
            (("pull_request", "head", "ref"), "vypusk-2026", "заявка: имя ветки не claude/…"),
            (("pull_request", "head", "ref"), "claude/../main", "заявка: имя ветки не claude/…"),
            (("pull_request", "head", "ref"), "claude/a b", "заявка: имя ветки не claude/…"),
            (("pull_request", "base", "ref"), "claude/drugaya", "заявка: база заявки — не основная ветка"),
            (("pull_request", "user", "login"), "kto-to", "заявка: автор заявки — не владелец репозитория"),
            (("action",), "synchronize", "заявка: событие — не открытие заявки"),
        ]
        for put, znachenie, stroka in sluchai:
            with self.subTest(put=put, znachenie=znachenie):
                shutil.copy(OBRAZCY / "sobytie.json", self.papka / "sobytie.json")
                self.pravka(*put, znachenie=znachenie)
                self.assertEqual(self.zapusk(zayavka=True),
                                 (3, f"{stroka}\nчужая заявка: закрыть без публикации\n"))

    def test_chuzhaya_s_plohimi_dannymi_tolko_zayavka(self):
        self.pravka("pull_request", "user", "login", znachenie="kto-to")
        self.zapisat("vypusk.json", "не json")
        self.assertEqual(self.zapusk(zayavka=True),
                         (3, "заявка: автор заявки — не владелец репозитория\n"
                             "чужая заявка: закрыть без публикации\n"))

    def test_ne_sobytie_zayavki(self):
        self.zapisat("sobytie.json", {"action": "opened"})
        self.assertEqual(self.zapusk(zayavka=True),
                         (3, "заявка: событие — не заявка на слияние\nчужая заявка: закрыть без публикации\n"))


class Fayly(SObrazcami):
    def proverit_spisok(self, spisok: str, stroki: str):
        self.zapisat("fayly.tsv", spisok)
        self.assertEqual(self.zapusk(zayavka=True), (1, stroki + "проверки не прошли: файлы\n"))

    def test_lishniy_fayl(self):
        self.proverit_spisok("added\tdannye/progon.json\nadded\tdannye/vypusk.json\nmodified\tnewsai/sbor.py\n",
                             "файлы: лишний файл в заявке: newsai/sbor.py\n")

    def test_udalenie_i_pereimenovanie(self):
        self.proverit_spisok("added\tdannye/progon.json\nmodified\tdannye/vypusk.json\nremoved\tdannye/sboj.json\n",
                             "файлы: dannye/sboj.json: изменение вида «removed», можно только добавить или изменить\n")
        self.proverit_spisok("renamed\tdannye/progon.json\nadded\tdannye/vypusk.json\n",
                             "файлы: dannye/progon.json: изменение вида «renamed», можно только добавить или изменить\n")

    def test_vypusk_i_sboj_vmeste(self):
        self.proverit_spisok("added\tdannye/progon.json\nadded\tdannye/vypusk.json\nadded\tdannye/sboj.json\n",
                             "файлы: в заявке и файл выпуска, и файл сбоя\n")

    def test_net_faylov_dannyh(self):
        self.proverit_spisok("", "файлы: в заявке нет файлов данных\n")

    def test_vypusk_bez_progona_mozhno(self):
        self.zapisat("fayly.tsv", "added\tdannye/vypusk.json\n")
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_bolshe_2_mb(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["zagolovok"] = "я" * 300
        tekst = json.dumps(v, ensure_ascii=False)
        self.zapisat("vypusk.json", tekst + " " * (2 * 1024 * 1024 - len(tekst.encode("utf-8")) + 1))
        self.assertEqual(self.zapusk(zayavka=True),
                         (1, "файлы: dannye/vypusk.json больше 2 МБ\nпроверки не прошли: файлы\n"))

    def test_rovno_2_mb_mozhno(self):
        put = self.papka / "vypusk.json"
        telo = put.read_bytes()
        put.write_bytes(telo + b" " * (2 * 1024 * 1024 - len(telo)))
        self.assertEqual((self.papka / "vypusk.json").stat().st_size, 2097152)
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))


class Shema(SObrazcami):
    def proverit_vypusk(self, vypusk, stroka: str, nazvanie="схема"):
        self.zapisat("vypusk.json", vypusk)
        self.assertEqual(self.zapusk(), (1, f"{stroka}\nпроверки не прошли: {nazvanie}\n"))

    def test_lishnee_pole(self):
        v = self.json("vypusk.json")
        v["sayty"][0]["citata"] = "цитата"
        self.proverit_vypusk(v, "схема: список сайтов, пункт 1: лишние поля citata")

    def test_abzacev_9(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["abzacy"] = ["Раз."] * 9
        self.proverit_vypusk(v, "схема: крупная новость, пункт 1: abzacy — список из 5–8 строк")

    def test_krupnyh_4(self):
        v = self.json("vypusk.json")
        v["krupnye"] *= 2
        self.proverit_vypusk(v, "схема: крупная новость: пунктов 4, можно до 3")

    def test_upravlyayushchie_simvoly(self):
        v = self.json("vypusk.json")
        v["x"][0]["zagolovok"] = "раз\nдва"
        self.proverit_vypusk(v, "схема: список X, пункт 1: в поле zagolovok управляющие символы")

    def test_ne_json(self):
        self.proverit_vypusk("{", "схема: файл выпуска не читается")

    def test_vyvod_ne_neset_dannyh_zayavki_s_novoy_stroki(self):
        v = self.json("vypusk.json")
        v["dengi"][0]["\n::error::vzlom"] = 1
        self.proverit_vypusk(v, "схема: список денег и сделок, пункт 1: лишние поля ::error::vzlom")

    def test_ne_otvetili_tolko_imena_istochnikov(self):
        p = self.json("progon.json")
        p["ne_otvetili"] = ["X", "Перейдите по ссылке"]
        self.zapisat("progon.json", p)
        self.assertEqual(self.zapusk(), (1, "схема: файл прогона: в ne_otvetili не только имена источников из "
                                            "настроек\nпроверки не прошли: схема\n"))

    def test_progon_ne_po_vidu(self):
        p = self.json("progon.json")
        del p["nachalo_progona"]
        p["kandidaty"][2]["url"] = None
        p["github_polnyy_obhod"] = "да"
        self.zapisat("progon.json", p)
        self.assertEqual(self.zapusk(), (1, "схема: файл прогона: нет nachalo_progona\n"
                                            "схема: файл прогона: кандидат 3 не по виду\n"
                                            "схема: файл прогона: github_polnyy_obhod — не true/false\n"
                                            "проверки не прошли: схема\n"))

    def test_net_progona_u_vypuska(self):
        (self.papka / "progon.json").unlink()
        self.assertEqual(self.zapusk(), (1, "схема: файл прогона не читается\nпроверки не прошли: схема\n"))

    def test_i_vypusk_i_sboj(self):
        shutil.copy(OBRAZCY / "sboj.json", self.papka / "sboj.json")
        self.assertEqual(self.zapusk(), (1, "схема: есть и файл выпуска, и файл сбоя\nпроверки не прошли: схема\n"))

    def test_ni_vypuska_ni_sboya(self):
        (self.papka / "vypusk.json").unlink()
        self.assertEqual(self.zapusk(), (1, "схема: нет ни файла выпуска, ни файла сбоя\n"
                                            "проверки не прошли: схема\n"))

    def test_negodnyy_fayl_sboya(self):
        (self.papka / "vypusk.json").unlink()
        for prichina in ("сбор упал: https://evil.example/login", "не ответил ни один источник, пишите @evil_bot",
                         "три отказа proverit", "сбор упал: t.me/evil", "я" * 201, 7):
            with self.subTest(prichina=prichina):
                self.zapisat("sboj.json", {"prichina": prichina})
                kod, vyvod = self.zapusk()
                self.assertEqual((kod, vyvod), (1, "схема: файл сбоя: причина не из набора причин публикатора\n"
                                                   "проверки не прошли: схема\n"))
                for kusok in ("evil", "proverit", "я" * 20):
                    self.assertNotIn(kusok, vyvod)

    def test_fayl_sboya_frazy_nabora(self):
        (self.papka / "vypusk.json").unlink()
        for prichina in ("сбор упал", "не ответил ни один источник", "проверка выпуска не прошла"):
            with self.subTest(prichina=prichina):
                self.zapisat("sboj.json", {"prichina": prichina})
                self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))


class Dlina(SObrazcami):
    def test_soobshchenie_dlinnee_4096(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["abzacy"] = ["а" * 820] * 5
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (1, "длина: сообщение 1 (крупная новость) длиннее 4096 знаков: 4213 — "
                                            "сократите текст\nпроверки не прошли: длина\n"))

    def test_rovno_4096_mozhno(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["abzacy"] = ["а" * 820] * 4 + ["а" * 703]
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))


class Ssylki(SObrazcami):
    def proverit_vypusk(self, vypusk, stroki: str):
        self.zapisat("vypusk.json", vypusk)
        self.assertEqual(self.zapusk(), (1, stroki + "проверки не прошли: ссылки\n"))

    def test_adres_ne_iz_kandidatov(self):
        v = self.json("vypusk.json")
        v["sayty"][0]["url"] = "https://evil.example/deep-think"
        self.proverit_vypusk(v, "ссылки: список сайтов, пункт 1: адреса нет среди кандидатов прогона\n")

    def test_x_drugogo_akkaunta(self):
        v = self.json("vypusk.json")
        v["x"][0]["akkaunt"] = "AnthropicAI"
        self.proverit_vypusk(v, "ссылки: список X, пункт 1: адреса нет среди постов X этого аккаунта\n")

    def test_x_adres_ne_posta_x(self):
        v = self.json("vypusk.json")
        v["x"][0]["url"] = "https://www.techmeme.com/261110/p12"
        self.proverit_vypusk(v, "ссылки: список X, пункт 1: адреса нет среди постов X этого аккаунта\n")

    def test_github_bez_skoro(self):
        v = self.json("vypusk.json")
        v["github"][0]["url"] = "https://deepmind.google/blog/deep-think-api/"
        self.proverit_vypusk(v, "ссылки: список GitHub, пункт 1: адреса нет среди issue со skoro\n")

    def test_idy_ne_iz_kandidatov(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["idy"].append("https://example.com/vydumka")
        self.proverit_vypusk(v, "ссылки: крупная новость, пункт 1: в idy не идентификатор кандидата прогона\n")

    def test_oboroty_bez_vyshedshey_stroki(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["oboroty"] = True
        self.proverit_vypusk(v, "ссылки: крупная новость, пункт 1: oboroty у новости, которая не выходила "
                                "строкой без oboroty\n")

    def test_oboroty_vtoroy_raz(self):
        s = self.json("sostoyanie.json")
        s["opublikovannye"][0]["oboroty"] = True
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(), (1, "ссылки: крупная новость, пункт 2: oboroty у новости, которая не "
                                            "выходила строкой без oboroty\nпроверки не прошли: ссылки\n"))

    def test_oboroty_krupnoy_nelzya(self):
        s = self.json("sostoyanie.json")
        s["opublikovannye"][0]["vid"] = "krupnaya"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk()[0], 1)


class Svezhest(SObrazcami):
    def test_agent_ne_pozzhe_proshlogo_vypuska(self):
        s = self.json("sostoyanie.json")
        s["posledniy_sbor"] = "2026-11-11T02:07:00Z"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(), (1, "свежесть: начало прогона не позже прошлого выпуска\n"
                                            "проверки не прошли: свежесть\n"))

    def test_publikator_otkrytie_ne_pozzhe_proshlogo_vypuska(self):
        s = self.json("sostoyanie.json")
        s["posledniy_sbor"] = "2026-11-11T01:15:00Z"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(zayavka=True), (1, "свежесть: заявка открыта не позже прошлого выпуска\n"
                                                        "проверки не прошли: свежесть\n"))
        s["posledniy_sbor"] = "2026-11-11T01:14:59Z"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_publikator_zayavka_starshe_3_chasov(self):
        self.assertEqual(self.zapusk(zayavka=True, seychas=datetime(2026, 11, 11, 5, 15, 1, tzinfo=timezone.utc)),
                         (1, "свежесть: заявка открыта больше 3 часов назад\nпроверки не прошли: свежесть\n"))
        self.assertEqual(self.zapusk(zayavka=True, seychas=datetime(2026, 11, 11, 5, 15, tzinfo=timezone.utc)),
                         (0, "проверка пройдена\n"))

    def test_publikator_ne_chitaet_nachalo_progona_agenta(self):
        p = self.json("progon.json")
        p["nachalo_progona"] = "2020-01-01T00:00:00Z"
        self.zapisat("progon.json", p)
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_bez_sobytiya_schitaet_ot_seychas(self):
        kod, out = self.zapusk(seychas=datetime(2026, 11, 11, 5, 8, tzinfo=timezone.utc))
        self.assertEqual((kod, out), (1, "свежесть: прогон старше 3 часов к открытию заявки\n"
                                         "проверки не прошли: свежесть\n"))

    def test_agent_progon_iz_budushchego(self):
        self.assertEqual(self.zapusk(seychas=datetime(2026, 11, 11, 1, 56, 59, tzinfo=timezone.utc)),
                         (1, "свежесть: начало прогона позже открытия заявки\nпроверки не прошли: свежесть\n"))

    def test_sostoyanie_ne_chitaetsya(self):
        self.zapisat("sostoyanie.json", "{")
        self.assertEqual(self.zapusk(), (1, "свежесть: файл состояния не читается\n"
                                            "проверки не прошли: свежесть\n"))

    def test_pervyy_vypusk_bez_sostoyaniya(self):
        (self.papka / "sostoyanie.json").unlink()
        v = self.json("vypusk.json")
        v["krupnye"].pop()
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))

    def test_neskolko_proverok_v_itoge(self):
        v = self.json("vypusk.json")
        v["dengi"][0]["url"] = "https://evil.example/"
        self.zapisat("vypusk.json", v)
        s = self.json("sostoyanie.json")
        s["posledniy_sbor"] = "2026-11-11T03:00:00Z"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(), (1, "ссылки: список денег и сделок, пункт 1: адреса нет среди кандидатов "
                                            "прогона\nсвежесть: начало прогона не позже прошлого выпуска\n"
                                            "проверки не прошли: ссылки, свежесть\n"))


ATAKA = "https://evil.example/claude-opus-6-leak"


class Publikator(SObrazcami):
    """Сверка со сбором публикатора (tests/proverka/sbor-publikatora.json).

    В заявке — подложный файл прогона с адресами атакующего; NEWSAI_PROGON тоже указывает на него:
    публикатор его не читает ни в какой форме вызова.
    """

    def setUp(self):
        super().setUp()
        shutil.copy(OBRAZCY / "progon-podlozhnyy.json", self.papka / "progon.json")
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_PROGON": str(self.papka / "progon.json")})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)

    def chistyy(self):
        return self.json("chistyy.json")

    def adresa(self, vypusk, razdel):
        return [p["url"] for p in vypusk[razdel]]

    def test_podlozhnyy_progon_nichego_ne_daet(self):
        v = self.json("vypusk.json")
        v["sayty"].append({"zagolovok": "Утечка Claude Opus 6", "url": ATAKA, "istochnik": "claude.dev",
                           "zvezda": True, "idy": [ATAKA]})
        v["x"].insert(0, {"zagolovok": "Поставьте новый CLI", "akkaunt": "AnthropicAI", "zvezda": False,
                          "url": "https://x.com/AnthropicAI/status/1988000000000000666",
                          "idy": ["https://x.com/AnthropicAI/status/1988000000000000666"]})
        v["github"].append({"zagolovok": "Запустите скрипт", "url": "https://github.com/anthropics/claude-code/issues/6666",
                            "idy": ["https://github.com/anthropics/claude-code/issues/6666"]})
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: список сайтов, пункт 2: адреса нет среди кандидатов сбора публикатора\n"
            "отброшено: список X, пункт 1: адреса нет среди постов X этого аккаунта в сборе публикатора\n"
            "отброшено: список GitHub, пункт 2: адреса нет среди issue со skoro в сборе публикатора\n"
            "проверка пройдена\n"))
        chistyy = self.chistyy()
        self.assertEqual(chistyy, self.json_obrazca("vypusk.json"))
        self.assertNotIn("evil.example", (self.papka / "chistyy.json").read_text(encoding="utf-8"))
        self.assertNotIn("6666", (self.papka / "chistyy.json").read_text(encoding="utf-8"))

    def json_obrazca(self, imya):
        return json.loads((OBRAZCY / imya).read_text(encoding="utf-8"))

    def test_otbros_po_url_ostalnye_v_prezhnem_poryadke(self):
        v = self.json("vypusk.json")
        v["sayty"] = [
            {"zagolovok": "Мистраль", "url": "https://www.techmeme.com/261110/p12", "istochnik": "Techmeme",
             "zvezda": False, "idy": ["https://www.techmeme.com/261110/p12"]},
            {"zagolovok": "Чужое", "url": "https://example.com/net-v-sbore", "istochnik": "Cursor",
             "zvezda": False, "idy": ["https://example.com/net-v-sbore"]},
            v["sayty"][0],
        ]
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: список сайтов, пункт 2: адреса нет среди кандидатов сбора публикатора\n"
            "проверка пройдена\n"))
        self.assertEqual(self.adresa(self.chistyy(), "sayty"),
                         ["https://www.techmeme.com/261110/p12", "https://deepmind.google/blog/deep-think-api/"])
        from newsai import publikaciya
        s = publikaciya.soobshcheniya(self.chistyy(), [])
        self.assertEqual(s[2]["tekst"], '1. <a href="https://www.techmeme.com/261110/p12">Мистраль</a> (Techmeme)\n'
                                        '2. ⭐ <a href="https://deepmind.google/blog/deep-think-api/">Gemini получил '
                                        "Deep Think в API</a> (DeepMind)")

    def test_x_chuzhogo_akkaunta(self):
        v = self.json("vypusk.json")
        v["x"][0]["akkaunt"] = "AnthropicAI"
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: список X, пункт 1: адреса нет среди постов X этого аккаунта в сборе публикатора\n"
            "проверка пройдена\n"))
        self.assertEqual(self.chistyy()["x"], [])

    def test_github_bez_skoro(self):
        v = self.json("vypusk.json")
        v["github"][0]["url"] = "https://deepmind.google/blog/deep-think-api/"
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: список GitHub, пункт 1: адреса нет среди issue со skoro в сборе публикатора\n"
            "проверка пройдена\n"))
        self.assertEqual(self.chistyy()["github"], [])

    def test_chuzhie_idy_ubrany(self):
        v = self.json("vypusk.json")
        v["krupnye"][0]["idy"].append(ATAKA)
        v["sayty"][0]["idy"] = [ATAKA]
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: крупная новость, пункт 1: из idy убрано не из сбора публикатора: 1\n"
            "отброшено: список сайтов, пункт 1: из idy убрано не из сбора публикатора: 1\n"
            "проверка пройдена\n"))
        chistyy = self.chistyy()
        self.assertEqual(chistyy["krupnye"][0]["idy"], ["https://www.anthropic.com/news/claude-opus-5-5",
                                                        "https://x.com/AnthropicAI/status/1988000000000000001"])
        self.assertEqual(chistyy["sayty"][0]["idy"], ["https://deepmind.google/blog/deep-think-api/"])

    def test_vse_otbrosheno_pustoy_vypusk(self):
        v = {"krupnye": [], "sayty": [{"zagolovok": "Утечка", "url": ATAKA, "istochnik": "claude.dev",
                                       "zvezda": True, "idy": [ATAKA]}],
             "x": [], "dengi": [], "github": []}
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True), (0,
            "отброшено: список сайтов, пункт 1: адреса нет среди кандидатов сбора публикатора\n"
            "проверка пройдена\n"))
        self.assertEqual(self.chistyy(), {"krupnye": [], "sayty": [], "x": [], "dengi": [], "github": []})

    def test_narushenie_ne_ochishchaetsya(self):
        v = self.json("vypusk.json")
        v["sayty"][0]["url"] = "javascript:alert(1)"
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True),
                         (1, "схема: список сайтов, пункт 1: адрес не http(s)\nпроверки не прошли: схема\n"))
        self.assertFalse((self.papka / "chistyy.json").exists())

    def test_oboroty_proveryaetsya_i_do_sbora(self):
        s = self.json("sostoyanie.json")
        s["opublikovannye"] = []
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.zapusk(zayavka=True, progon=None, vyhod=None),
                         (1, "ссылки: крупная новость, пункт 2: oboroty у новости, которая не выходила строкой "
                             "без oboroty\nпроверки не прошли: ссылки\n"))

    def test_do_sbora_ssylki_ne_sveryayutsya(self):
        v = self.json("vypusk.json")
        v["sayty"][0]["url"] = ATAKA
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(zayavka=True, progon=None, vyhod=None), (0, "проверка пройдена\n"))
        self.assertFalse((self.papka / "chistyy.json").exists())

    def test_povtor_toy_zhe_zayavki_posle_obrabotki(self):
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))
        sost = sostoyanie.zagruzit(self.papka / "sostoyanie.json")
        sostoyanie.zavershit_progon(sost, datetime(2026, 11, 11, 2, 15, tzinfo=timezone.utc), [], SEYCHAS,
                                    self.papka / "sostoyanie.json")
        self.assertEqual(self.json("sostoyanie.json")["posledniy_sbor"], "2026-11-11T01:15:00Z")
        self.assertEqual(self.zapusk(zayavka=True), (1, "свежесть: заявка открыта не позже прошлого выпуска\n"
                                                        "проверки не прошли: свежесть\n"))
        self.assertEqual(self.zapusk(zayavka=True, progon=None, vyhod=None)[0], 1)

    def test_sbor_ne_chitaetsya_oshibka_ne_zayavki(self):
        with self.assertRaises(proverka.OshibkaSbora):
            self.zapusk(zayavka=True, progon=self.papka / "net-sbora.json")


VNE_SLOTA = ("слот: заявка открыта вне окна слота: от начала слота до плюс 40 минут\n"
             "заявка не к своему слоту: закрыть без публикации\n")
OBRABOTAN = "слот: слот заявки уже обработан\nзаявка не к своему слоту: закрыть без публикации\n"


class Slot(SObrazcami):
    """Проверка «слот»: заявка — в окне своего слота [начало, начало + 40 мин), слот ещё не обработан.
    Слот 09:00 по Бангкоку = 02:00 UTC; следующий — 17:00 = 10:00 UTC."""

    def otkryta(self, kogda: str) -> None:
        s = self.json("sobytie.json")
        s["pull_request"]["created_at"] = kogda
        self.zapisat("sobytie.json", s)

    def obe_formy(self, seychas=SEYCHAS):
        """(до сбора, против сбора) — коды и вывод обеих форм proverit публикатора."""
        return (self.zapusk(zayavka=True, progon=None, vyhod=None, seychas=seychas),
                self.zapusk(zayavka=True, seychas=seychas))

    def test_v_okne_prohodit(self):
        for kogda in ("2026-11-11T02:00:00Z", "2026-11-11T02:39:59Z"):
            with self.subTest(kogda=kogda):
                self.otkryta(kogda)
                self.assertEqual(self.obe_formy(), ((0, "проверка пройдена\n"), (0, "проверка пройдена\n")))

    def test_posle_okna_do_sleduyushchego_slota_kod_3(self):
        for kogda in ("2026-11-11T02:40:00Z", "2026-11-11T05:00:00Z", "2026-11-11T09:59:59Z"):
            with self.subTest(kogda=kogda):
                self.otkryta(kogda)
                seychas = datetime.fromisoformat(kogda.replace("Z", "+00:00"))
                self.assertEqual(self.obe_formy(seychas), ((3, VNE_SLOTA), (3, VNE_SLOTA)))
                self.assertFalse((self.papka / "chistyy.json").exists())

    def test_obrabotannyy_slot_kod_3(self):
        s = self.json("sostoyanie.json")
        s["posledniy_slot"] = "2026-11-11T02:00:00Z"
        self.zapisat("sostoyanie.json", s)
        self.assertEqual(self.obe_formy(), ((3, OBRABOTAN), (3, OBRABOTAN)))
        self.assertFalse((self.papka / "chistyy.json").exists())

    def test_vtoraya_zayavka_togo_zhe_slota(self):
        sost = sostoyanie.zagruzit(self.papka / "sostoyanie.json")
        sostoyanie.zapisat_slot(sost, "2026-11-11T02:00:00Z", self.papka / "sostoyanie.json")
        self.otkryta("2026-11-11T02:30:00Z")
        self.assertEqual(self.zapusk(zayavka=True, seychas=datetime(2026, 11, 11, 2, 31, tzinfo=timezone.utc)),
                         (3, OBRABOTAN))

    def test_vne_okna_i_obrabotan(self):
        s = self.json("sostoyanie.json")
        s["posledniy_slot"] = "2026-11-11T02:00:00Z"
        self.zapisat("sostoyanie.json", s)
        self.otkryta("2026-11-11T03:00:00Z")
        self.assertEqual(self.zapusk(zayavka=True),
                         (3, "слот: заявка открыта вне окна слота: от начала слота до плюс 40 минут\n"
                             "слот: слот заявки уже обработан\nзаявка не к своему слоту: закрыть без публикации\n"))

    def test_slot_do_faylov_i_dannyh(self):
        self.otkryta("2026-11-11T04:00:00Z")
        self.zapisat("fayly.tsv", "added\t.github/x.yml\n")
        self.zapisat("vypusk.json", "не json")
        self.assertEqual(self.zapusk(zayavka=True), (3, VNE_SLOTA))

    def test_chuzhaya_vne_slota_tolko_zayavka(self):
        s = self.json("sobytie.json")
        s["pull_request"]["head"]["repo"]["fork"] = True
        s["pull_request"]["created_at"] = "2026-11-11T04:00:00Z"
        self.zapisat("sobytie.json", s)
        self.assertEqual(self.zapusk(zayavka=True), (3, "заявка: ветка заявки не из этого репозитория\n"
                                                        "чужая заявка: закрыть без публикации\n"))

    def test_sostoyanie_ne_chitaetsya(self):
        self.zapisat("sostoyanie.json", "{")
        self.assertEqual(self.zapusk(zayavka=True), (1, "свежесть: файл состояния не читается\n"
                                                        "проверки не прошли: свежесть\n"))
        self.otkryta("2026-11-11T04:00:00Z")
        self.assertEqual(self.zapusk(zayavka=True), (3, VNE_SLOTA))

    def test_slot_01_cherez_polnoch(self):
        s = self.json("sostoyanie.json")
        s["posledniy_sbor"] = "2026-11-10T08:00:00Z"
        self.zapisat("sostoyanie.json", s)
        for kogda, kod in (("2026-11-10T18:39:59Z", 0), ("2026-11-10T18:40:00Z", 3)):
            with self.subTest(kogda=kogda):
                self.otkryta(kogda)
                seychas = datetime(2026, 11, 10, 18, 45, tzinfo=timezone.utc)
                self.assertEqual(self.zapusk(zayavka=True, progon=None, vyhod=None, seychas=seychas)[0], kod)


class Povtor(SObrazcami):
    """Повтор адреса из opublikovannye: у публикатора пункт отброшен, у агента нарушение «ссылки»;
    крупная с oboroty — не повтор. Состояние-образец: вышел строкой сайтов Codex (OpenAI); в сборе
    публикатора и в прогоне агента этот кандидат — с novyy: false."""

    CODEX = "https://openai.com/index/codex-for-everyone/"

    def setUp(self):
        super().setUp()
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_PROGON": str(OBRAZCY / "progon-podlozhnyy.json")})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)

    def test_kandidat_novyy_false_s_vyshedshim_adresom_otbroshen(self):
        v = self.json("vypusk.json")
        v["sayty"].append({"zagolovok": "OpenAI открыла Codex для всех", "url": self.CODEX, "istochnik": "OpenAI",
                           "zvezda": False, "idy": [self.CODEX]})
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (1, "ссылки: список сайтов, пункт 2: адрес уже выходил в канал\n"
                                            "проверки не прошли: ссылки\n"))
        self.assertEqual(self.zapusk(zayavka=True), (0, "отброшено: список сайтов, пункт 2: адрес уже выходил в "
                                                        "канал\nпроверка пройдена\n"))
        self.assertEqual([p["url"] for p in self.json("chistyy.json")["sayty"]],
                         ["https://deepmind.google/blog/deep-think-api/"])

    def test_krupnaya_s_oboroty_ne_povtor(self):
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))
        self.assertEqual([p["url"] for p in self.json("chistyy.json")["krupnye"]],
                         ["https://www.anthropic.com/news/claude-opus-5-5", self.CODEX])

    def test_krupnaya_bez_oboroty_povtor(self):
        v = self.json("vypusk.json")
        v["krupnye"][1]["oboroty"] = False
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (1, "ссылки: крупная новость, пункт 2: адрес уже выходил в канал\n"
                                            "проверки не прошли: ссылки\n"))
        self.assertEqual(self.zapusk(zayavka=True), (0, "отброшено: крупная новость, пункт 2: адрес уже выходил в "
                                                        "канал\nпроверка пройдена\n"))
        self.assertEqual(len(self.json("chistyy.json")["krupnye"]), 1)

    def test_kazhdyy_razdel(self):
        for razdel, nazvanie, vid in (("sayty", "список сайтов", "sayty"), ("x", "список X", "x"),
                                      ("dengi", "список денег и сделок", "dengi"),
                                      ("github", "список GitHub", "github")):
            with self.subTest(razdel=razdel):
                shutil.copy(OBRAZCY / "sostoyanie.json", self.papka / "sostoyanie.json")
                s = self.json("sostoyanie.json")
                url = self.json("vypusk.json")[razdel][0]["url"]
                s["opublikovannye"].append({"zagolovok": "было", "url": url, "data": "2026-11-10T22:10:00Z",
                                            "vid": vid, "oboroty": False})
                self.zapisat("sostoyanie.json", s)
                self.assertEqual(self.zapusk(), (1, f"ссылки: {nazvanie}, пункт 1: адрес уже выходил в канал\n"
                                                    "проверки не прошли: ссылки\n"))
                self.assertEqual(self.zapusk(zayavka=True), (0, f"отброшено: {nazvanie}, пункт 1: адрес уже "
                                                                "выходил в канал\nпроверка пройдена\n"))
                self.assertEqual(self.json("chistyy.json")[razdel], [])


class AdresVTekste(SObrazcami):
    """Адрес в тексте: Telegram сделал бы его ссылкой — у публикатора пункт отброшен, у агента «ссылки».

    Публикатор — с подложным файлом прогона в заявке, как в Publikator."""

    ADRESA = ("https://evil.example/login", "www.evil.example", "t.me/evil_channel", "evil.com",
              "@evil_channel", "pochta@evil.example")
    NE_ADRESA = ("Next.js", "GPT-5.5", "v2.1", "Claude 4.5")

    def setUp(self):
        super().setUp()
        zaplatka = mock.patch.dict(os.environ, {"NEWSAI_PROGON": str(OBRAZCY / "progon-podlozhnyy.json")})
        zaplatka.start()
        self.addCleanup(zaplatka.stop)

    def s_pravkoy(self, pole: str, tekst: str) -> None:
        """Записать выпуск-образец с текстом в поле: zagolovok и istochnik — строки сайтов, abzacy —
        третий абзац первой крупной."""
        v = self.json_obrazca()
        if pole == "abzacy":
            v["krupnye"][0]["abzacy"][2] = f"Подробности: {tekst}"
        elif pole == "zagolovok":
            v["sayty"][0]["zagolovok"] = f"Gemini получил Deep Think {tekst}"
        else:
            v["sayty"][0]["istochnik"] = tekst
        self.zapisat("vypusk.json", v)

    def json_obrazca(self):
        return json.loads((OBRAZCY / "vypusk.json").read_text(encoding="utf-8"))

    def test_kazhdyy_adres_v_kazhdom_pole(self):
        gde_i_prichina = {
            "zagolovok": ("список сайтов, пункт 1", ["адрес или упоминание в заголовке"]),
            "abzacy": ("крупная новость, пункт 1", ["адрес или упоминание в абзаце 3"]),
            "istochnik": ("список сайтов, пункт 1", ["адрес или упоминание в имени источника",
                                                     "имя источника — не источник кандидата с этим адресом"]),
        }
        for pole, (gde, prichiny) in gde_i_prichina.items():
            for adres in self.ADRESA:
                with self.subTest(pole=pole, adres=adres):
                    self.s_pravkoy(pole, adres)
                    kod, vyvod = self.zapusk()
                    self.assertEqual((kod, vyvod), (1, "".join(f"ссылки: {gde}: {p}\n" for p in prichiny)
                                                    + "проверки не прошли: ссылки\n"))
                    kod, vyvod = self.zapusk(zayavka=True)
                    self.assertEqual((kod, vyvod), (0, f"отброшено: {gde}: {prichiny[0]}\nпроверка пройдена\n"))
                    chistyy = (self.papka / "chistyy.json").read_text(encoding="utf-8")
                    self.assertNotIn("evil", chistyy)
                    razdel = "krupnye" if pole == "abzacy" else "sayty"
                    self.assertEqual(json.loads(chistyy)[razdel], self.json_obrazca()[razdel][1:])

    def test_upominanie_v_zagolovke_kazhdogo_razdela(self):
        for razdel, nazvanie in (("krupnye", "крупная новость"), ("sayty", "список сайтов"), ("x", "список X"),
                                 ("dengi", "список денег и сделок"), ("github", "список GitHub")):
            with self.subTest(razdel=razdel):
                v = self.json_obrazca()
                v[razdel][0]["zagolovok"] += " — подробности у @evil_bot"
                self.zapisat("vypusk.json", v)
                self.assertEqual(self.zapusk(), (1, f"ссылки: {nazvanie}, пункт 1: адрес или упоминание в "
                                                    "заголовке\nпроверки не прошли: ссылки\n"))
                self.assertEqual(self.zapusk(zayavka=True), (0, f"отброшено: {nazvanie}, пункт 1: адрес или "
                                                                "упоминание в заголовке\nпроверка пройдена\n"))

    def test_ne_adres_prohodit(self):
        for ne_adres in self.NE_ADRESA:
            for pole in ("zagolovok", "abzacy"):
                with self.subTest(ne_adres=ne_adres, pole=pole):
                    self.s_pravkoy(pole, ne_adres)
                    self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))
                    self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_istochnik_ne_togo_istochnika(self):
        for razdel, nazvanie, chuzhoy in (("sayty", "список сайтов", "Techmeme"),
                                          ("dengi", "список денег и сделок", "DeepMind")):
            with self.subTest(razdel=razdel):
                v = self.json_obrazca()
                v[razdel][0]["istochnik"] = chuzhoy
                self.zapisat("vypusk.json", v)
                self.assertEqual(self.zapusk(), (1, f"ссылки: {nazvanie}, пункт 1: имя источника — не источник "
                                                    "кандидата с этим адресом\nпроверки не прошли: ссылки\n"))
                self.assertEqual(self.zapusk(zayavka=True), (0,
                    f"отброшено: {nazvanie}, пункт 1: имя источника — не источник кандидата сбора публикатора "
                    "с этим адресом\nпроверка пройдена\n"))
                self.assertEqual(json.loads((self.papka / "chistyy.json").read_text(encoding="utf-8"))[razdel], [])

    def test_imya_istochnika_iz_nastroek_ne_adres(self):
        v = self.json_obrazca()
        opus = "https://www.anthropic.com/news/claude-opus-5-5"
        v["sayty"][0] = {"zagolovok": "Anthropic выпустила Claude Opus 5.5", "url": opus,
                         "istochnik": "claude.dev", "zvezda": False, "idy": [opus]}
        self.zapisat("vypusk.json", v)
        self.assertEqual(self.zapusk(), (0, "проверка пройдена\n"))
        self.assertEqual(self.zapusk(zayavka=True), (0, "проверка пройдена\n"))

    def test_raspoznavanie(self):
        for tekst in self.ADRESA + ("http://x", "HTTPS://EVIL.EXAMPLE", "WWW.evil", "telegram.me/evil",
                                    "telegram.dog/evil", "tg:resolve?domain=evil", "evil.COM/путь", "пример.рф",
                                    "evil​.com", "evil．com", "＠evil", "@_evil", "@1evil", "10.0.0.1"):
            with self.subTest(tekst=tekst):
                self.assertTrue(proverka.adres_v_tekste(f"Текст {tekst} дальше"))
        for tekst in self.NE_ADRESA + ("т.е. и т.д.", "Node.js 24.1", "Ben's Bites", "@ всех", "C# и С++",
                                       "Конец.Начало", "Llama 3.3"):
            with self.subTest(tekst=tekst):
                self.assertFalse(proverka.adres_v_tekste(f"Текст {tekst} дальше"))


class Komanda(SObrazcami):
    """`python -m newsai proverit` — связка аргументов с файлами и коды выхода."""

    def komanda(self, *argumenty):
        """Точка входа в процессе с часами SEYCHAS: в отдельном процессе свежесть считалась бы по часам
        машины, и тест краснел бы через 3 часа после открытия заявки-образца [техника]."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(proverka, "_seychas", return_value=SEYCHAS):
            try:
                kod = tochka.main(["proverit", *argumenty])
            except SystemExit as e:
                kod = e.code
        return subprocess.CompletedProcess(argumenty, kod, out.getvalue(), err.getvalue())

    def zayavka_i_fayly(self):
        """Первая форма публикатора — до сбора: заявка, файлы, схема, длина, свежесть."""
        return ["--vypusk", str(self.papka / "vypusk.json"), "--sboj", str(self.papka / "sboj.json"),
                "--sostoyanie", str(self.papka / "sostoyanie.json"),
                "--zayavka", str(self.papka / "sobytie.json"), "--fayly", str(self.papka / "fayly.tsv")]

    def faily(self):
        """Вторая форма — против сбора публикатора, очищенный выпуск на выходе."""
        return self.zayavka_i_fayly() + ["--progon", str(OBRAZCY / "sbor-publikatora.json"),
                                         "--vyhod", str(self.papka / "chistyy.json")]

    def test_proshla(self):
        r = self.komanda(*self.faily())
        self.assertEqual((r.returncode, r.stdout), (0, "проверка пройдена\n"), r.stderr)
        self.assertEqual(json.loads((self.papka / "chistyy.json").read_text(encoding="utf-8")),
                         dict(self.json("vypusk.json")))

    def test_do_sbora_proshla_bez_vyhoda(self):
        r = self.komanda(*self.zayavka_i_fayly())
        self.assertEqual((r.returncode, r.stdout), (0, "проверка пройдена\n"), r.stderr)
        self.assertFalse((self.papka / "chistyy.json").exists())

    def test_do_sbora_chuzhaya_kod_3(self):
        s = self.json("sobytie.json")
        s["pull_request"]["user"]["login"] = "kto-to"
        self.zapisat("sobytie.json", s)
        r = self.komanda(*self.zayavka_i_fayly())
        self.assertEqual((r.returncode, r.stdout), (3, "заявка: автор заявки — не владелец репозитория\n"
                                                       "чужая заявка: закрыть без публикации\n"))

    def test_otbroshennoe_v_vyvode_kod_0(self):
        v = self.json("vypusk.json")
        v["sayty"].append({"zagolovok": "Утечка Claude Opus 6", "url": "https://evil.example/claude-opus-6-leak",
                           "istochnik": "claude.dev", "zvezda": True, "idy": ["https://evil.example/claude-opus-6-leak"]})
        self.zapisat("vypusk.json", v)
        r = self.komanda(*self.faily())
        self.assertEqual((r.returncode, r.stdout),
                         (0, "отброшено: список сайтов, пункт 2: адреса нет среди кандидатов сбора публикатора\n"
                             "проверка пройдена\n"), r.stderr)
        self.assertNotIn("evil.example", (self.papka / "chistyy.json").read_text(encoding="utf-8"))

    def test_vyhod_tolko_so_sborom(self):
        for argumenty in (self.zayavka_i_fayly() + ["--progon", str(OBRAZCY / "sbor-publikatora.json")],
                          self.zayavka_i_fayly() + ["--vyhod", str(self.papka / "chistyy.json")],
                          ["--vyhod", str(self.papka / "chistyy.json")]):
            with self.subTest(argumenty=argumenty[-2:]):
                r = self.komanda(*argumenty)
                self.assertEqual(r.returncode, 2)
                self.assertIn("--vyhod задаётся вместе с --zayavka и --progon", r.stderr)

    def test_sbor_publikatora_ne_chitaetsya_kod_2(self):
        argumenty = self.zayavka_i_fayly() + ["--progon", str(self.papka / "net-sbora.json"),
                                              "--vyhod", str(self.papka / "chistyy.json")]
        r = self.komanda(*argumenty)
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("сбор публикатора не читается", r.stderr)
        self.assertFalse((self.papka / "chistyy.json").exists())

    def test_spravka_proverit_novye_flagi(self):
        r = self.komanda("--help")
        self.assertEqual(r.returncode, 0)
        for flag in ("--zayavka", "--fayly", "--progon", "--vyhod"):
            self.assertIn(flag, r.stdout)

    def test_narushenie_kod_1(self):
        self.zapisat("fayly.tsv", "added\tdannye/progon.json\nadded\tdannye/vypusk.json\nadded\t.github/x.yml\n")
        r = self.komanda(*self.faily())
        self.assertEqual((r.returncode, r.stdout),
                         (1, "файлы: лишний файл в заявке: .github/x.yml\nпроверки не прошли: файлы\n"))

    def test_chuzhaya_kod_3(self):
        s = self.json("sobytie.json")
        s["pull_request"]["head"]["repo"]["fork"] = True
        self.zapisat("sobytie.json", s)
        r = self.komanda(*self.faily())
        self.assertEqual((r.returncode, r.stdout), (3, "заявка: ветка заявки не из этого репозитория\n"
                                                       "чужая заявка: закрыть без публикации\n"))

    def test_vne_slota_kod_3(self):
        s = self.json("sobytie.json")
        s["pull_request"]["created_at"] = "2026-11-11T02:40:00Z"
        self.zapisat("sobytie.json", s)
        for argumenty in (self.zayavka_i_fayly(), self.faily()):
            with self.subTest(sbor="--progon" in argumenty):
                r = self.komanda(*argumenty)
                self.assertEqual((r.returncode, r.stdout), (3, VNE_SLOTA), r.stderr)
                self.assertFalse((self.papka / "chistyy.json").exists())

    def test_zayavka_bez_spiska_faylov(self):
        r = self.komanda("--zayavka", str(self.papka / "sobytie.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--zayavka и --fayly задаются вместе", r.stderr)

    def test_v_spravke(self):
        r = subprocess.run([sys.executable, "-m", "newsai", "--help"], cwd=nastrojki.KOREN,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn("proverit", r.stdout)


if __name__ == "__main__":
    unittest.main()
