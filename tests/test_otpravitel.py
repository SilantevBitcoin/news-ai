"""Шов «Отправитель Bot API с подменённым соединением» (спека, «Сообщения Telegram» — «Отправка», Р67).

Фабрика соединения и пауза подменены, настоящего ожидания и сети нет. Подменённое соединение —
класс Telegram из test_publikaciya: этапы connect / request / getresponse / read берут из очереди
свой сбой или ответ.
"""
import http.client
import os
import ssl
import traceback
import unittest
from unittest import mock

from newsai import publikaciya, sostoyanie
from tests.test_publikaciya import KANAL, LOZHNYY, Otvet, SFaylami, Sboj, Telegram, http_oshibka

SBROS = ConnectionResetError(104, "Connection reset by peer")
RAZRYV = http.client.RemoteDisconnected("Remote end closed connection without response")
ADRES = "https://api.telegram.org/bot123456:poddelka-dlya-testa/sendMessage"
PARAMETRY = {"chat_id": 1, "text": "т"}


def sboi(etap: str, chislo: int) -> list:
    return [Sboj(etap, ConnectionResetError(104, "Connection reset by peer")) for _ in range(chislo)]


class Otpravitel(unittest.TestCase):
    """bot_api с явными фабрикой соединения и паузой."""

    def setUp(self):
        self.pauzy = []

    def bot(self, *otvety):
        self.telegram = Telegram(*otvety)
        return publikaciya.bot_api(LOZHNYY, soedinenie=self.telegram, pauza=self.pauzy.append)

    def otpravit(self, *otvety, parametry=None):
        """Одно сообщение; вернуть результат или OshibkaTelegram."""
        bot = self.bot(*otvety)
        try:
            return bot("sendMessage", parametry or PARAMETRY)
        except publikaciya.OshibkaTelegram as e:
            return e

    def test_vypusk_iz_5_soobshcheniy_odno_soedinenie(self):
        bot = self.bot()
        for n in range(1, 6):
            self.assertEqual(bot("sendMessage", {"chat_id": KANAL, "text": f"сообщение {n}"}), {"message_id": 1})
        self.assertEqual(len(self.telegram.soedineniya), 1)
        soed = self.telegram.soedineniya[0]
        self.assertEqual(soed.podklyucheniy, 1)
        self.assertEqual(len(soed.zaprosy), 5)
        self.assertEqual([(z.method, z.full_url) for z in self.telegram.zaprosy], [("POST", ADRES)] * 5)
        self.assertEqual(self.telegram.zaprosy[0].headers, {"Content-Type": "application/json"})
        self.assertEqual(self.telegram.tela()[4], {"chat_id": KANAL, "text": "сообщение 5"})
        self.assertEqual(self.pauzy, [])
        self.assertFalse(soed.zakryto)
        bot.zakryt()
        self.assertTrue(soed.zakryto)
        self.assertEqual(bot.schetchiki, {"soedineniy": 1, "sboev_do_otpravki": 0, "sboev_posle_otpravki": 0,
                                          "povtorov": 0, "ozhidaniy_429": 0})

    def test_connection_close_sleduyushchiy_na_novom(self):
        bot = self.bot(Otvet(200, Telegram.ok(), zakryt=True))
        bot("sendMessage", PARAMETRY)
        bot("sendMessage", PARAMETRY)
        self.assertEqual(len(self.telegram.soedineniya), 2)
        self.assertEqual([len(s.zaprosy) for s in self.telegram.soedineniya], [1, 1])
        self.assertTrue(self.telegram.soedineniya[0].zakryto)
        self.assertEqual(self.pauzy, [])

    def test_posle_zakryt_novoe_soedinenie(self):
        bot = self.bot()
        bot("sendMessage", PARAMETRY)
        bot.zakryt()
        bot("sendMessage", PARAMETRY)
        self.assertEqual(len(self.telegram.soedineniya), 2)

    def test_sbros_pri_podklyuchenii_povtor_i_uspeh(self):
        rez = self.otpravit(*sboi("connect", 1), {"ok": True, "result": {"message_id": 9}})
        self.assertEqual(rez, {"message_id": 9})
        self.assertEqual(len(self.telegram.zaprosy), 1)
        self.assertEqual(len(self.telegram.soedineniya), 2)
        self.assertEqual(self.pauzy, [5])

    def test_tri_sbrosa_pri_podklyuchenii_obryv_svyazi(self):
        for isklyuchenie in (SBROS, ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
                             TimeoutError("timed out")):
            with self.subTest(isklyuchenie=type(isklyuchenie).__name__):
                self.pauzy = []
                rez = self.otpravit(*(Sboj("connect", isklyuchenie) for _ in range(3)))
                self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
                self.assertEqual(rez.vid, "obryv")
                self.assertEqual(len(self.telegram.zaprosy), 0)
                self.assertEqual(len(self.telegram.soedineniya), 3)
                self.assertEqual(self.pauzy, [5, 5])
        self.assertEqual(str(rez), "sendMessage: нет связи с Bot API после 3 попыток: TimeoutError: timed out")

    def test_tri_sbrosa_tekst_dlya_loga(self):
        rez = self.otpravit(*sboi("connect", 3))
        self.assertEqual(str(rez), "sendMessage: нет связи с Bot API после 3 попыток: "
                                   "ConnectionResetError: [Errno 104] Connection reset by peer")

    def test_zapis_v_zakrytoe_soedinenie_povtor_kak_do_otpravki(self):
        rez = self.otpravit(Sboj("request", BrokenPipeError(32, "Broken pipe")), Telegram.ok())
        self.assertEqual(rez, {"message_id": 1})
        self.assertEqual(len(self.telegram.zaprosy), 1)
        self.assertEqual(len(self.telegram.soedineniya), 2)
        self.assertTrue(self.telegram.soedineniya[0].zakryto)
        self.assertEqual(self.pauzy, [5])

    def test_sboi_do_otpravki_podklyuchenie_i_zapis_vmeste_do_3(self):
        rez = self.otpravit(Sboj("connect", SBROS), Sboj("request", BrokenPipeError(32, "Broken pipe")),
                            Sboj("connect", SBROS))
        self.assertEqual(rez.vid, "obryv")
        self.assertEqual(len(self.telegram.zaprosy), 0)
        self.assertEqual(self.pauzy, [5, 5])

    def test_obryv_pri_chtenii_otveta_odin_povtor(self):
        for sboj in (Sboj("getresponse", RAZRYV), Sboj("read", TimeoutError("timed out")),
                     Sboj("getresponse", SBROS)):
            with self.subTest(etap=sboj.etap, isklyuchenie=type(sboj.isklyuchenie).__name__):
                self.pauzy = []
                rez = self.otpravit(sboj, {"ok": True, "result": {"message_id": 3}})
                self.assertEqual(rez, {"message_id": 3})
                self.assertEqual(len(self.telegram.zaprosy), 2)
                self.assertEqual(len(self.telegram.soedineniya), 2)
                self.assertTrue(self.telegram.soedineniya[0].zakryto)
                self.assertEqual(self.pauzy, [])

    def test_vtoroy_obryv_posle_otpravki_obryv_svyazi(self):
        rez = self.otpravit(Sboj("getresponse", RAZRYV), Sboj("getresponse", RAZRYV), Telegram.ok())
        self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
        self.assertEqual(rez.vid, "obryv")
        self.assertEqual(len(self.telegram.zaprosy), 2)
        self.assertEqual(self.pauzy, [])
        self.assertEqual(str(rez), "sendMessage: нет ответа Bot API после повтора: "
                                   "RemoteDisconnected: Remote end closed connection without response")

    def test_povtor_posle_otpravki_podklyuchaetsya_po_pravilu_do(self):
        rez = self.otpravit(Sboj("getresponse", RAZRYV), Sboj("connect", SBROS), Telegram.ok())
        self.assertEqual(rez, {"message_id": 1})
        self.assertEqual(len(self.telegram.zaprosy), 2)
        self.assertEqual(len(self.telegram.soedineniya), 3)
        self.assertEqual(self.pauzy, [5])

    def test_povtor_posle_otpravki_podklyuchenie_ne_udalos(self):
        rez = self.otpravit(Sboj("getresponse", RAZRYV), *sboi("connect", 3))
        self.assertEqual(rez.vid, "obryv")
        self.assertEqual(len(self.telegram.zaprosy), 1)
        self.assertEqual(self.pauzy, [5, 5])

    def test_schetchiki_na_odno_soobshchenie(self):
        bot = self.bot(*sboi("connect", 2), Telegram.ok(), Sboj("getresponse", RAZRYV), *sboi("connect", 2),
                       Telegram.ok())
        bot("sendMessage", PARAMETRY)
        bot("sendMessage", PARAMETRY)
        self.assertEqual(self.pauzy, [5, 5, 5, 5])
        self.assertEqual(len(self.telegram.zaprosy), 3)
        self.assertEqual(bot.schetchiki, {"soedineniy": 6, "sboev_do_otpravki": 4, "sboev_posle_otpravki": 1,
                                          "povtorov": 5, "ozhidaniy_429": 0})

    def test_429_zhdet_retry_after_i_povtoryaet_na_tom_zhe_soedinenii(self):
        oshibka = http_oshibka(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                     "parameters": {"retry_after": 7}})
        rez = self.otpravit(oshibka, {"ok": True, "result": {"message_id": 5}})
        self.assertEqual(rez, {"message_id": 5})
        self.assertEqual(self.pauzy, [7])
        self.assertEqual(len(self.telegram.zaprosy), 2)
        self.assertEqual(len(self.telegram.soedineniya), 1)
        self.assertEqual(self.telegram.zaprosy[1].full_url, ADRES)
        self.assertEqual(self.telegram.tela()[1], {"chat_id": 1, "text": "т"})

    def test_429_sverh_ozhidaniy_otkaz(self):
        oshibka = http_oshibka(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                     "parameters": {"retry_after": 1}})
        rez = self.otpravit(*[oshibka] * 6, Telegram.ok())
        self.assertEqual(rez.vid, "otkaz")
        self.assertEqual(str(rez), "sendMessage: 429 Too Many Requests")
        self.assertEqual(self.pauzy, [1, 1, 1, 1, 1])
        self.assertEqual(len(self.telegram.zaprosy), 6)

    def test_drugaya_oshibka_bez_povtora_otkaz_i_bez_tokena(self):
        bot = self.bot(http_oshibka(400, {"ok": False, "error_code": 400,
                                          "description": f"Bad Request: token {LOZHNYY}"}))
        with self.assertRaises(publikaciya.OshibkaTelegram) as e:
            bot("sendMessage", PARAMETRY)
        self.assertEqual(e.exception.vid, "otkaz")
        self.assertEqual(str(e.exception), "sendMessage: 400 Bad Request: token ***")
        self.assertEqual(len(self.telegram.zaprosy), 1)
        self.assertEqual(self.pauzy, [])
        bot("sendMessage", PARAMETRY)
        self.assertEqual(len(self.telegram.soedineniya), 1)

    def test_ne_json_otvet_otkaz(self):
        for otvet, tekst in ((Otvet(502, b"<html>Bad Gateway</html>"), "sendMessage: ответ не JSON, HTTP 502"),
                             (Otvet(200, b"\xff\xfe"), "sendMessage: ответ не JSON, HTTP 200"),
                             (["не", "словарь"], "sendMessage: ответ не объект JSON, HTTP 200")):
            with self.subTest(tekst=tekst):
                rez = self.otpravit(otvet)
                self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
                self.assertEqual((rez.vid, str(rez)), ("otkaz", tekst))
                self.assertEqual(len(self.telegram.zaprosy), 1)

    def test_lyuboe_isklyuchenie_bez_tokena_i_v_trassirovke(self):
        for oshibka in (http.client.InvalidURL(f"URL can't contain control characters. '/bot{LOZHNYY}/sendMessage'"),
                        RuntimeError(f"что-то {LOZHNYY}"), KeyError(LOZHNYY)):
            with self.subTest(oshibka=type(oshibka).__name__):
                self.pauzy = []
                rez = self.otpravit(oshibka)
                self.assertIsInstance(rez, publikaciya.OshibkaTelegram)
                self.assertEqual(rez.vid, "otkaz")
                self.assertNotIn(LOZHNYY, str(rez))
                self.assertIn(type(oshibka).__name__, str(rez))
                self.assertNotIn(LOZHNYY, "".join(traceback.format_exception(rez)))
                self.assertEqual(len(self.telegram.zaprosy), 1)
                self.assertEqual(self.pauzy, [])

    def test_invalidurl_pri_zapisi_bez_povtora(self):
        rez = self.otpravit(Sboj("request", http.client.InvalidURL(f"'/bot{LOZHNYY}/sendMessage'")))
        self.assertEqual(rez.vid, "otkaz")
        self.assertEqual(str(rez), "sendMessage: InvalidURL: '/bot***/sendMessage'")
        self.assertEqual(len(self.telegram.soedineniya), 1)
        self.assertEqual(self.pauzy, [])

    def test_obryv_bez_tokena_i_v_trassirovke(self):
        for etap in ("connect", "getresponse"):
            with self.subTest(etap=etap):
                rez = self.otpravit(*(Sboj(etap, OSError(f"сбой /bot{LOZHNYY}/sendMessage")) for _ in range(3)))
                self.assertEqual(rez.vid, "obryv")
                self.assertNotIn(LOZHNYY, str(rez))
                self.assertIn("/bot***/sendMessage", str(rez))
                self.assertNotIn(LOZHNYY, "".join(traceback.format_exception(rez)))

    def test_nastoyashchaya_fabrika_bez_seti(self):
        soed = publikaciya._soedinenie()
        self.assertIsInstance(soed, http.client.HTTPSConnection)
        self.assertEqual((soed.host, soed.port, soed.timeout, soed.sock), ("api.telegram.org", 443, 20, None))
        self.assertEqual(soed._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(soed._context.check_hostname, True)

    def test_nastoyashchaya_pauza(self):
        with mock.patch.object(publikaciya.time, "sleep") as son:
            publikaciya._pauza(5)
        son.assert_called_once_with(5)

    def test_bot_api_bez_parametrov_beret_funkcii_modulya(self):
        telegram = Telegram(*sboi("connect", 1))
        with mock.patch.object(publikaciya, "_soedinenie", telegram), \
                mock.patch.object(publikaciya, "_pauza") as pauza:
            publikaciya.bot_api(LOZHNYY)("sendMessage", PARAMETRY)
        self.assertEqual(len(telegram.soedineniya), 2)
        pauza.assert_called_once_with(5)


class OdinOtpravitelNaKomandu(SFaylami):
    """Одна команда — один отправитель: строка сбоя после обрыва идёт им же; соединения закрыты к
    концу каждой команды."""

    def schyot(self):
        """Подсчитать созданных отправителей."""
        return mock.patch.object(publikaciya, "_otpravitel_po_tokenu", wraps=publikaciya._otpravitel_po_tokenu)

    def test_otkaz_posredi_vypuska_stroka_sboya_tem_zhe_soedineniem(self):
        telegram = Telegram(Telegram.ok(), http_oshibka(400, {"ok": False, "error_code": 400,
                                                              "description": "Bad Request: chat not found"}))
        with self.schyot() as sozdano:
            kod, _, _, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(sozdano.call_count, 1)
        self.assertEqual(len(telegram.soedineniya), 1)
        self.assertEqual(len(telegram.soedineniya[0].zaprosy), 3)
        self.assertTrue(telegram.soedineniya[0].zakryto)

    def test_obryv_svyazi_stroka_sboya_tem_zhe_otpravitelem(self):
        telegram = Telegram(Telegram.ok(), Sboj("getresponse", RAZRYV), Sboj("getresponse", RAZRYV))
        with self.schyot() as sozdano:
            kod, _, err, _ = self.zapusk(telegram)
        self.assertEqual(kod, 1)
        self.assertEqual(sozdano.call_count, 1)
        self.assertIn("newsai opublikovat: отправлено 1 из 7, ошибка: sendMessage: нет ответа Bot API после "
                      "повтора: RemoteDisconnected: Remote end closed connection without response\n", err)
        self.assertEqual(len(telegram.zaprosy), 4)
        self.assertEqual(set(telegram.tela()[3]), {"chat_id", "text"})
        self.assertEqual([len(s.zaprosy) for s in telegram.soedineniya], [2, 1, 1])
        self.assertTrue(all(s.zakryto for s in telegram.soedineniya))
        self.assertEqual(len(sostoyanie.zagruzit(self.sost)["opublikovannye"]), 1)

    def test_vypusk_zakryvaet_soedinenie(self):
        kod, _, err, telegram = self.zapusk()
        self.assertEqual(kod, 0, err)
        self.assertEqual(len(telegram.soedineniya), 1)
        self.assertTrue(telegram.soedineniya[0].zakryto)

    def test_kazhdaya_komanda_zakryvaet_soedinenie(self):
        zayavki = self.papka / "zayavki.json"
        zayavki.write_text("[]", encoding="utf-8")
        prichina = self.papka / "prichina.txt"
        prichina.write_text("сбор публикатора не удался\n", encoding="utf-8")
        komandy = {"sboj": lambda: publikaciya.sboj("сбор упал"),
                   "sboj_iz_fayla": lambda: publikaciya.sboj_iz_fayla(prichina),
                   "nesohraneno": publikaciya.nesohraneno,
                   "storozh": lambda: publikaciya.storozh(zayavki),
                   "opublikovat_fayl_sboya": lambda: publikaciya.opublikovat()}
        for imya, komanda in komandy.items():
            with self.subTest(komanda=imya):
                if imya == "opublikovat_fayl_sboya":
                    self.fayl_sboya({"prichina": "сбор упал"})
                    os.environ["NEWSAI_VYPUSK"] = str(self.papka / "vypuska-net.json")
                telegram = Telegram()
                with mock.patch.object(publikaciya, "_soedinenie", telegram), \
                        mock.patch.object(publikaciya, "_pauza"), mock.patch("sys.stdout"):
                    self.assertEqual(komanda(), 0)
                self.assertEqual(len(telegram.soedineniya), 1)
                self.assertEqual(len(telegram.zaprosy), 1)
                self.assertTrue(telegram.soedineniya[0].zakryto)

    def test_chuzhoy_otpravitel_ne_zakryvaetsya(self):
        telegram = Telegram()
        bot = publikaciya.bot_api(LOZHNYY, soedinenie=telegram, pauza=lambda s: None)
        self.assertEqual(publikaciya.nesohraneno(bot), 0)
        self.assertFalse(telegram.soedineniya[0].zakryto)
        bot.zakryt()
        self.assertTrue(telegram.soedineniya[0].zakryto)


if __name__ == "__main__":
    unittest.main()
