"""Точка входа: python -m newsai sbor [--konec-iz-zayavki <event.json>] | sbor-issue | tekst <url>
| proverit [...]
| opublikovat [--suhoj]
| sboj --prichina-iz <файл> [--slot-iz-zayavki <event.json>] | nesohraneno | storozh --zayavki <файл>."""
from __future__ import annotations

import argparse
import sys


def razbor() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m newsai", description="Выпуски канала «ИИ-сводка».")
    k = p.add_subparsers(dest="komanda", required=True, metavar="команда")
    sb = k.add_parser("sbor", help="собрать кандидатов и не ответивших в файл прогона")
    sb.add_argument("--konec-iz-zayavki", metavar="event.json",
                    help="сбор публикатора: конец периода — pull_request.created_at события GitHub, "
                         "а не часы машины; коды: 2 — нет времени открытия, 4 — не ответил ни один источник")
    k.add_parser("sbor-issue", help="поиск issue GitHub Claude Code в файл issue (NEWSAI_ISSUE или "
                                    "dannye/issue.json); зовут публикатор после заявки и сборщик issue; "
                                    "код 2 — запрос к API не прошёл или поиск пуст и без токена, файл "
                                    "не переписан")
    t = k.add_parser("tekst", help="текст страницы без разметки, до 20 000 знаков")
    t.add_argument("url")
    v = k.add_parser("proverit", help="проверить файл выпуска или сбоя против прогона и состояния; "
                                      "ничего не отправляет")
    for flag, chto in (("--vypusk", "файл выпуска"), ("--sboj", "файл сбоя"), ("--sostoyanie", "файл состояния")):
        v.add_argument(flag, metavar="файл", help=f"{chto} (по умолчанию — из папки данных)")
    v.add_argument("--progon", metavar="файл",
                   help="агент: свой файл прогона (по умолчанию — из папки данных); с --zayavka — сбор "
                        "публикатора, без умолчания: файл прогона заявки не читается")
    v.add_argument("--zayavka", metavar="event.json",
                   help="публикатор: событие GitHub заявки; без --progon — заявка, слот, файлы, схема, "
                        "длина, свежесть; с --progon — ещё сверка со сбором публикатора; код 3 — чужая "
                        "заявка или не своего слота, закрыть без публикации")
    v.add_argument("--fayly", metavar="файл", help="список файлов заявки: строки «status, табуляция, имя»")
    v.add_argument("--vyhod", metavar="файл",
                   help="публикатор (с --zayavka и --progon): очищенный выпуск — без пунктов, "
                        "чьих ссылок нет в сборе публикатора")
    o = k.add_parser("opublikovat", help="файл выпуска → сообщения в канал, файл сбоя → строка сбоя")
    o.add_argument("--suhoj", action="store_true", help="напечатать запросы к Bot API вместо отправки")
    s = k.add_parser("sboj", help="строка сбоя в канал человеческими словами (причина — в лог)")
    s.add_argument("--prichina-iz", required=True, metavar="файл",
                   help="файл, первая строка которого — причина (до 200 знаков)")
    s.add_argument("--slot-iz-zayavki", metavar="event.json",
                   help="публикатор: событие GitHub своей заявки — слот её открытия пишется в "
                        "posledniy_slot состояния, период не сдвигается")
    k.add_parser("nesohraneno",
                 help="сообщение в канал «состояние не сохранено — следующий выпуск может повторить новости»")
    spravka_storozha = ("судит последний слот, чьё окно (начало + 40 минут) закрылось к запуску: слот не "
                        "обработан и заявки агента к нему нет — «⚠️ Выпуск <час этого слота> не собран: агент "
                        "не прислал выпуск»; в режиме проверки (NEWSAI_REZHIM_PROVERKI=1) молчит, код 0")
    st = k.add_parser("storozh", help=spravka_storozha, description=spravka_storozha)
    st.add_argument("--zayavki", required=True, metavar="файл",
                    help="список заявок — ответ REST GitHub pulls?state=all")
    return p


def main(argv: list[str] | None = None) -> int:
    for potok in (sys.stdout, sys.stderr):
        if hasattr(potok, "reconfigure"):
            potok.reconfigure(encoding="utf-8")
    args = razbor().parse_args(argv)
    try:
        if args.komanda == "sbor":
            if args.konec_iz_zayavki:
                from .sbor import sbor_publikatora
                return sbor_publikatora(args.konec_iz_zayavki)
            from .nastrojki import fayl_issue
            from .sbor import sbor
            return sbor(put_issue=fayl_issue())
        if args.komanda == "sbor-issue":
            from .gitkhab import sbor_issue
            return sbor_issue()
        if args.komanda == "tekst":
            from .tekst import tekst
            print(tekst(args.url))
            return 0
        if args.komanda == "proverit":
            if (args.zayavka is None) != (args.fayly is None):
                print("newsai proverit: --zayavka и --fayly задаются вместе", file=sys.stderr)
                return 2
            if (args.vyhod is not None) != (args.zayavka is not None and args.progon is not None):
                print("newsai proverit: --vyhod задаётся вместе с --zayavka и --progon", file=sys.stderr)
                return 2
            from .proverka import komanda
            return komanda(args)
        if args.komanda == "opublikovat":
            from .publikaciya import opublikovat
            return opublikovat(suhoj=args.suhoj)
        if args.komanda == "sboj":
            from .publikaciya import sboj_iz_fayla
            return sboj_iz_fayla(args.prichina_iz, sobytie=args.slot_iz_zayavki)
        if args.komanda == "nesohraneno":
            from .publikaciya import nesohraneno
            return nesohraneno()
        if args.komanda == "storozh":
            from .publikaciya import storozh
            return storozh(args.zayavki)
    except NotImplementedError as e:
        print(f"newsai {args.komanda}: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
