"""Швы «Функция сбора issue для сборщика» и «Сбор агента с записанным файлом issue» (спека, «Швы для
тестов»): записанные ответы GitHub из tests/zapisi/, временные состояние и файл issue.

Время прогона агента — 02:07 UTC (09:07 по Бангкоку), слот начался в 02:00 UTC: файл issue годен, если
собран с 01:40 до 02:07 UTC. Сборщик по cron — 01:40.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from newsai import gitkhab, nastrojki, sbor, sostoyanie
from newsai.zaprosy import OshibkaZaprosa, Otvet

UTC = timezone.utc
ZAPISI = Path(__file__).parent / "zapisi"
TEMP = nastrojki.KOREN / "temp"
SEYCHAS = datetime(2026, 11, 11, 2, 7, tzinfo=UTC)
SOBRANO = datetime(2026, 11, 11, 1, 40, 12, tzinfo=UTC)
ISSUE = "https://github.com/anthropics/claude-code/issues/42796"
BEZ_KOMANDY = "https://github.com/anthropics/claude-code/issues/65697"
GH = "GitHub Claude Code"
ZAKRYTO = Otvet(403, b'{"message":"Resource not accessible by integration"}', {})
OTKAZ_POISKA = "поиск issue: ответ 403, доступ закрыт: Resource not accessible by integration"
OTKAZ_KOMMENTARIEV = "комментарии #65697: ответ 403, доступ закрыт: Resource not accessible by integration"

_zaplatki = []


def setUpModule():
    TEMP.mkdir(exist_ok=True)
    okr = mock.patch.dict(os.environ, {}, clear=False)
    okr.start()
    for imya in ("GITHUB_TOKEN", "GH_TOKEN", "NEWSAI_ISSUE", "NEWSAI_SOSTOYANIE", "NEWSAI_PROGON"):
        os.environ.pop(imya, None)
    sek = mock.patch.object(nastrojki, "FAYL_SEKRETOV", TEMP / "net-takogo-fayla-sekretov")
    sek.start()
    _zaplatki.extend([sek, okr])


def tearDownModule():
    for z in _zaplatki:
        z.stop()


def otvet(imya):
    return Otvet(200, (ZAPISI / imya).read_bytes(), {"content-type": "application/json"})


class Set:
    """Подмена HTTP: первое правило, чья подстрока есть в адресе, даёт ответ; запросы копятся."""

    def __init__(self, pravila):
        self.pravila = pravila
        self.zaprosy = []
        self.zagolovki = []

    def __call__(self, url, zagolovki=None):
        self.zaprosy.append(url)
        self.zagolovki.append(dict(zagolovki or {}))
        for chast, o in self.pravila:
            if chast in url:
                if isinstance(o, Exception):
                    raise o
                return o
        return Otvet(404)


def gh_set(*pervye):
    return Set(list(pervye) + [("api.github.com/search/issues", otvet("gh_poisk.json")),
                               ("issues/42796/comments", otvet("gh_kommentarii_42796.json")),
                               ("issues/65697/comments", otvet("gh_kommentarii_65697.json"))])


def sost_s(posledniy=None, vidennye=None, github_obhod=False):
    s = sostoyanie.pustoe()
    s["posledniy_sbor"] = posledniy
    s["vidennye"] = dict(vidennye or {})
    s["github_obhod"] = github_obhod
    return s


def istochnik(*loginy):
    i = next(i for i in sbor.zagruzit_istochniki() if i["imya"] == GH)
    if loginy:
        i = {**i, "filtr": {**i["filtr"], "loginy_komandy": list(loginy)}}
    return i


def zapis(url=BEZ_KOMANDY, obnovleno="2026-11-10T20:00:00Z", otvet_data="2026-11-10T19:00:00Z", **ost):
    z = {"url": url, "zagolovok": "Linux build", "obnovleno": obnovleno, "reakcii": 655, "kommentariev": 55,
         "sostoyanie": "open", "tekst": "Please ship Linux.",
         "otvet_komandy": {"login": "MarshallOfSound", "data": otvet_data, "tekst": "Beta is out."}}
    z.update(ost)
    return z


def kand(rez):
    return [k.v_slovar() for k in rez["kandidaty"]]


class Papka(unittest.TestCase):
    def setUp(self):
        self._papka = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self._papka.cleanup)
        self.papka = Path(self._papka.name)
        self.put_issue = self.papka / "issue.json"

    def fayl(self, sobrano="2026-11-11T01:40:12Z", nachalo=None, issue=(), dannye=None):
        dannye = dannye if dannye is not None else {"sobrano": sobrano, "nachalo_poiska": nachalo,
                                                    "issue": list(issue)}
        self.put_issue.write_text(json.dumps(dannye, ensure_ascii=False), encoding="utf-8")
        return self.put_issue

    def put_sostoyaniya(self, sost):
        put = self.papka / "sostoyanie.json"
        sostoyanie.sohranit(sost, put)
        return put

    def put_istochnikov(self, *loginy):
        put = self.papka / "istochniki.json"
        put.write_text(json.dumps({"istochniki": [istochnik(*loginy)]}, ensure_ascii=False), encoding="utf-8")
        return put

    def sborshchik(self, sost, set_, sobrano=SOBRANO, loginy=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err):
            kod = gitkhab.sbor_issue(set_, sobrano, self.put_istochnikov(*loginy), self.put_sostoyaniya(sost),
                                     self.put_issue)
        return kod, out.getvalue(), err.getvalue()


class NachaloSlota(unittest.TestCase):
    def test_nachalo_slota_v_utc(self):
        from newsai.vremya import nachalo_slota
        for kogda, nachalo in ((datetime(2026, 11, 11, 2, 7, tzinfo=UTC), "2026-11-11T02:00:00Z"),
                               (datetime(2026, 11, 11, 1, 59, tzinfo=UTC), "2026-11-10T18:00:00Z"),
                               (datetime(2026, 11, 11, 17, 30, tzinfo=UTC), "2026-11-11T10:00:00Z"),
                               (datetime(2026, 11, 11, 18, 0, tzinfo=UTC), "2026-11-11T18:00:00Z")):
            with self.subTest(kogda=kogda):
                self.assertEqual(nachalo_slota(kogda).strftime("%Y-%m-%dT%H:%M:%SZ"), nachalo)


class Sborshchik(Papka):
    """`sbor-issue`: вид файла, период поиска, отбор, отказ."""

    def test_vid_fayla_bez_github_obhod_poisk_bez_perioda(self):
        set_ = gh_set()
        kod, vyvod, _ = self.sborshchik(sost_s("2026-11-10T18:07:00Z"), set_)
        self.assertEqual(kod, 0)
        self.assertNotIn("updated%3A", set_.zaprosy[0])
        self.assertIn("reactions%3A%3E%3D100", set_.zaprosy[0])
        dannye = json.loads(self.put_issue.read_text(encoding="utf-8"))
        self.assertEqual(sorted(dannye), ["issue", "nachalo_poiska", "sobrano"])
        self.assertEqual((dannye["sobrano"], dannye["nachalo_poiska"]), ("2026-11-11T01:40:12Z", None))
        self.assertEqual(len(dannye["issue"]), 1)
        z = dannye["issue"][0]
        self.assertEqual(sorted(z), ["kommentariev", "obnovleno", "otvet_komandy", "reakcii", "sostoyanie",
                                     "tekst", "url", "zagolovok"])
        self.assertEqual({k: v for k, v in z.items() if k not in ("tekst", "otvet_komandy")}, {
            "url": "https://github.com/anthropics/claude-code/issues/42796",
            "zagolovok": "[MODEL] Claude Code is unusable for complex engineering tasks with the Feb updates",
            "obnovleno": "2026-04-24T00:26:08Z", "reakcii": 3286, "kommentariev": 583, "sostoyanie": "closed"})
        self.assertEqual((len(z["tekst"]), z["tekst"][:23]), (400, "### Preflight Checklist"))
        o = z["otvet_komandy"]
        self.assertEqual((sorted(o), o["login"], o["data"], len(o["tekst"]), o["tekst"][:37]),
                         (["data", "login", "tekst"], "bcherny", "2026-04-06T17:55:03Z", 200,
                          "Hi, thanks for the detailed analysis."))
        self.assertEqual(vyvod.splitlines()[:2], ["issue: 1", "nachalo_poiska: null"])
        self.assertFalse(self.put_issue.with_name("issue.json.tmp").exists())

    def test_s_github_obhod_period_iz_sostoyaniya(self):
        set_ = gh_set()
        kod, vyvod, _ = self.sborshchik(sost_s("2026-11-10T18:07:00Z", github_obhod=True), set_)
        self.assertEqual(kod, 0)
        self.assertIn("updated%3A%3E%3D2026-11-10T18%3A07%3A00Z", set_.zaprosy[0])
        dannye = json.loads(self.put_issue.read_text(encoding="utf-8"))
        self.assertEqual(dannye["nachalo_poiska"], "2026-11-10T18:07:00Z")
        self.assertEqual(vyvod.splitlines()[:2], ["issue: 1", "nachalo_poiska: 2026-11-10T18:07:00Z"])

    def test_loginy_komandy(self):
        kod, _, _ = self.sborshchik(sost_s(), gh_set(), loginy=("marshallofsound",))
        self.assertEqual(kod, 0)
        dannye = json.loads(self.put_issue.read_text(encoding="utf-8"))
        self.assertEqual([(z["url"], z["otvet_komandy"]["login"], z["otvet_komandy"]["data"]) for z in dannye["issue"]],
                         [(ISSUE, "bcherny", "2026-04-06T17:55:03Z"),
                          (BEZ_KOMANDY, "MarshallOfSound", "2026-06-30T17:10:33Z")])

    def test_vidennye_ne_popadayut_kommentarii_ne_zaprashivayutsya(self):
        set_ = gh_set()
        kod, vyvod, _ = self.sborshchik(sost_s(vidennye={ISSUE: "2026-11-01T00:00:00Z"}), set_)
        self.assertEqual(kod, 0)
        self.assertEqual(json.loads(self.put_issue.read_text(encoding="utf-8"))["issue"], [])
        self.assertEqual([u for u in set_.zaprosy if "/comments" in u],
                         ["https://api.github.com/repos/anthropics/claude-code/issues/65697/comments?per_page=100&page=1"])
        self.assertEqual(vyvod.splitlines()[0], "issue: 0")

    def test_otkaz_kod_2_fayl_ne_tronut(self):
        prezhniy = b'{"sobrano": "2026-11-10T17:40:05Z", "nachalo_poiska": null, "issue": []}\n'
        for imya, pervye, prichina in (
                ("поиск 403", ("api.github.com/search/issues", ZAKRYTO), OTKAZ_POISKA),
                ("комментарии 403", ("issues/65697/comments", ZAKRYTO), OTKAZ_KOMMENTARIEV),
                ("сеть", ("issues/42796/comments", OshibkaZaprosa("timed out")), "timed out")):
            with self.subTest(imya):
                self.put_issue.write_bytes(prezhniy)
                kod, vyvod, err = self.sborshchik(sost_s(), gh_set(pervye))
                self.assertEqual(kod, 2)
                self.assertEqual(self.put_issue.read_bytes(), prezhniy)
                self.assertEqual(err, f"newsai sbor-issue: {prichina}; файл issue не переписан\n")
                self.assertEqual(vyvod, "")
                self.assertFalse(self.put_issue.with_name("issue.json.tmp").exists())

    def test_token_v_zaprose_no_ne_v_vyvode(self):
        set_ = gh_set()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "proba-token-sborshchika"}):
            kod, vyvod, err = self.sborshchik(sost_s(), set_)
        self.assertEqual(kod, 0)
        self.assertEqual({z.get("Authorization") for z in set_.zagolovki}, {"Bearer proba-token-sborshchika"})
        self.assertNotIn("proba-token-sborshchika", vyvod + err + self.put_issue.read_text(encoding="utf-8"))

    def test_komanda_cherez_tochku_vhoda(self):
        from newsai import __main__ as tochka
        okr = {"NEWSAI_ISSUE": str(self.put_issue),
               "NEWSAI_SOSTOYANIE": str(self.put_sostoyaniya(sost_s("2026-11-10T18:07:00Z")))}
        out = io.StringIO()
        with mock.patch.dict(os.environ, okr), mock.patch("newsai.zaprosy.poluchit", gh_set()), \
                mock.patch.object(gitkhab, "seychas", lambda: SOBRANO), redirect_stdout(out):
            kod = tochka.main(["sbor-issue"])
        self.assertEqual(kod, 0)
        self.assertEqual(json.loads(self.put_issue.read_text(encoding="utf-8"))["sobrano"], "2026-11-11T01:40:12Z")
        self.assertIn("issue: 1\nnachalo_poiska: null\n", out.getvalue())


class Agent(Papka):
    """Сбор агента: запрос к API не прошёл — issue из годного файла сборщика."""

    def sobrat(self, sost, set_, fayl=None, seychas=SEYCHAS, ist=None):
        return sbor.sobrat([ist or istochnik()], sost, seychas, set_, fayl)

    def test_api_otvetil_fayl_ne_chitaetsya(self):
        fayl = self.fayl(issue=[zapis()])
        with mock.patch.object(gitkhab, "prochitat_fayl_issue", wraps=gitkhab.prochitat_fayl_issue) as chtenie:
            rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True), gh_set(), fayl)
        chtenie.assert_not_called()
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE])
        self.assertEqual((rez["ne_otvetili"], rez["itogi"][0]["otchet"]), ([], ""))

    def test_403_na_poiske_iz_fayla(self):
        fayl = self.fayl(nachalo="2026-11-10T18:07:00Z", issue=[zapis()])
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True),
                          gh_set(("api.github.com/search/issues", ZAKRYTO)), fayl)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual(rez["itogi"][0]["oshibka"], None)
        self.assertEqual(rez["itogi"][0]["otchet"], "из файла сборщика, собран 2026-11-11T01:40:12Z")
        self.assertEqual(kand(rez), [{
            "id": BEZ_KOMANDY, "istochnik": GH, "zagolovok": "Linux build", "url": BEZ_KOMANDY,
            "data": "2026-11-10T20:00:00Z",
            "tekst": "Реакций: 655. Комментариев: 55. Состояние: open.\n"
                     "Ответ команды (MarshallOfSound, 2026-11-10T19:00:00Z): Beta is out.\n\n"
                     "Issue: Please ship Linux.",
            "skoro": True, "novyy": True}])
        self.assertFalse(rez["github_polnyy_obhod"])

    def test_403_na_kommentariyah_naydennoe_otbrosheno(self):
        fayl = self.fayl(issue=[zapis()])
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True),
                          gh_set(("issues/65697/comments", ZAKRYTO)), fayl)
        self.assertEqual([k["id"] for k in kand(rez)], [BEZ_KOMANDY])
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual(rez["itogi"][0]["otchet"], "из файла сборщика, собран 2026-11-11T01:40:12Z")

    def test_kandidaty_iz_fayla_i_iz_api_ravny(self):
        loginy = ("marshallofsound",)
        ist = istochnik(*loginy)
        for imya, sost, seychas, polnyy_api in (
                ("с github_obhod", sost_s("2026-04-01T00:00:00Z", github_obhod=True), SEYCHAS, False),
                ("полный обход, ответы старые", sost_s("2026-11-10T18:07:00Z"), SEYCHAS, True),
                ("полный обход, свежий ответ", sost_s(None), datetime(2026, 7, 1, 2, 7, tzinfo=UTC), True)):
            with self.subTest(imya):
                sobrano = seychas.replace(hour=1, minute=40, second=12)
                kod, _, _ = self.sborshchik(sost, gh_set(), sobrano=sobrano, loginy=loginy)
                self.assertEqual(kod, 0)
                iz_api = self.sobrat(sost, gh_set(), self.put_issue, seychas, ist)
                iz_fayla = self.sobrat(sost, gh_set(("api.github.com/search/issues", ZAKRYTO)), self.put_issue,
                                       seychas, ist)
                self.assertEqual(iz_fayla["itogi"][0]["otchet"],
                                 "из файла сборщика, собран " + sobrano.strftime("%Y-%m-%dT%H:%M:%SZ"))
                self.assertEqual(kand(iz_fayla), kand(iz_api))
                self.assertEqual(iz_fayla["tolko_v_vidennye"], iz_api["tolko_v_vidennye"])
                self.assertEqual((iz_api["github_polnyy_obhod"], iz_fayla["github_polnyy_obhod"]), (polnyy_api, False))
                if imya == "с github_obhod":
                    self.assertEqual([(k["id"], k["data"]) for k in kand(iz_fayla)],
                                     [(ISSUE, "2026-04-24T00:26:08Z"), (BEZ_KOMANDY, "2026-10-18T14:14:24Z")])
                    self.assertTrue(kand(iz_fayla)[0]["tekst"].startswith(
                        "Реакций: 3286. Комментариев: 583. Состояние: closed.\n"
                        "Ответ команды (bcherny, 2026-04-06T17:55:03Z): Hi, thanks for the detailed analysis."))
                elif imya == "полный обход, ответы старые":
                    self.assertEqual((kand(iz_fayla), iz_fayla["tolko_v_vidennye"]), ([], [ISSUE, BEZ_KOMANDY]))
                else:
                    self.assertEqual(([k["id"] for k in kand(iz_fayla)], iz_fayla["tolko_v_vidennye"]),
                                     ([BEZ_KOMANDY], [ISSUE]))

    def test_vidennye_i_pervye_24_chasa_k_faylu(self):
        staryy = zapis(url=ISSUE, otvet_data="2026-11-01T00:00:00Z")
        svezhiy = zapis()
        fayl = self.fayl(issue=[staryy, svezhiy])
        zakryt = ("api.github.com/search/issues", ZAKRYTO)
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", {ISSUE: "2026-11-01T00:00:00Z"}, github_obhod=True),
                          gh_set(zakryt), fayl)
        self.assertEqual(([k["id"] for k in kand(rez)], rez["tolko_v_vidennye"]), ([BEZ_KOMANDY], []))
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z"), gh_set(zakryt), fayl)
        self.assertEqual(([k["id"] for k in kand(rez)], rez["tolko_v_vidennye"]), ([BEZ_KOMANDY], [ISSUE]))
        self.assertFalse(rez["github_polnyy_obhod"])

    def test_obnovleno_vne_perioda_iz_fayla_ne_kandidat(self):
        fayl = self.fayl(issue=[zapis(obnovleno="2026-11-10T18:06:59Z"), zapis(url=ISSUE)])
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True),
                          gh_set(("api.github.com/search/issues", ZAKRYTO)), fayl)
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE])

    def test_granicy_godnosti(self):
        zakryt = ("api.github.com/search/issues", ZAKRYTO)
        for sobrano, nachalo in (("2026-11-11T01:40:00Z", "2026-11-10T18:07:00Z"),
                                 ("2026-11-11T02:07:00Z", None)):
            with self.subTest(sobrano=sobrano):
                fayl = self.fayl(sobrano=sobrano, nachalo=nachalo, issue=[zapis()])
                rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True), gh_set(zakryt), fayl)
                self.assertEqual((rez["ne_otvetili"], [k["id"] for k in kand(rez)]), ([], [BEZ_KOMANDY]))

    def test_prichiny_negodnosti(self):
        zakryt = ("api.github.com/search/issues", ZAKRYTO)
        dlinnyy = zapis(tekst="я" * 3001)
        sluchai = (
            ("старше 20 минут до слота", dict(sobrano="2026-11-11T01:39:59Z"), True,
             "собран 2026-11-11T01:39:59Z — раньше 2026-11-11T01:40:00Z, за 20 минут до начала слота "
             "2026-11-11T02:00:00Z"),
            ("позже начала прогона", dict(sobrano="2026-11-11T02:07:01Z"), True,
             "собран 2026-11-11T02:07:01Z — позже начала прогона 2026-11-11T02:07:00Z"),
            ("поиск позже начала периода", dict(nachalo="2026-11-10T18:07:01Z"), True,
             "поиск с 2026-11-10T18:07:01Z — позже начала периода 2026-11-10T18:07:00Z"),
            ("период без github_obhod", dict(nachalo="2026-11-10T18:07:00Z"), False,
             "поиск с 2026-11-10T18:07:00Z, а полного обхода ещё не было — нужен поиск без периода"),
            ("нет файла", None, True, "файла нет"),
            ("не JSON", "{не json", True, "не читается: JSONDecodeError"),
            ("нет поля", dict(dannye={"sobrano": "2026-11-11T01:40:12Z", "issue": []}), True,
             "не по схеме: ждём объект с полями sobrano, nachalo_poiska, issue"),
            ("sobrano не время", dict(sobrano="вчера"), True, "не по схеме: sobrano — не время UTC"),
            ("лишнее поле issue", dict(issue=[{**zapis(), "lishnee": 1}]), True,
             "не по схеме: issue 1: ждём поля url, zagolovok, obnovleno, reakcii, kommentariev, sostoyanie, "
             "tekst, otvet_komandy"),
            ("состояние", dict(issue=[zapis(sostoyanie="merged")]), True,
             "не по схеме: issue 1: sostoyanie — не open и не closed"),
            ("адрес не issue", dict(issue=[zapis(url="https://evil.example/issues/1")]), True,
             "не по схеме: issue 1: url — не адрес issue GitHub"),
            ("реакции строкой", dict(issue=[zapis(reakcii="655")]), True,
             "не по схеме: issue 1: reakcii — не число"),
            ("длинный текст", dict(issue=[dlinnyy]), True, "не по схеме: issue 1: tekst длиннее 3000 знаков"),
        )
        for imya, fayl, github_obhod, prichina in sluchai:
            with self.subTest(imya):
                if self.put_issue.exists():
                    self.put_issue.unlink()
                if isinstance(fayl, str):
                    self.put_issue.write_text(fayl, encoding="utf-8")
                elif fayl is not None:
                    self.fayl(**fayl)
                rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=github_obhod), gh_set(zakryt),
                                  self.put_issue)
                self.assertEqual(rez["ne_otvetili"], [GH])
                self.assertEqual(kand(rez), [])
                self.assertEqual(rez["itogi"][0]["oshibka"],
                                 f"{OTKAZ_POISKA}; файл сборщика issue не годен: {prichina}")

    def test_negodnyy_fayl_naydennoe_do_otkaza_ostaetsya(self):
        rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True),
                          gh_set(("issues/65697/comments", ZAKRYTO)), self.papka / "net.json")
        self.assertEqual(rez["ne_otvetili"], [GH])
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE])
        self.assertEqual(rez["itogi"][0]["oshibka"], f"{OTKAZ_KOMMENTARIEV}; файл сборщика issue не годен: файла нет")

    def test_bez_puti_prezhnee_povedenie(self):
        self.fayl(issue=[zapis()])
        with mock.patch.object(gitkhab, "prochitat_fayl_issue", wraps=gitkhab.prochitat_fayl_issue) as chtenie:
            rez = self.sobrat(sost_s("2026-11-10T18:07:00Z", github_obhod=True),
                              gh_set(("api.github.com/search/issues", ZAKRYTO)))
        chtenie.assert_not_called()
        self.assertEqual(rez["ne_otvetili"], [GH])
        self.assertEqual(rez["itogi"][0]["oshibka"],
                         "поиск issue: ответ 403, доступ закрыт: Resource not accessible by integration")


class Komandy(Papka):
    """Команда `sbor` агента передаёт файл issue, `sbor --konec-iz-zayavki` — нет."""

    def okruzhenie(self):
        self.fayl(issue=[zapis()])
        return {"NEWSAI_ISSUE": str(self.put_issue),
                "NEWSAI_SOSTOYANIE": str(self.put_sostoyaniya(sost_s("2026-11-10T18:07:00Z", github_obhod=True))),
                "NEWSAI_PROGON": str(self.papka / "progon.json")}

    def zapusk(self, *argumenty):
        from newsai import __main__ as tochka
        put_ist = self.put_istochnikov()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.okruzhenie()), \
                mock.patch.object(nastrojki, "FAYL_ISTOCHNIKOV", put_ist), \
                mock.patch.object(sbor, "poluchit_po_seti", gh_set(("api.github.com/search/issues", ZAKRYTO))), \
                mock.patch.object(sbor, "seychas", lambda: SEYCHAS), \
                mock.patch.object(gitkhab, "prochitat_fayl_issue", wraps=gitkhab.prochitat_fayl_issue) as chtenie, \
                redirect_stdout(out), mock.patch("sys.stderr", err):
            kod = tochka.main(list(argumenty))
        progon = json.loads((self.papka / "progon.json").read_text(encoding="utf-8"))
        return kod, out.getvalue(), progon, chtenie

    def test_sbor_agenta_beret_fayl(self):
        kod, vyvod, progon, chtenie = self.zapusk("sbor")
        self.assertEqual(kod, 0)
        self.assertEqual(chtenie.call_count, 1)
        self.assertIn("GitHub Claude Code: 1, новых 1 · из файла сборщика, собран 2026-11-11T01:40:12Z\n", vyvod)
        self.assertEqual(([k["id"] for k in progon["kandidaty"]], progon["ne_otvetili"],
                          progon["github_polnyy_obhod"]), ([BEZ_KOMANDY], [], False))

    def test_sbor_publikatora_fayl_ne_chitaet_dazhe_pri_403(self):
        sobytie = self.papka / "sobytie.json"
        sobytie.write_text(json.dumps({"pull_request": {"created_at": "2026-11-11T02:15:00Z"}}), encoding="utf-8")
        kod, vyvod, progon, chtenie = self.zapusk("sbor", "--konec-iz-zayavki", str(sobytie))
        self.assertEqual(kod, 4)
        chtenie.assert_not_called()
        self.assertEqual((progon["kandidaty"], progon["ne_otvetili"]), ([], [GH]))
        self.assertIn(f"НЕ ОТВЕТИЛ: {OTKAZ_POISKA}\n", vyvod)


if __name__ == "__main__":
    unittest.main()
