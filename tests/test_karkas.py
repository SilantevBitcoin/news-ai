"""Каркас: чтение секретов и загрузка токена, файл источников, точка входа."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from newsai import gitkhab, iks, nastrojki, sekrety, tokenbota

LOZHNYY = "123456:poddelka-dlya-testa"
IMENA = ("TELEGRAM_BOT_TOKEN", "TWITTERAPI_KEY", "GITHUB_TOKEN")


class Sekrety(unittest.TestCase):
    """Шов «загрузка секрета»: временный файл секретов и подменённое окружение; настоящий файл не читается."""

    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.addCleanup(self._papka.cleanup)
        self.fayl = Path(self._papka.name) / "sekrety"
        okr = {k: v for k, v in os.environ.items() if k not in IMENA}
        for zaplatka in (mock.patch.dict(os.environ, okr, clear=True),
                         mock.patch.object(nastrojki, "FAYL_SEKRETOV", self.fayl)):
            zaplatka.start()
            self.addCleanup(zaplatka.stop)

    def test_okruzhenie_vazhnee_fayla(self):
        self.fayl.write_text("TWITTERAPI_KEY=iz-fayla\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TWITTERAPI_KEY": "  iz-okruzheniya \n"}):
            self.assertEqual(sekrety.nayti("TWITTERAPI_KEY"), ("iz-okruzheniya", "в переменной окружения"))

    def test_pustoe_okruzhenie_beret_fayl(self):
        self.fayl.write_text("TWITTERAPI_KEY=iz-fayla\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TWITTERAPI_KEY": "   "}):
            self.assertEqual(sekrety.nayti("TWITTERAPI_KEY"), ("iz-fayla", "в файле секретов проекта"))

    def test_razbor_fayla(self):
        self.fayl.write_text(
            "﻿# комментарий\n\nGITHUB_TOKEN=\nexport  TWITTERAPI_KEY = 'v kavychkah # ne kommentariy'\n"
            "GITHUB_TOKEN=vtoroy # хвост\nNE_STROKA\n", encoding="utf-8")
        self.assertEqual(sekrety.prochitat("TWITTERAPI_KEY"), "v kavychkah # ne kommentariy")
        self.assertIsNone(sekrety.prochitat("GITHUB_TOKEN"))

    def test_kommentariy_v_hvoste(self):
        self.fayl.write_text("GITHUB_TOKEN=znachenie # хвост\n", encoding="utf-8")
        self.assertEqual(sekrety.prochitat("GITHUB_TOKEN"), "znachenie")

    def test_net_nigde(self):
        self.assertEqual(sekrety.nayti("TWITTERAPI_KEY"), (None, None))
        self.fayl.write_text("DRUGOY=1\n", encoding="utf-8")
        self.assertEqual(sekrety.nayti("TWITTERAPI_KEY"), (None, None))

    def test_yavnyy_fayl(self):
        drugoy = self.fayl.with_name("drugoy")
        drugoy.write_text("TWITTERAPI_KEY=iz-drugogo\n", encoding="utf-8")
        self.fayl.write_text("TWITTERAPI_KEY=iz-osnovnogo\n", encoding="utf-8")
        self.assertEqual(sekrety.prochitat("TWITTERAPI_KEY", drugoy), "iz-drugogo")

    def test_vse_tri_sekreta_odnim_chteniem(self):
        self.fayl.write_text(f"TELEGRAM_BOT_TOKEN={LOZHNYY}\nexport TWITTERAPI_KEY=\"k-twitter\"\n"
                             "GITHUB_TOKEN=k-github # хвост\n", encoding="utf-8")
        self.assertEqual((tokenbota.zagruzit_token(), iks.klyuch_twitterapi(), gitkhab.token_github()),
                         (LOZHNYY, "k-twitter", "k-github"))
        with mock.patch.dict(os.environ, {"TWITTERAPI_KEY": "e-twitter", "GITHUB_TOKEN": "e-github"}):
            self.assertEqual((iks.klyuch_twitterapi(), gitkhab.token_github()), ("e-twitter", "e-github"))


class Token(unittest.TestCase):
    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.fayl = Path(self._papka.name) / "sekrety"

    def tearDown(self):
        self._papka.cleanup()

    def bez_peremennoy(self):
        okr = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
        return mock.patch.dict(os.environ, okr, clear=True)

    def test_okruzhenie_vazhnee_fayla(self):
        self.fayl.write_text(f"TELEGRAM_BOT_TOKEN=iz-fayla\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": LOZHNYY}):
            self.assertEqual(tokenbota.zagruzit_token(self.fayl), LOZHNYY)

    def test_iz_fayla(self):
        self.fayl.write_text(
            "# комментарий\n\nTWITTERAPI_KEY=\nexport TELEGRAM_BOT_TOKEN = \"" + LOZHNYY + "\"\n",
            encoding="utf-8")
        with self.bez_peremennoy():
            self.assertEqual(tokenbota.zagruzit_token(self.fayl), LOZHNYY)

    def test_kommentariy_v_stroke(self):
        self.fayl.write_text(f"TELEGRAM_BOT_TOKEN={LOZHNYY} # из BotFather\n", encoding="utf-8")
        with self.bez_peremennoy():
            self.assertEqual(tokenbota.zagruzit_token(self.fayl), LOZHNYY)

    def test_net_tokena_bez_znacheniya_v_soobshchenii(self):
        self.fayl.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
        with self.bez_peremennoy(), self.assertRaises(tokenbota.NetTokena) as e:
            tokenbota.zagruzit_token(self.fayl)
        self.assertIn("TELEGRAM_BOT_TOKEN", str(e.exception))

    def test_net_fayla(self):
        with self.bez_peremennoy(), self.assertRaises(tokenbota.NetTokena):
            tokenbota.zagruzit_token(self.fayl.with_name("netu"))

    def test_vid_tokena_v_okruzhenii(self):
        for plohoy in ("123456:AAA BBB-proba", "abc:def", "123456", ":abc", "123456:abc\x07", "123:абв"):
            with self.subTest(plohoy=plohoy), \
                    mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": plohoy}), \
                    self.assertRaises(tokenbota.NetTokena) as e:
                tokenbota.zagruzit_token(self.fayl.with_name("netu"))
            self.assertEqual(str(e.exception),
                             "TELEGRAM_BOT_TOKEN в переменной окружения не того вида: "
                             "ждём <цифры>:<буквы, цифры, _ и ->")

    def test_vid_tokena_v_fayle(self):
        self.fayl.write_text("TELEGRAM_BOT_TOKEN=123456:AAA\tBBB\n", encoding="utf-8")
        with self.bez_peremennoy(), self.assertRaises(tokenbota.NetTokena) as e:
            tokenbota.zagruzit_token(self.fayl)
        self.assertNotIn("AAA", str(e.exception))
        self.assertIn("в файле секретов проекта не того вида", str(e.exception))

    def test_okruzhenie_ne_togo_vida_fayl_ne_chitaetsya(self):
        self.fayl.write_text(f"TELEGRAM_BOT_TOKEN={LOZHNYY}\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "ne-token"}), \
                self.assertRaises(tokenbota.NetTokena):
            tokenbota.zagruzit_token(self.fayl)


class Puti(unittest.TestCase):
    def test_fayly_dannyh_v_papke_dannye(self):
        okr = {k: v for k, v in os.environ.items() if not k.startswith("NEWSAI_")}
        with mock.patch.dict(os.environ, okr, clear=True):
            koren = nastrojki.KOREN
            self.assertEqual(nastrojki.fayl_sostoyaniya(), koren / "dannye" / "sostoyanie.json")
            self.assertEqual(nastrojki.fayl_progona(), koren / "dannye" / "progon.json")
            self.assertEqual(nastrojki.fayl_vypuska(), koren / "dannye" / "vypusk.json")
            self.assertEqual(nastrojki.fayl_sboya(), koren / "dannye" / "sboj.json")
            self.assertEqual(nastrojki.fayl_issue(), koren / "dannye" / "issue.json")

    def test_puti_iz_okruzheniya(self):
        okr = {"NEWSAI_SOSTOYANIE": "s.json", "NEWSAI_PROGON": "p.json", "NEWSAI_VYPUSK": "v.json",
               "NEWSAI_SBOJ": "b.json", "NEWSAI_ISSUE": "i.json"}
        with mock.patch.dict(os.environ, okr):
            self.assertEqual([nastrojki.fayl_sostoyaniya(), nastrojki.fayl_progona(), nastrojki.fayl_vypuska(),
                              nastrojki.fayl_sboya(), nastrojki.fayl_issue()],
                             [Path("s.json"), Path("p.json"), Path("v.json"), Path("b.json"), Path("i.json")])


class Istochniki(unittest.TestCase):
    def setUp(self):
        self.spisok = json.loads(nastrojki.FAYL_ISTOCHNIKOV.read_text(encoding="utf-8"))["istochniki"]

    def test_vse_stroki_tablicy_speki(self):
        imena = [i["imya"] for i in self.spisok]
        self.assertEqual(imena, [
            "claude.dev", "Claude Code", "Claude Platform", "Блог Claude", "OpenAI", "Codex", "OpenAI API",
            "AINews", "Simon Willison", "Ben's Bites", "Cursor", "DeepMind", "Блог Google",
            "Google Developers Blog", "Techmeme", "X", "GitHub Claude Code"])
        for i in self.spisok:
            self.assertTrue(i["vid"] and i["adres"], i["imya"])

    def test_30_akkauntov_x(self):
        x = next(i for i in self.spisok if i["imya"] == "X")
        self.assertEqual(len(x["akkaunty"]), 30)
        self.assertEqual(len(set(x["akkaunty"])), 30)


class TochkaVhoda(unittest.TestCase):
    def zapusk(self, *argumenty):
        return subprocess.run([sys.executable, "-m", "newsai", *argumenty], cwd=nastrojki.KOREN,
                              capture_output=True, text=True, encoding="utf-8")

    def test_spravka(self):
        r = self.zapusk("--help")
        self.assertEqual(r.returncode, 0)
        for komanda in ("sbor", "sbor-issue", "tekst", "proverit", "opublikovat", "sboj", "nesohraneno",
                        "storozh"):
            self.assertIn(komanda, r.stdout)
        self.assertIn("    sbor-issue ", r.stdout)

    def test_sboj_beret_prichinu_iz_fayla(self):
        r = self.zapusk("sboj", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--prichina-iz", r.stdout)
        self.assertIn("--slot-iz-zayavki", r.stdout)
        self.assertNotEqual(self.zapusk("sboj", "причина").returncode, 0)

    def test_sbor_publikatora_v_spravke(self):
        r = self.zapusk("sbor", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--konec-iz-zayavki", r.stdout)

    def test_neizvestnaya_komanda(self):
        self.assertNotEqual(self.zapusk("chto-to").returncode, 0)


if __name__ == "__main__":
    unittest.main()
