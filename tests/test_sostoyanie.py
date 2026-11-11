"""Шов «Функции состояния»: период, первые 24 часа, повтор увиденного, чистка 30 и 14 дней."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsai import nastrojki, sostoyanie
from newsai.vremya import iz_stroki, v_stroku

UTC = timezone.utc
SEYCHAS = datetime(2026, 11, 11, 2, 7, tzinfo=UTC)
ISSUE = "https://github.com/anthropics/claude-code/issues/1234"


class SVremennoyPapkoy(unittest.TestCase):
    def setUp(self):
        papka = nastrojki.KOREN / "temp"
        papka.mkdir(exist_ok=True)
        self._papka = tempfile.TemporaryDirectory(dir=papka)
        self.put = Path(self._papka.name) / "sostoyanie.json"

    def tearDown(self):
        self._papka.cleanup()


class Period(SVremennoyPapkoy):
    def test_net_fayla_pervyy_progon_24_chasa(self):
        sost = sostoyanie.zagruzit(self.put)
        self.assertTrue(sostoyanie.pervyy_progon(sost))
        nachalo, konec = sostoyanie.period(sost, SEYCHAS)
        self.assertEqual(konec, SEYCHAS)
        self.assertEqual(nachalo, SEYCHAS - timedelta(hours=24))

    def test_period_ot_poslednego_sbora(self):
        sost = sostoyanie.pustoe()
        sost["posledniy_sbor"] = "2026-11-10T18:07:00Z"
        self.assertFalse(sostoyanie.pervyy_progon(sost))
        nachalo, konec = sostoyanie.period(sost, SEYCHAS)
        self.assertEqual(nachalo, datetime(2026, 11, 10, 18, 7, tzinfo=UTC))
        self.assertEqual(konec, SEYCHAS)

    def test_zavershenie_sdvigaet_period_i_pri_pustom_vypuske(self):
        sost = sostoyanie.zagruzit(self.put)
        sostoyanie.zavershit_progon(sost, SEYCHAS, [], SEYCHAS + timedelta(minutes=5), self.put)
        sled = sostoyanie.zagruzit(self.put)
        self.assertEqual(sled["posledniy_sbor"], "2026-11-11T01:07:00Z")
        nachalo, _ = sostoyanie.period(sled, SEYCHAS + timedelta(hours=8))
        self.assertEqual(nachalo, datetime(2026, 11, 11, 1, 7, tzinfo=UTC))


class Zapas(SVremennoyPapkoy):
    """Запас 60 минут: кандидат внутри запаса не попадает в vidennye и в следующем сборе снова novyy."""

    def test_kandidat_vnutri_zapasa_snova_novyy(self):
        from newsai import publikaciya, sbor
        from newsai.zaprosy import Otvet

        ist = next(i for i in sbor.zagruzit_istochniki() if i["imya"] == "Simon Willison")
        atom = (Path(__file__).parent / "zapisi" / "simonw.atom").read_bytes()

        def set_(url, zagolovki=None):
            return Otvet(200, atom, {"content-type": "application/atom+xml"})

        konec = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
        sost = sostoyanie.zagruzit(self.put)
        rez = sbor.sobrat([ist], sost, konec, set_)
        dannye = {"kandidaty": [k.v_slovar() for k in rez["kandidaty"]], "tolko_v_vidennye": []}
        self.assertEqual(publikaciya._idy_progona(dannye, konec),
                         ["https://simonwillison.net/2026/Nov/10/prompting-claude-fable-52/"])
        sostoyanie.zavershit_progon(sost, konec, publikaciya._idy_progona(dannye, konec), konec, self.put)
        sled = sostoyanie.zagruzit(self.put)
        self.assertEqual(sled["posledniy_sbor"], "2026-11-10T07:00:00Z")
        self.assertEqual(list(sled["vidennye"]), ["https://simonwillison.net/2026/Nov/10/prompting-claude-fable-52/"])
        rez = sbor.sobrat([ist], sled, datetime(2026, 11, 10, 16, 0, tzinfo=UTC), set_)
        self.assertEqual([(k.zagolovok, k.novyy) for k in rez["kandidaty"]], [
            ("Introducing Our Smallest, Most Efficient Model: Muse Ember", True),
            ("Quoting Prompting Claude Fable 5.2", False)])

    def test_granica_zapasa(self):
        from newsai import publikaciya
        konec = datetime(2026, 11, 11, 2, 15, tzinfo=UTC)
        dannye = {"kandidaty": [
            {"id": "https://a/do", "data": "2026-11-11T01:14:59Z", "novyy": True},
            {"id": "https://a/rovno", "data": "2026-11-11T01:15:00Z", "novyy": True},
            {"id": "https://a/staryy", "data": "2026-11-10T20:00:00Z", "novyy": False},
            {"id": "https://a/bez-daty", "novyy": True}],
            "tolko_v_vidennye": ["https://github.com/anthropics/claude-code/issues/1000"]}
        self.assertEqual(publikaciya._idy_progona(dannye, konec),
                         ["https://a/do", "https://github.com/anthropics/claude-code/issues/1000"])


class Uvidennoe(SVremennoyPapkoy):
    def test_uvidennoe_ne_otdaetsya_povtorno(self):
        sost = sostoyanie.zagruzit(self.put)
        kandidaty = [{"id": "https://a/1"}, {"id": "https://a/2"}]
        sostoyanie.zavershit_progon(sost, SEYCHAS, ["https://a/1"], SEYCHAS, self.put)
        sled = sostoyanie.zagruzit(self.put)
        self.assertTrue(sostoyanie.videno(sled, "https://a/1"))
        self.assertEqual([k["id"] for k in sostoyanie.novye(sled, kandidaty)], ["https://a/2"])

    def test_data_pervoy_vstrechi_ne_perezapisyvaetsya(self):
        sost = sostoyanie.pustoe()
        sostoyanie.zapomnit(sost, "https://a/1", SEYCHAS)
        sostoyanie.zapomnit(sost, "https://a/1", SEYCHAS + timedelta(days=3))
        self.assertEqual(sost["vidennye"]["https://a/1"], v_stroku(SEYCHAS))


class ZapisPunkta(SVremennoyPapkoy):
    def test_punkt_srazu_v_fayle(self):
        sost = sostoyanie.zagruzit(self.put)
        sostoyanie.zapisat_punkt(sost, "Новая модель", "https://a/1", SEYCHAS,
                                 idy=["https://x.com/a/status/9"], put=self.put, vid="sayty")
        na_diske = json.loads(self.put.read_text(encoding="utf-8"))
        self.assertEqual(na_diske["opublikovannye"],
                         [{"zagolovok": "Новая модель", "url": "https://a/1", "data": "2026-11-11T02:07:00Z",
                           "vid": "sayty", "oboroty": False}])
        self.assertIn("https://a/1", na_diske["vidennye"])
        self.assertIn("https://x.com/a/status/9", na_diske["vidennye"])
        self.assertIsNone(na_diske["posledniy_sbor"])

    def test_vid_i_oboroty(self):
        sost = sostoyanie.pustoe()
        for vid in ("krupnaya", "sayty", "x", "dengi", "github"):
            sostoyanie.zapisat_punkt(sost, vid, f"https://a/{vid}", SEYCHAS, put=self.put, vid=vid)
        self.assertEqual([(p["vid"], p["oboroty"]) for p in sost["opublikovannye"]],
                         [("krupnaya", False), ("sayty", False), ("x", False), ("dengi", False), ("github", False)])
        with self.assertRaises(ValueError):
            sostoyanie.zapisat_punkt(sost, "?", "https://a/2", SEYCHAS, put=self.put, vid="spisok")

    def test_nabiraet_oboroty_pomechaet_prezhnyuyu_stroku(self):
        sost = sostoyanie.pustoe()
        sostoyanie.zapisat_punkt(sost, "Строкой", "https://a/1", SEYCHAS - timedelta(hours=8), put=self.put,
                                 vid="sayty")
        sostoyanie.zapisat_punkt(sost, "Чужое", "https://a/2", SEYCHAS, put=self.put, vid="x")
        sostoyanie.zapisat_punkt(sost, "Крупной", "https://a/1", SEYCHAS, put=self.put, vid="krupnaya",
                                 oboroty=True)
        self.assertEqual([(p["url"], p["vid"], p["oboroty"])
                          for p in sostoyanie.zagruzit(self.put)["opublikovannye"]],
                         [("https://a/1", "sayty", True), ("https://a/2", "x", False),
                          ("https://a/1", "krupnaya", True)])

    def test_oboroty_tolko_u_krupnoy(self):
        sost = sostoyanie.pustoe()
        sostoyanie.zapisat_punkt(sost, "Строка", "https://a/1", SEYCHAS, put=self.put, vid="sayty", oboroty=True)
        self.assertIs(sost["opublikovannye"][0]["oboroty"], False)


class SlotIObhod(SVremennoyPapkoy):
    def test_sboj_pishet_slot_i_ne_sdvigaet_period(self):
        sost = sostoyanie.pustoe()
        sost["posledniy_sbor"] = "2026-11-10T18:07:00Z"
        sostoyanie.zapisat_slot(sost, "2026-11-11T02:00:00Z", self.put)
        na_diske = sostoyanie.zagruzit(self.put)
        self.assertEqual(na_diske["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertEqual(na_diske["posledniy_sbor"], "2026-11-10T18:07:00Z")
        nachalo, _ = sostoyanie.period(na_diske, SEYCHAS + timedelta(hours=8))
        self.assertEqual(nachalo, datetime(2026, 11, 10, 18, 7, tzinfo=UTC))

    def test_zavershenie_pishet_slot_i_otmetku_obhoda(self):
        sost = sostoyanie.zagruzit(self.put)
        self.assertIsNone(sost["posledniy_slot"])
        self.assertIs(sost["github_obhod"], False)
        sostoyanie.zavershit_progon(sost, SEYCHAS, [], SEYCHAS, self.put, slot="2026-11-11T02:00:00Z",
                                    github_obhod=True)
        na_diske = sostoyanie.zagruzit(self.put)
        self.assertEqual(na_diske["posledniy_slot"], "2026-11-11T02:00:00Z")
        self.assertIs(na_diske["github_obhod"], True)

    def test_otmetka_obhoda_ne_snimaetsya(self):
        sost = sostoyanie.pustoe()
        sost["github_obhod"] = True
        sostoyanie.zavershit_progon(sost, SEYCHAS, [], SEYCHAS, self.put)
        self.assertIs(sostoyanie.zagruzit(self.put)["github_obhod"], True)

    def test_staryy_fayl_bez_novyh_poley(self):
        self.put.write_text(json.dumps({"posledniy_sbor": "2026-11-10T18:07:00Z", "vidennye": {},
                                        "opublikovannye": []}), encoding="utf-8")
        sost = sostoyanie.zagruzit(self.put)
        self.assertIsNone(sost["posledniy_slot"])
        self.assertIs(sost["github_obhod"], False)


class Chistka(SVremennoyPapkoy):
    def test_vidennye_starshe_30_dney_udalyayutsya_krome_issue(self):
        sost = sostoyanie.pustoe()
        sostoyanie.zapomnit(sost, "https://a/staroe", SEYCHAS - timedelta(days=31))
        sostoyanie.zapomnit(sost, "https://a/granica", SEYCHAS - timedelta(days=30))
        sostoyanie.zapomnit(sost, "https://a/svezhee", SEYCHAS - timedelta(days=1))
        sostoyanie.zapomnit(sost, ISSUE, SEYCHAS - timedelta(days=400))
        sostoyanie.pochistit(sost, SEYCHAS)
        self.assertEqual(set(sost["vidennye"]), {"https://a/granica", "https://a/svezhee", ISSUE})

    def test_opublikovannye_starshe_14_dney_udalyayutsya(self):
        sost = sostoyanie.pustoe()
        for dney, url in ((15, "https://a/staroe"), (14, "https://a/granica"), (2, "https://a/svezhee")):
            sost["opublikovannye"].append(
                {"zagolovok": url, "url": url, "data": v_stroku(SEYCHAS - timedelta(days=dney))})
        sostoyanie.pochistit(sost, SEYCHAS)
        self.assertEqual([p["url"] for p in sost["opublikovannye"]], ["https://a/granica", "https://a/svezhee"])

    def test_chistka_pri_zavershenii(self):
        sost = sostoyanie.pustoe()
        sostoyanie.zapomnit(sost, "https://a/staroe", SEYCHAS - timedelta(days=40))
        sostoyanie.zavershit_progon(sost, SEYCHAS, [], SEYCHAS, self.put)
        self.assertNotIn("https://a/staroe", sostoyanie.zagruzit(self.put)["vidennye"])

    def test_raspoznavanie_issue(self):
        self.assertTrue(sostoyanie.eto_issue_github(ISSUE))
        self.assertFalse(sostoyanie.eto_issue_github("https://github.com/anthropics/claude-code/releases/tag/v2.0.0"))
        self.assertFalse(sostoyanie.eto_issue_github("https://x.com/sama/status/1"))


class Fayl(SVremennoyPapkoy):
    def test_isporchennyy_fayl_ne_obnulyaet_sostoyanie(self):
        self.put.write_text("{ не json", encoding="utf-8")
        with self.assertRaises(sostoyanie.OshibkaSostoyaniya):
            sostoyanie.zagruzit(self.put)

    def test_sohranit_i_prochitat(self):
        sost = sostoyanie.pustoe()
        sost["posledniy_sbor"] = "2026-11-10T10:07:00Z"
        sostoyanie.zapomnit(sost, "https://a/1", SEYCHAS)
        sostoyanie.sohranit(sost, self.put)
        self.assertEqual(sostoyanie.zagruzit(self.put), sost)
        self.assertFalse(self.put.with_name(self.put.name + ".tmp").exists())

    def test_put_iz_okruzheniya(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"NEWSAI_SOSTOYANIE": str(self.put)}):
            sostoyanie.sohranit(sostoyanie.pustoe())
        self.assertTrue(self.put.exists())


class Vremya(unittest.TestCase):
    def test_stroka_tuda_obratno(self):
        self.assertEqual(iz_stroki("2026-11-11T02:07:00Z"), SEYCHAS)
        self.assertEqual(iz_stroki("2026-11-11T09:07:00+07:00"), SEYCHAS)
        self.assertEqual(iz_stroki("2026-11-11T02:07:00"), SEYCHAS)

    def test_slot_po_bangkoku(self):
        from newsai.vremya import slot
        self.assertEqual(slot(datetime(2026, 11, 11, 2, 7, tzinfo=UTC)), "09:00")
        self.assertEqual(slot(datetime(2026, 11, 11, 10, 7, tzinfo=UTC)), "17:00")
        self.assertEqual(slot(datetime(2026, 11, 11, 18, 7, tzinfo=UTC)), "01:00")
        self.assertEqual(slot(datetime(2026, 11, 11, 17, 30, tzinfo=UTC)), "17:00")


if __name__ == "__main__":
    unittest.main()
