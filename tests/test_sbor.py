"""Шов «Функция сбора с подменённым HTTP»: записанные ответы источников из tests/zapisi/.

Образцы — настоящие ответы 11.11.2026, урезанные до нескольких записей; claude-code-changelog.xml —
ответ 12.11.2026 (записи 2.1.322, 2.1.321, 2.1.306). Правки образцов: в claude-code-changelog.xml у
2.1.321 между разделами Added и Changed вставлен раздел Fixed из трёх пунктов релиза v2.1.321 на
github.com (в changelog документации его нет), один — с меткой платформы «Windows:»; в fx_200.json
третий пост помечен репостом.
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

from newsai import nastrojki, sbor, sostoyanie
from newsai.zaprosy import OshibkaZaprosa, Otvet

UTC = timezone.utc
ZAPISI = Path(__file__).parent / "zapisi"
SEYCHAS = datetime(2026, 11, 11, 2, 7, tzinfo=UTC)
TEMP = nastrojki.KOREN / "temp"

_okruzhenie = None
_sekrety = None


def setUpModule():
    global _okruzhenie, _sekrety
    TEMP.mkdir(exist_ok=True)
    _okruzhenie = mock.patch.dict(os.environ, {}, clear=False)
    _okruzhenie.start()
    for imya in ("GITHUB_TOKEN", "GH_TOKEN", "TWITTERAPI_KEY"):
        os.environ.pop(imya, None)
    _sekrety = mock.patch.object(nastrojki, "FAYL_SEKRETOV", TEMP / "net-takogo-fayla-sekretov")
    _sekrety.start()


def tearDownModule():
    _sekrety.stop()
    _okruzhenie.stop()


def otvet(imya, status=200, tip="text/xml; charset=utf-8"):
    return Otvet(status, (ZAPISI / imya).read_bytes(), {"content-type": tip})


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


def sost_s(posledniy=None, vidennye=None, github_obhod=False):
    s = sostoyanie.pustoe()
    s["posledniy_sbor"] = posledniy
    s["vidennye"] = dict(vidennye or {})
    if github_obhod:
        s["github_obhod"] = True
    return s


def nastoyashchie():
    return {i["imya"]: i for i in sbor.zagruzit_istochniki()}


def kand(rez):
    """Кандидаты сбора в виде файла прогона."""
    return [k.v_slovar() for k in rez["kandidaty"]]


class Lenty(unittest.TestCase):
    """RSS и Atom: разбор, фильтры шума, окно охвата, novyy."""

    def sobrat(self, imya, fayl, posledniy="2026-11-01T00:00:00Z", seychas=SEYCHAS, vidennye=None):
        ist = nastoyashchie()[imya]
        rez = sbor.sobrat([ist], sost_s(posledniy, vidennye), seychas, Set([(ist["adres"], otvet(fayl))]))
        self.assertEqual(rez["ne_otvetili"], [], rez["itogi"][0]["oshibka"])
        return kand(rez)

    def test_openai_tolko_product_i_research(self):
        k = self.sobrat("OpenAI", "openai.xml")
        self.assertEqual([x["zagolovok"] for x in k], [
            "Introducing Businesses in ChatGPT", "ChatGPT Enterprise now available in AWS Marketplace",
            "Accelerating engineering for the physical world", "LifeVal"])
        self.assertEqual(k[0], {
            "id": "https://openai.com/index/introducing-businesses-chatgpt", "istochnik": "OpenAI",
            "zagolovok": "Introducing Businesses in ChatGPT",
            "url": "https://openai.com/index/introducing-businesses-chatgpt",
            "data": "2026-11-10T10:00:00Z", "tekst": k[0]["tekst"], "skoro": False, "novyy": True})
        self.assertNotIn("akkaunt", k[0])

    def test_codex_bez_cli_release(self):
        k = self.sobrat("Codex", "codex.xml", posledniy="2026-10-20T00:00:00Z")
        self.assertEqual([x["zagolovok"] for x in k], ["ChatGPT for iOS"])

    def test_ainews_tolko_s_metkoy(self):
        k = self.sobrat("AINews", "latent.xml")
        self.assertEqual(len(k), 1)
        self.assertTrue(k[0]["zagolovok"].startswith("[AINews]"))

    def test_claude_code_changelog_bez_fixed(self):
        k = self.sobrat("Claude Code", "claude-code-changelog.xml", posledniy="2026-10-20T00:00:00Z")
        self.assertEqual([x["zagolovok"] for x in k], ["2.1.322", "2.1.321"])
        self.assertEqual(k[0], {
            "id": "https://code.claude.com/docs/en/changelog#2-1-322", "istochnik": "Claude Code",
            "zagolovok": "2.1.322", "url": "https://code.claude.com/docs/en/changelog#2-1-322",
            "data": "2026-11-10T03:18:49Z",
            "tekst": "- Added fast mode on Vertex in the global, us and eu regions, falling back to standard "
                     "speed if Vertex refuses it",
            "skoro": False, "novyy": True})
        self.assertEqual(k[1]["url"], "https://code.claude.com/docs/en/changelog#2-1-321")
        self.assertNotIn("Fixed", k[1]["tekst"])
        self.assertNotIn("FileChanged", k[1]["tekst"])
        self.assertIn("- [VSCode] Added a warning above the prompt box when both an auth token and an API key are "
                      "set, matching the terminal's startup warning\n- Updated the session-feedback message",
                      k[1]["tekst"])
        self.assertTrue(k[1]["tekst"].startswith("- Added Claude Fable 5.2 (claude-fable-5-2), now the default "
                                                 "Fable model"))

    def test_techmeme_bez_filtra_so_ssylkoy_na_statyu(self):
        k = self.sobrat("Techmeme", "techmeme.xml")
        self.assertEqual(len(k), 2)
        self.assertTrue(k[0]["tekst"].startswith("Статья: https://www.bloomberg.com/"))

    def test_atom_simon_willison_granica_okna(self):
        ist = nastoyashchie()["Simon Willison"]
        set_ = Set([(ist["adres"], otvet("simonw.atom"))])
        progon_ = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
        rez = sbor.sobrat([ist], sost_s("2026-11-10T07:28:21Z"), progon_, set_)
        self.assertEqual([x["zagolovok"] for x in kand(rez)],
                         ["Introducing Our Smallest, Most Efficient Model: Muse Ember"])
        self.assertEqual(rez["okno"].nachalo, datetime(2026, 11, 10, 7, 28, 21, tzinfo=UTC))
        rez = sbor.sobrat([ist], sost_s("2026-11-10T00:00:00Z"),
                          datetime(2026, 11, 10, 7, 28, 21, tzinfo=UTC), set_)
        self.assertEqual([x["zagolovok"] for x in kand(rez)], ["Quoting Prompting Claude Fable 5.2"])

    def test_okno_24_chasa_shire_perioda(self):
        ist = nastoyashchie()["Simon Willison"]
        rez = sbor.sobrat([ist], sost_s("2026-11-10T18:07:00Z",
                                        {"https://simonwillison.net/2026/Nov/10/muse-ember/":
                                         "2026-11-10T10:07:00Z"}),
                          SEYCHAS, Set([(ist["adres"], otvet("simonw.atom"))]))
        self.assertEqual((rez["period"].nachalo, rez["period"].konec),
                         (datetime(2026, 11, 10, 18, 7, tzinfo=UTC), SEYCHAS))
        self.assertEqual((rez["okno"].nachalo, rez["okno"].konec),
                         (datetime(2026, 11, 10, 2, 7, tzinfo=UTC), SEYCHAS))
        self.assertEqual([(x["zagolovok"], x["novyy"]) for x in kand(rez)], [
            ("Introducing Our Smallest, Most Efficient Model: Muse Ember", False),
            ("Quoting Prompting Claude Fable 5.2", True)])

    def test_den_bez_vremeni(self):
        self.assertEqual(len(self.sobrat("Claude Platform", "platform.xml", posledniy="2026-11-10T02:07:00Z")), 2)
        self.assertEqual(self.sobrat("Claude Platform", "platform.xml", posledniy="2026-11-12T02:07:00Z",
                                     seychas=datetime(2026, 11, 12, 10, 7, tzinfo=UTC)), [])

    def test_uvidennoe_ostaetsya_s_novyy_false(self):
        k = self.sobrat("OpenAI", "openai.xml",
                        vidennye={"https://openai.com/index/introducing-businesses-chatgpt": "2026-11-10T10:07:00Z"})
        self.assertEqual([(x["zagolovok"], x["novyy"]) for x in k], [
            ("Introducing Businesses in ChatGPT", False),
            ("ChatGPT Enterprise now available in AWS Marketplace", True),
            ("Accelerating engineering for the physical world", True),
            ("LifeVal", True)])

    def test_pervyy_progon_24_chasa(self):
        k = self.sobrat("OpenAI", "openai.xml", posledniy=None)
        self.assertEqual([x["zagolovok"] for x in k], ["Introducing Businesses in ChatGPT"])


class TipKandidata(unittest.TestCase):
    """Кандидат — один тип с полями; data — datetime до записи файла (находка 13 ревью по осям)."""

    def test_data_datetime_u_vseh_vidov(self):
        vse = nastoyashchie()
        ist = [vse["OpenAI"], vse["Блог Claude"], vse["OpenAI API"], x_istochnik("AnthropicAI"), vse[GH]]
        set_ = gh_set(pervye=[(vse["OpenAI"]["adres"], otvet("openai.xml")),
                              (vse["Блог Claude"]["adres"], otvet("claude-articles.html", tip="text/html")),
                              (vse["OpenAI API"]["adres"], otvet("oai-api-changelog.html", tip="text/html")),
                              ("fxtwitter.com/2/profile/AnthropicAI/",
                               otvet("fx_200.json", tip="application/json"))])
        rez = sbor.sobrat(ist, sost_s("2026-11-01T00:00:00Z", github_obhod=True), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual({k.istochnik for k in rez["kandidaty"]},
                         {"OpenAI", "Блог Claude", "OpenAI API", "X", GH})
        for k in rez["kandidaty"]:
            with self.subTest(k.identifikator):
                self.assertIsInstance(k.data, datetime)
                self.assertEqual(k.data.utcoffset().total_seconds(), 0)

    def test_v_fayle_data_strokoy(self):
        rez = sbor.sobrat([x_istochnik("AnthropicAI")], sost_s(X_PERIOD), SEYCHAS,
                          Set([("fxtwitter.com/2/profile/AnthropicAI/", otvet("fx_200.json", tip="application/json"))]))
        k = rez["kandidaty"][0]
        self.assertEqual(k.data, datetime(2026, 11, 10, 11, 33, 49, tzinfo=UTC))
        self.assertEqual(sorted(k.v_slovar()), ["akkaunt", "data", "id", "istochnik", "novyy", "skoro",
                                                "tekst", "url", "zagolovok"])
        self.assertEqual(k.v_slovar()["data"], "2026-11-10T11:33:49Z")


CHANGELOG = "https://developers.openai.com/api/docs/changelog"


class Stranicy(unittest.TestCase):
    def test_blog_claude_kartochki(self):
        ist = nastoyashchie()["Блог Claude"]
        rez = sbor.sobrat([ist], sost_s("2026-11-10T10:07:00Z"), SEYCHAS,
                          Set([(ist["adres"], otvet("claude-articles.html", tip="text/html"))]))
        k = kand(rez)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertIn("Claude Fable 5.2 and Mythos 5.2", [x["zagolovok"] for x in k])
        self.assertIn("https://claude.com/resources/articles/sheets-whiteboards-data", [x["url"] for x in k])
        self.assertEqual(len(k), len({x["id"] for x in k}))

    def test_openai_api_changelog_po_datam(self):
        ist = nastoyashchie()["OpenAI API"]
        rez = sbor.sobrat([ist], sost_s("2026-11-04T10:00:00Z"), datetime(2026, 11, 5, 20, 0, tzinfo=UTC),
                          Set([(ist["adres"], otvet("oai-api-changelog.html", tip="text/html"))]))
        k = kand(rez)
        self.assertEqual([x["data"][:10] for x in k], ["2026-11-05", "2026-11-04"])
        self.assertEqual(k[0]["id"], "https://developers.openai.com/api/docs/changelog#2026-11-05 Feature · "
                                     "gpt-6-cyber: Use GPT-6-Cyber to investigate vulnerabilities, reproduce "
                                     "them in an authorized test environment, and verify fixes.")
        self.assertEqual(k[0]["url"], "https://developers.openai.com/api/docs/changelog")

    def test_openai_api_odin_den_dve_zapisi_raznye_id(self):
        zapis = ('<div class="mt-5"{yakor}><div class="grid"><div data-variant="outline">Nov 5</div>'
                 '<div data-variant="soft">{metka}</div>'
                 '<div class="_MarkdownContent_x">{tekst}</div></div></div>')
        stranica = ('<h3 class="_ChangelogSectionTitle_a">November, 2026</h3>'
                    + zapis.format(yakor=' id="nov-5-a"', metka="Feature", tekst="<p>Added A. More.</p>")
                    + zapis.format(yakor="", metka="Update", tekst="<p>Changed B.</p>")).encode()
        ist = nastoyashchie()["OpenAI API"]
        rez = sbor.sobrat([ist], sost_s("2026-11-05T00:00:00Z"), datetime(2026, 11, 5, 20, 0, tzinfo=UTC),
                          Set([(ist["adres"], Otvet(200, stranica, {"content-type": "text/html"}))]))
        self.assertEqual([(x["id"], x["url"]) for x in kand(rez)], [
            (CHANGELOG + "#2026-11-05 Feature: Added A.", CHANGELOG + "#nov-5-a"),
            (CHANGELOG + "#2026-11-05 Update: Changed B.", CHANGELOG)])


X_PERIOD = "2026-11-06T00:00:00Z"


def x_istochnik(*akkaunty):
    return {**nastoyashchie()["X"], "akkaunty": list(akkaunty)}


class X(unittest.TestCase):
    def test_200_i_204(self):
        set_ = Set([("fxtwitter.com/2/profile/AnthropicAI/", otvet("fx_200.json", tip="application/json")),
                    ("fxtwitter.com/2/profile/OpenAI/", Otvet(204)),
                    ("syndication.twitter.com", otvet("syndication.html", tip="text/html"))])
        rez = sbor.sobrat([x_istochnik("AnthropicAI", "OpenAI")], sost_s(X_PERIOD), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual(rez["itogi"][0]["otchet"], "пути: FxTwitter — 2")
        k = kand(rez)
        self.assertEqual([x["data"] for x in k],
                         ["2026-11-10T11:33:49Z", "2026-11-09T19:00:21Z", "2026-11-06T19:49:34Z"])
        self.assertEqual({x["akkaunt"] for x in k}, {"AnthropicAI"})
        self.assertEqual(k[0]["id"], "https://x.com/AnthropicAI/status/2120117722230968385")
        self.assertEqual(k[0]["url"], "https://x.com/AnthropicAI/status/2120117722230968385")
        self.assertEqual(k[0]["istochnik"], "X")
        self.assertTrue(k[0]["novyy"])
        self.assertEqual(len(k[0]["zagolovok"]), 120)
        self.assertTrue(k[0]["tekst"].startswith("@AnthropicAI: Anthropic has launched the roadshow"))
        self.assertIn("since=1793923200", set_.zaprosy[0] + set_.zaprosy[1])

    def test_since_ot_nachala_okna(self):
        set_ = Set([("fxtwitter.com/2/profile/AnthropicAI/", otvet("fx_200.json", tip="application/json"))])
        rez = sbor.sobrat([x_istochnik("AnthropicAI")], sost_s("2026-11-10T20:07:00Z"), SEYCHAS, set_)
        self.assertIn("since=1794276420", set_.zaprosy[0])
        self.assertEqual([(x["data"], x["akkaunt"]) for x in kand(rez)],
                         [("2026-11-10T11:33:49Z", "AnthropicAI")])

    def test_zapasnoy_put_lenta_x(self):
        set_ = Set([("fxtwitter.com", Otvet(503)),
                    ("syndication.twitter.com/srv/timeline-profile/screen-name/AnthropicAI",
                     otvet("syndication.html", tip="text/html"))])
        rez = sbor.sobrat([x_istochnik("AnthropicAI")], sost_s(X_PERIOD), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertIn("лента X — 1", rez["itogi"][0]["otchet"])
        self.assertEqual(len(kand(rez)), 4)
        self.assertEqual(kand(rez)[0]["akkaunt"], "AnthropicAI")
        self.assertNotIn("t.co/", kand(rez)[0]["tekst"])

    def test_ni_odin_put_u_akkaunta_x_v_ne_otvetivshih(self):
        set_ = Set([("fxtwitter.com/2/profile/AnthropicAI/", otvet("fx_200.json", tip="application/json")),
                    ("fxtwitter.com", OshibkaZaprosa("timeout")), ("syndication", Otvet(429))])
        rez = sbor.sobrat([x_istochnik("AnthropicAI", "sama")], sost_s(X_PERIOD), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], ["X"])
        self.assertEqual(len(kand(rez)), 3)
        self.assertIn("sama", rez["itogi"][0]["oshibka"])

    TVITY = (b'{"status":"success","data":{"tweets":[{"id":"777","text":"proba twitterapi",'
             b'"createdAt":"Tue Nov 10 11:33:49 +0000 2026"}]}}')

    def tretiy_put(self, okruzhenie=None, stroka_fayla=None):
        set_ = Set([("fxtwitter.com", Otvet(503)), ("syndication", Otvet(429)),
                    ("api.twitterapi.io", Otvet(200, self.TVITY, {"content-type": "application/json"}))])
        with tempfile.TemporaryDirectory(dir=TEMP) as papka:
            fayl = Path(papka) / "sekrety"
            if stroka_fayla:
                fayl.write_text(stroka_fayla, encoding="utf-8")
            with mock.patch.object(nastrojki, "FAYL_SEKRETOV", fayl), \
                    mock.patch.dict(os.environ, okruzhenie or {}):
                rez = sbor.sobrat([x_istochnik("AnthropicAI")], sost_s(X_PERIOD), SEYCHAS, set_)
        return rez, set_

    def test_klyuch_twitterapi_iz_fayla_sekretov(self):
        rez, set_ = self.tretiy_put(stroka_fayla="# проба\nTWITTERAPI_KEY=klyuch-iz-fayla\n")
        self.assertEqual(rez["itogi"][0]["otchet"], "пути: twitterapi.io — 1")
        self.assertEqual([(x["id"], x["tekst"]) for x in kand(rez)],
                         [("https://x.com/AnthropicAI/status/777", "@AnthropicAI: proba twitterapi")])
        self.assertEqual([z.get("X-API-Key") for z in set_.zagolovki if z], ["klyuch-iz-fayla"])

    def test_klyuch_twitterapi_okruzhenie_vazhnee_fayla(self):
        _, set_ = self.tretiy_put({"TWITTERAPI_KEY": "klyuch-okruzheniya"},
                                  "TWITTERAPI_KEY=klyuch-iz-fayla\n")
        self.assertEqual([z.get("X-API-Key") for z in set_.zagolovki if z], ["klyuch-okruzheniya"])

    def test_bez_klyucha_twitterapi_ne_zovetsya(self):
        rez, set_ = self.tretiy_put()
        self.assertEqual(rez["ne_otvetili"], ["X"])
        self.assertFalse([u for u in set_.zaprosy if "twitterapi" in u])


ISSUE = "https://github.com/anthropics/claude-code/issues/42796"
BEZ_KOMANDY = "https://github.com/anthropics/claude-code/issues/65697"
GH = "GitHub Claude Code"


def gh_set(**ost):
    pravila = [("api.github.com/search/issues", otvet("gh_poisk.json", tip="application/json")),
               ("issues/42796/comments", otvet("gh_kommentarii_42796.json", tip="application/json")),
               ("issues/65697/comments", otvet("gh_kommentarii_65697.json", tip="application/json"))]
    return Set(list(ost.get("pervye", [])) + pravila)


class GitHub(unittest.TestCase):
    def ist(self, **filtr):
        i = nastoyashchie()[GH]
        return {**i, "filtr": {**i["filtr"], **filtr}}

    def test_issue_s_otvetom_komandy_skoro(self):
        set_ = gh_set()
        rez = sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual([(k["id"], k["skoro"], k["novyy"]) for k in kand(rez)], [(ISSUE, True, True)])
        self.assertIn("bcherny", kand(rez)[0]["tekst"])
        self.assertIn("updated%3A%3E%3D2026-11-10T18%3A07%3A00Z", set_.zaprosy[0])
        self.assertIn("reactions%3A%3E%3D100", set_.zaprosy[0])
        self.assertFalse(rez["github_polnyy_obhod"])

    def test_issue_vyhodit_odin_raz(self):
        rez = sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", {ISSUE: "2026-11-01T00:00:00Z"},
                                               github_obhod=True), SEYCHAS, gh_set())
        self.assertEqual(kand(rez), [])
        self.assertEqual(rez["tolko_v_vidennye"], [])
        self.assertEqual(rez["ne_otvetili"], [])

    def test_otvet_ot_logina_iz_spiska_komandy(self):
        rez = sbor.sobrat([self.ist(loginy_komandy=["marshallofsound"])],
                          sost_s("2026-10-01T00:00:00Z", github_obhod=True), SEYCHAS, gh_set())
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE, BEZ_KOMANDY])
        self.assertIn("Ответ команды (MarshallOfSound, 2026-06-30T17:10:33Z)", kand(rez)[1]["tekst"])

    def test_polnyy_obhod_poka_net_otmetki(self):
        set_ = gh_set()
        rez = sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z"), SEYCHAS, set_)
        self.assertNotIn("updated%3A", set_.zaprosy[0])
        self.assertEqual(kand(rez), [])
        self.assertEqual(rez["tolko_v_vidennye"], [ISSUE])
        self.assertTrue(rez["github_polnyy_obhod"])

    def test_polnyy_obhod_svezhiy_otvet_v_vypusk(self):
        rez = sbor.sobrat([self.ist()], sost_s(None), datetime(2026, 4, 7, 2, 7, tzinfo=UTC), gh_set())
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE])
        self.assertEqual(rez["tolko_v_vidennye"], [])
        self.assertTrue(rez["github_polnyy_obhod"])

    def test_sboy_posredi_polnogo_obhoda_nichego(self):
        limit = Otvet(403, b'{"message":"API rate limit exceeded"}', {"x-ratelimit-remaining": "0"})
        rez = sbor.sobrat([self.ist()], sost_s(None), SEYCHAS, gh_set(pervye=[("issues/65697/comments", limit)]))
        self.assertEqual(rez["ne_otvetili"], [GH])
        self.assertEqual(kand(rez), [])
        self.assertEqual(rez["tolko_v_vidennye"], [])
        self.assertFalse(rez["github_polnyy_obhod"])
        self.assertEqual(rez["itogi"][0]["oshibka"], "комментарии #65697: исчерпан лимит запросов GitHub API "
                                                     "(нет GITHUB_TOKEN: без него 60 запросов в час)")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "proba-okruzhenie"}):
            rez = sbor.sobrat([self.ist()], sost_s(None), SEYCHAS, gh_set(pervye=[("issues/65697/comments", limit)]))
        self.assertEqual(rez["itogi"][0]["oshibka"], "комментарии #65697: исчерпан лимит запросов GitHub API")

    def test_limit_api_v_obychnom_progone(self):
        limit = Otvet(403, b'{"message":"API rate limit exceeded"}', {"x-ratelimit-remaining": "0"})
        rez = sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS,
                          gh_set(pervye=[("issues/65697/comments", limit)]))
        self.assertEqual(rez["ne_otvetili"], [GH])
        self.assertEqual([k["id"] for k in kand(rez)], [ISSUE])

    def test_403_dostup_s_prichinoy(self):
        zakryto = Otvet(403, b'{"message":"Resource not accessible by integration"}', {})
        rez = sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS,
                          Set([("api.github.com/search/issues", zakryto)]))
        self.assertEqual(rez["ne_otvetili"], [GH])
        self.assertEqual(rez["itogi"][0]["oshibka"],
                         "поиск issue: ответ 403, доступ закрыт: Resource not accessible by integration")

    def test_token_iz_okruzheniya(self):
        set_ = gh_set()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "proba-okruzhenie", "GH_TOKEN": "ne-etot"}):
            sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS, set_)
        self.assertEqual({z.get("Authorization") for z in set_.zagolovki}, {"Bearer proba-okruzhenie"})

    def test_gh_token_ne_chitaetsya(self):
        set_ = gh_set()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "ne-etot"}):
            sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS, set_)
        self.assertEqual({z.get("Authorization") for z in set_.zagolovki}, {None})

    def test_token_iz_fayla_sekretov(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as papka:
            fayl = Path(papka) / "sekrety"
            fayl.write_text("# проба\nGITHUB_TOKEN=proba-fayl\n", encoding="utf-8")
            set_ = gh_set()
            with mock.patch.object(nastrojki, "FAYL_SEKRETOV", fayl):
                sbor.sobrat([self.ist()], sost_s("2026-11-10T18:07:00Z", github_obhod=True), SEYCHAS, set_)
        self.assertEqual({z.get("Authorization") for z in set_.zagolovki}, {"Bearer proba-fayl"})


class Sboi(unittest.TestCase):
    def test_upavshiy_istochnik_ne_meshaet_ostalnym(self):
        vse = nastoyashchie()
        ist = [vse["OpenAI"], vse["DeepMind"], vse["Cursor"]]
        set_ = Set([(vse["OpenAI"]["adres"], otvet("openai.xml")),
                    (vse["DeepMind"]["adres"], OshibkaZaprosa("timed out")),
                    (vse["Cursor"]["adres"], Otvet(200, b"<html>not a feed</html>"))])
        rez = sbor.sobrat(ist, sost_s("2026-11-01T00:00:00Z"), SEYCHAS, set_)
        self.assertEqual(rez["ne_otvetili"], ["DeepMind", "Cursor"])
        self.assertEqual(len(kand(rez)), 4)

    def test_oshibka_http(self):
        ist = nastoyashchie()["Ben's Bites"]
        rez = sbor.sobrat([ist], sost_s(None), SEYCHAS, Set([(ist["adres"], Otvet(500))]))
        self.assertEqual(rez["ne_otvetili"], ["Ben's Bites"])


class Komanda(unittest.TestCase):
    """sbor() целиком: файлы источников, состояния и прогона."""

    def setUp(self):
        self._papka = tempfile.TemporaryDirectory(dir=TEMP)
        self.papka = Path(self._papka.name)

    def tearDown(self):
        self._papka.cleanup()

    def test_fayl_progona(self):
        vse = nastoyashchie()
        put_ist = self.papka / "istochniki.json"
        put_ist.write_text(json.dumps({"istochniki": [vse["OpenAI"], vse["DeepMind"], vse[GH],
                                                      x_istochnik("AnthropicAI")]},
                                      ensure_ascii=False), encoding="utf-8")
        put_sost = self.papka / "sostoyanie.json"
        put_progona = self.papka / "progon.json"
        set_ = gh_set(pervye=[(vse["OpenAI"]["adres"], otvet("openai.xml")),
                              (vse["DeepMind"]["adres"], Otvet(503)),
                              ("fxtwitter.com/2/profile/AnthropicAI/", otvet("fx_200.json", tip="application/json"))])
        vyvod = io.StringIO()
        with redirect_stdout(vyvod):
            kod = sbor.sbor(set_, SEYCHAS, put_ist, put_sost, put_progona)
        self.assertEqual(kod, 0)
        dannye = json.loads(put_progona.read_text(encoding="utf-8"))
        self.assertEqual(sorted(dannye), ["github_polnyy_obhod", "kandidaty", "nachalo_okna", "nachalo_perioda",
                                          "nachalo_progona", "ne_otvetili", "pervyy_progon", "tolko_v_vidennye"])
        self.assertEqual(dannye["nachalo_progona"], "2026-11-11T02:07:00Z")
        self.assertEqual(dannye["nachalo_perioda"], "2026-11-10T02:07:00Z")
        self.assertEqual(dannye["nachalo_okna"], "2026-11-10T02:07:00Z")
        self.assertTrue(dannye["pervyy_progon"])
        self.assertEqual([(k["istochnik"], k["zagolovok"][:33], k["novyy"], k.get("akkaunt"))
                          for k in dannye["kandidaty"]],
                         [("OpenAI", "Introducing Businesses in ChatGPT", True, None),
                          ("X", "Anthropic has launched the roadsh", True, "AnthropicAI")])
        self.assertEqual(dannye["ne_otvetili"], ["DeepMind"])
        self.assertEqual(dannye["tolko_v_vidennye"], [ISSUE])
        self.assertTrue(dannye["github_polnyy_obhod"])
        self.assertFalse(put_sost.exists())
        tekst_ = vyvod.getvalue()
        self.assertIn("OpenAI: 1, новых 1", tekst_)
        self.assertIn("окно охвата: 2026-11-10T02:07:00Z — 2026-11-11T02:07:00Z", tekst_)
        self.assertIn("не ответили: DeepMind", tekst_)

    def test_sboy_polnogo_obhoda_v_fayle(self):
        vse = nastoyashchie()
        put_ist = self.papka / "istochniki.json"
        put_ist.write_text(json.dumps({"istochniki": [vse[GH]]}, ensure_ascii=False), encoding="utf-8")
        put_progona = self.papka / "progon.json"
        set_ = gh_set(pervye=[("issues/65697/comments", OshibkaZaprosa("timed out"))])
        with redirect_stdout(io.StringIO()):
            sbor.sbor(set_, SEYCHAS, put_ist, self.papka / "sostoyanie.json", put_progona)
        dannye = json.loads(put_progona.read_text(encoding="utf-8"))
        self.assertEqual((dannye["kandidaty"], dannye["tolko_v_vidennye"], dannye["ne_otvetili"],
                          dannye["github_polnyy_obhod"]), ([], [], [GH], False))


class SborPublikatora(unittest.TestCase):
    """`sbor --konec-iz-zayavki <event.json>`: конец периода — открытие заявки из события GitHub."""

    def setUp(self):
        self._papka = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self._papka.cleanup)
        self.papka = Path(self._papka.name)
        self.sobytie = json.loads((Path(__file__).parent / "proverka" / "sobytie.json").read_text(encoding="utf-8"))
        self.put_progona = self.papka / "sbor.json"

    def zapisat_sobytie(self, created_at=None):
        s = json.loads(json.dumps(self.sobytie))
        if created_at is None:
            del s["pull_request"]["created_at"]
        else:
            s["pull_request"]["created_at"] = created_at
        put = self.papka / "sobytie.json"
        put.write_text(json.dumps(s), encoding="utf-8")
        return put

    def istochniki(self, *imena):
        vse = nastoyashchie()
        put = self.papka / "istochniki.json"
        put.write_text(json.dumps({"istochniki": [vse[i] for i in imena]}, ensure_ascii=False), encoding="utf-8")
        return put

    def sostoyanie_(self, posledniy):
        put = self.papka / "sostoyanie.json"
        sostoyanie.sohranit(sost_s(posledniy), put)
        return put

    def test_konec_perioda_otkrytie_zayavki(self):
        ist = nastoyashchie()["Simon Willison"]
        sobytie = self.zapisat_sobytie("2026-11-10T07:28:21Z")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err):
            kod = sbor.sbor_publikatora(sobytie, Set([(ist["adres"], otvet("simonw.atom"))]),
                                        self.istochniki("Simon Willison"), self.sostoyanie_("2026-11-10T00:00:00Z"),
                                        self.put_progona)
        self.assertEqual(kod, 0, err.getvalue())
        dannye = json.loads(self.put_progona.read_text(encoding="utf-8"))
        self.assertEqual(dannye["nachalo_progona"], "2026-11-10T07:28:21Z")
        self.assertEqual([k["zagolovok"] for k in dannye["kandidaty"]], ["Quoting Prompting Claude Fable 5.2"])

    def test_sobytie_bez_vremeni_otkrytiya_otkaz(self):
        for plohoe in (None, "вчера", 12345):
            with self.subTest(created_at=plohoe):
                sobytie = self.zapisat_sobytie(plohoe)
                err = io.StringIO()
                with mock.patch("sys.stderr", err):
                    kod = sbor.sbor_publikatora(sobytie, Set([]), self.istochniki("Simon Willison"),
                                                self.sostoyanie_(None), self.put_progona)
                self.assertEqual(kod, 2)
                self.assertIn("в событии заявки нет времени открытия (pull_request.created_at)", err.getvalue())
                self.assertFalse(self.put_progona.exists())

    def test_net_fayla_sobytiya_otkaz(self):
        with mock.patch("sys.stderr", io.StringIO()):
            kod = sbor.sbor_publikatora(self.papka / "net.json", Set([]), self.istochniki("Simon Willison"),
                                        self.sostoyanie_(None), self.put_progona)
        self.assertEqual(kod, 2)
        self.assertFalse(self.put_progona.exists())

    def test_ne_otvetil_ni_odin_istochnik_kod_4(self):
        vse = nastoyashchie()
        set_ = Set([(vse["DeepMind"]["adres"], Otvet(503)), (vse["Cursor"]["adres"], OshibkaZaprosa("timed out"))])
        sobytie = self.zapisat_sobytie("2026-11-11T02:15:00Z")
        with redirect_stdout(io.StringIO()):
            kod = sbor.sbor_publikatora(sobytie, set_, self.istochniki("DeepMind", "Cursor"),
                                        self.sostoyanie_("2026-11-10T18:07:00Z"), self.put_progona)
            kod_agenta = sbor.sbor(set_, SEYCHAS, self.istochniki("DeepMind", "Cursor"),
                                   self.sostoyanie_("2026-11-10T18:07:00Z"), self.papka / "agent.json")
        self.assertEqual((kod, kod_agenta), (4, 0))

    def test_komanda_sobytie_bez_vremeni_kod_2(self):
        from newsai import __main__ as tochka
        sobytie = self.zapisat_sobytie(None)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"NEWSAI_PROGON": str(self.put_progona)}), mock.patch("sys.stderr", err):
            kod = tochka.main(["sbor", "--konec-iz-zayavki", str(sobytie)])
        self.assertEqual(kod, 2)
        self.assertIn("pull_request.created_at", err.getvalue())
        self.assertFalse(self.put_progona.exists())


if __name__ == "__main__":
    unittest.main()
