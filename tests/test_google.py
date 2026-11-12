"""Шов «Сбор Блога Google и Google Developers Blog» с подменённым HTTP по записанным ответам.

Образцы — живые ответы 13.11.2026, урезанные до нескольких записей:
- google-ai.xml — лента ИИ blog.google/innovation-and-ai/technology/ai/rss/: Search + Gemini 4 Hydrogen
  (12.11 18:00), Gemini Decisions API (11.11 18:00), Esther Mahlangu (11.11 09:00), Gemini Intelligence
  (10.11 19:00);
- google-gemini.xml — лента моделей …/models-and-research/gemini-models/rss/: Search + Gemini 4 Hydrogen,
  Gemini 4 Hydrogen для разработчиков, Introducing Gemini 4 Hydrogen (все 12.11 18:00), Gemini 4 Carbon
  (10.11 16:00);
- google-devtools.xml — лента …/technology/developers-tools/rss/: Gemini 4 Hydrogen для разработчиков
  (12.11 18:00), Gemini Decisions API (11.11 18:00), Gemini 4 Carbon для разработчиков (10.11 16:00);
- google-dev-rss.xml — лента developers.googleblog.com/rss/ (без дат): первые четыре записи;
- google-dev-sitemap.xml — карта сайта developers.googleblog.com/sitemap.xml 13.11: главная, адрес без
  lastmod и три записи ленты. Правка образца: из карты убрана запись «From prompt to app: Build with
  Google Maps in AI Studio» (в живой карте она есть, lastmod 2026-11-09) — запись ленты, которой нет в
  карте.
"""
import unittest
from datetime import datetime, timezone
from pathlib import Path

from newsai import razbor, sbor, sostoyanie
from newsai.zaprosy import OshibkaZaprosa, Otvet

UTC = timezone.utc
ZAPISI = Path(__file__).parent / "zapisi"
SLOT = datetime(2026, 11, 12, 18, 8, tzinfo=UTC)
POSLEDNIY = "2026-11-10T23:02:18Z"

AI = "https://blog.google/innovation-and-ai/technology/ai/rss/"
GEMINI = "https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/"
DEVTOOLS = "https://blog.google/innovation-and-ai/technology/developers-tools/rss/"
DEV_RSS = "https://developers.googleblog.com/rss/"
DEV_KARTA = "https://developers.googleblog.com/sitemap.xml"


def otvet(imya, status=200):
    return Otvet(status, (ZAPISI / imya).read_bytes(), {"content-type": "application/xml; charset=utf-8"})


class Set:
    """Подмена HTTP: адрес → ответ (или исключение); запросы копятся."""

    def __init__(self, pravila):
        self.pravila = pravila
        self.zaprosy = []

    def __call__(self, url, zagolovki=None):
        self.zaprosy.append(url)
        o = self.pravila.get(url, Otvet(404))
        if isinstance(o, Exception):
            raise o
        return o


def ist(imya):
    return next(i for i in sbor.zagruzit_istochniki() if i["imya"] == imya)


def sost_s(posledniy=POSLEDNIY, vidennye=None):
    s = sostoyanie.pustoe()
    s["posledniy_sbor"] = posledniy
    s["vidennye"] = dict(vidennye or {})
    return s


def kratko(rez):
    return [(k["istochnik"], k["zagolovok"], k["url"], k["data"], k["novyy"])
            for k in (x.v_slovar() for x in rez["kandidaty"])]


class Nastrojka(unittest.TestCase):
    def test_adresa_v_istochnikah(self):
        g = ist("Блог Google")
        self.assertEqual((g["vid"], g["adres"], g["dop_adresa"]), ("rss", AI, [GEMINI, DEVTOOLS]))
        d = ist("Google Developers Blog")
        self.assertEqual((d["vid"], d["adres"], d["filtr"]), ("rss", DEV_RSS, {"daty_iz_karty": DEV_KARTA}))


class BlogGoogle(unittest.TestCase):
    PRAVILA = {AI: "google-ai.xml", GEMINI: "google-gemini.xml", DEVTOOLS: "google-devtools.xml"}

    def sobrat(self, zamena=None, vidennye=None):
        pravila = {a: otvet(f) for a, f in self.PRAVILA.items()}
        pravila.update(zamena or {})
        set_ = Set(pravila)
        rez = sbor.sobrat([ist("Блог Google")], sost_s(vidennye=vidennye), SLOT, set_)
        return rez, set_

    def test_tri_lenty_odin_istochnik_povtor_odin_kandidat(self):
        rez, set_ = self.sobrat(vidennye={
            "https://blog.google/company-news/outreach-and-initiatives/arts-culture/esther-mahlangu/":
                "2026-11-11T12:00:00Z"})
        self.assertEqual(set_.zaprosy, [AI, GEMINI, DEVTOOLS])
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual(kratko(rez), [
            ("Блог Google", "Google Search gets a major upgrade with Gemini 4 Hydrogen",
             "https://blog.google/products-and-platforms/products/search/gemini-4-hydrogen-search-upgrade/",
             "2026-11-12T18:00:00Z", True),
            ("Блог Google", "Gemini Decisions API: Fast, calibrated multimodal decisions",
             "https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/",
             "2026-11-11T18:00:00Z", True),
            ("Блог Google", "Experience the art and legacy of Dr. Esther Mahlangu",
             "https://blog.google/company-news/outreach-and-initiatives/arts-culture/esther-mahlangu/",
             "2026-11-11T09:00:00Z", False),
            ("Блог Google", "Gemini 4 Hydrogen: everyday coding and agentic workflows for developers",
             "https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-4-hydrogen/",
             "2026-11-12T18:00:00Z", True),
            ("Блог Google", "Introducing Gemini 4 Hydrogen",
             "https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-hydrogen/",
             "2026-11-12T18:00:00Z", True),
        ])

    def test_propushchennaya_novost_13_11_kandidat(self):
        rez, _ = self.sobrat()
        k = [x.v_slovar() for x in rez["kandidaty"]
             if x.url == "https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/"]
        self.assertEqual(len(k), 1)
        self.assertEqual((k[0]["istochnik"], k[0]["novyy"]), ("Блог Google", True))

    def test_odna_lenta_ne_otvetila_ostalnye_kandidaty(self):
        rez, set_ = self.sobrat({GEMINI: Otvet(503)})
        self.assertEqual(set_.zaprosy, [AI, GEMINI, DEVTOOLS])
        self.assertEqual(rez["ne_otvetili"], ["Блог Google"])
        self.assertEqual(rez["itogi"][0]["oshibka"], f"Блог Google: {GEMINI}: ответ 503")
        self.assertEqual([z for _, z, _, _, _ in kratko(rez)], [
            "Google Search gets a major upgrade with Gemini 4 Hydrogen",
            "Gemini Decisions API: Fast, calibrated multimodal decisions",
            "Experience the art and legacy of Dr. Esther Mahlangu",
            "Gemini 4 Hydrogen: everyday coding and agentic workflows for developers",
        ])

    def test_set_i_razbor_odnoy_lenty(self):
        rez, _ = self.sobrat({AI: OshibkaZaprosa(f"{AI}: timed out"),
                              DEVTOOLS: Otvet(200, b"<html>not a feed</html>")})
        self.assertEqual(rez["ne_otvetili"], ["Блог Google"])
        self.assertEqual(rez["itogi"][0]["oshibka"],
                         f"Блог Google: {AI}: timed out; "
                         f"Блог Google: {DEVTOOLS}: не RSS и не Atom: <html>")
        self.assertEqual([z for _, z, _, _, _ in kratko(rez)], [
            "Google Search gets a major upgrade with Gemini 4 Hydrogen",
            "Gemini 4 Hydrogen: everyday coding and agentic workflows for developers",
            "Introducing Gemini 4 Hydrogen",
        ])

    def test_ni_odna_lenta_ne_otvetila(self):
        rez, _ = self.sobrat({AI: Otvet(500), GEMINI: Otvet(500), DEVTOOLS: Otvet(500)})
        self.assertEqual(rez["ne_otvetili"], ["Блог Google"])
        self.assertEqual(rez["kandidaty"], [])


class DevelopersBlog(unittest.TestCase):
    def sobrat(self, karta=None, lenta=None, posledniy=POSLEDNIY):
        set_ = Set({DEV_RSS: lenta or otvet("google-dev-rss.xml"),
                    DEV_KARTA: karta or otvet("google-dev-sitemap.xml")})
        return sbor.sobrat([ist("Google Developers Blog")], sost_s(posledniy), SLOT, set_), set_

    def test_data_iz_karty_net_v_karte_ne_kandidat(self):
        rez, set_ = self.sobrat()
        self.assertEqual(set_.zaprosy, [DEV_RSS, DEV_KARTA])
        self.assertEqual(rez["ne_otvetili"], [])
        self.assertEqual(kratko(rez), [
            ("Google Developers Blog", "A2UI 1.0: Build delightful, fast, reliable, agentic experiences",
             "https://developers.googleblog.com/a2ui-10-build-delightful-fast-reliable-agentic-experiences/",
             "2026-11-12T00:00:00Z", True),
            ("Google Developers Blog",
             "On-Device Speech Recognition and Text-to-Speech in Google AI Edge Gallery",
             "https://developers.googleblog.com/on-device-speech-recognition-and-text-to-speech-in-google-ai-edge-gallery/",
             "2026-11-10T00:00:00Z", True),
            ("Google Developers Blog",
             "Announcing Long-Context Training on TPUs with MaxText Context Parallelism",
             "https://developers.googleblog.com/announcing-long-context-training-on-tpus-with-maxtext-context-parallelism/",
             "2026-11-09T00:00:00Z", True),
        ])

    def test_granica_okna_kak_u_zapisey_bez_vremeni(self):
        rez, _ = self.sobrat(posledniy=None)
        self.assertEqual([d for _, _, _, d, _ in kratko(rez)], ["2026-11-12T00:00:00Z", "2026-11-10T00:00:00Z"])

    def test_karta_ne_otvetila(self):
        for karta in (Otvet(503), OshibkaZaprosa(f"{DEV_KARTA}: timed out")):
            rez, _ = self.sobrat(karta=karta)
            self.assertEqual(rez["ne_otvetili"], ["Google Developers Blog"])
            self.assertEqual(rez["kandidaty"], [])
        rez, _ = self.sobrat(karta=Otvet(503))
        self.assertEqual(rez["itogi"][0]["oshibka"], "Google Developers Blog: карта сайта: ответ 503")

    def test_ni_u_odnoy_zapisi_net_daty(self):
        karta = Otvet(200, (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://developers.googleblog.com/</loc><lastmod>2026-11-11</lastmod></url>'
            '<url><loc>https://developers.googleblog.com/agent-distraction/</loc></url>'
            '</urlset>').encode("utf-8"))
        rez, _ = self.sobrat(karta=karta)
        self.assertEqual(rez["ne_otvetili"], ["Google Developers Blog"])
        self.assertEqual(rez["itogi"][0]["oshibka"],
                         "Google Developers Blog: ни у одной записи ленты нет даты в карте сайта")
        self.assertEqual(rez["kandidaty"], [])

    def test_lenta_ne_otvetila_karta_ne_zaprashivaetsya(self):
        rez, set_ = self.sobrat(lenta=Otvet(502))
        self.assertEqual(set_.zaprosy, [DEV_RSS])
        self.assertEqual(rez["ne_otvetili"], ["Google Developers Blog"])
        self.assertEqual(rez["itogi"][0]["oshibka"], "Google Developers Blog: ответ 502")


class KartaSayta(unittest.TestCase):
    def test_den_bez_vremeni_i_adresa_bez_lastmod(self):
        tekst = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 '<url><loc>https://a.example/x/</loc><lastmod>2026-11-12T21:30:00+00:00</lastmod></url>'
                 '<url><loc>https://a.example/y/</loc></url>'
                 '<url><loc>https://a.example/z/</loc><lastmod>вчера</lastmod></url>'
                 '</urlset>')
        self.assertEqual(razbor.karta_sayta(tekst), {"https://a.example/x/": datetime(2026, 11, 12, tzinfo=UTC)})

    def test_zapisannaya_karta(self):
        karta = razbor.karta_sayta((ZAPISI / "google-dev-sitemap.xml").read_text(encoding="utf-8"))
        self.assertEqual(len(karta), 4)
        self.assertNotIn("https://developers.googleblog.com/get-ready-for-google-io/", karta)

    def test_indeks_kart_otkaz(self):
        with self.assertRaises(razbor.OshibkaRazbora):
            razbor.karta_sayta('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')


if __name__ == "__main__":
    unittest.main()
