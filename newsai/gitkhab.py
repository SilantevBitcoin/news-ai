"""Issue Claude Code от 100 реакций с ответом команды — строка списка с пометкой `скоро` (Р31).

Ответ команды — комментарий с author_association из avtor_komandy или от логина из loginy_komandy
(Р41). Каждый issue выходит один раз — когда впервые набрал оба условия: issue из vidennye не
проверяется. Пока в состоянии нет действующей отметки github_obhod (sostoyanie.obhod_deystvuet:
отметка и хоть один issue в vidennye), поиск идёт без периода: все набравшие оба условия — в
tolko_v_vidennye, в выпуск — только те, где первый комментарий команды за последние 24 часа; сбой
посреди такого обхода не отдаёт ничего. С действующей отметкой поиск — с более раннего из начала
периода и момента сбора минус 7 дней, без отбора по дате обновления: в vidennye issue попадает только
вышедшим, пропущенный выпуском поиск ловит снова.

Поиск с токеном пуст — один повтор без Authorization (у токена Actions поиск по чужому репозиторию
может быть пустым); пуст и так — отказ «поиск issue: 0 результатов»: таких issue больше 170.

Файл issue (спека «Сборщик issue»): команда `sbor-issue` пишет найденное в dannye/issue.json; зовут её
публикатор после своей заявки и workflow сборщика issue. Сбор агента берёт issue из этого файла, когда
запрос к API не прошёл (из облака — 403 прокси), и только если файл годен (собран за 24 часа до
начала прогона). Записи из API и из файла превращаются в кандидатов одной функцией — текст кандидата
один и тот же.

Вид файла issue:
    {
      "sobrano": "<UTC>",                 # начало сбора
      "nachalo_poiska": "<UTC>" | null,   # null — поиск без периода
      "issue": [{"url", "zagolovok", "obnovleno": "<UTC>", "reakcii", "kommentariev",
                 "sostoyanie": "open|closed", "tekst" (до 3000),
                 "otvet_komandy": {"login", "data": "<UTC>", "tekst" (до 3000)}}]
    }
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from . import nastrojki, sekrety
from .progon import Kandidat, Period
from .razbor import data_iso
from .sostoyanie import obhod_deystvuet
from .vremya import iz_stroki, seychas, v_stroku
from .zaprosy import OshibkaZaprosa, Otvet

API = "https://api.github.com"
STRANIC_POISKA = 10
STRANIC_KOMMENTARIEV = 30
PEREMENNAYA_TOKENA = "GITHUB_TOKEN"
TEKST_ZNAKOV = 3000

POLYA_ISSUE = ("url", "zagolovok", "obnovleno", "reakcii", "kommentariev", "sostoyanie", "tekst",
               "otvet_komandy")
POLYA_OTVETA = ("login", "data", "tekst")


class OshibkaGitHub(Exception):
    pass


class NegodnyyFayl(Exception):
    """Файл issue не годится сбору агента; текст — чем именно."""


def nachalo_poiska(sost: dict, nachalo_perioda: datetime, moment: datetime) -> datetime | None:
    """Начало периода поиска issue: без действующей отметки github_obhod — None (поиск без периода),
    иначе — более раннее из начала периода и момента сбора минус ISSUE_POISK_DNEY дней."""
    if not obhod_deystvuet(sost):
        return None
    return min(nachalo_perioda, moment - timedelta(days=nastrojki.ISSUE_POISK_DNEY))


def token_github() -> str | None:
    """Сначала окружение, потом файл секретов проекта."""
    return sekrety.prochitat(PEREMENNAYA_TOKENA)


def _zagolovki(token: str | None) -> dict:
    z = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "newsai"}
    if token:
        z["Authorization"] = f"Bearer {token}"
    return z


def _soobshchenie(otvet: Otvet) -> str:
    try:
        tekst = str((json.loads(otvet.tekst()) or {}).get("message") or "")
    except (ValueError, AttributeError):
        tekst = ""
    return " ".join(tekst.split())[:200]


def _json(otvet: Otvet, chto: str):
    if otvet.status in (403, 429) and (otvet.zagolovki.get("x-ratelimit-remaining") == "0"
                                       or b"rate limit" in otvet.telo.lower()):
        raise OshibkaGitHub(f"{chto}: исчерпан лимит запросов GitHub API")
    if otvet.status == 403:
        prichina = _soobshchenie(otvet)
        raise OshibkaGitHub(f"{chto}: ответ 403, доступ закрыт" + (f": {prichina}" if prichina else ""))
    if otvet.status != 200:
        raise OshibkaGitHub(f"{chto}: ответ {otvet.status}")
    try:
        return json.loads(otvet.tekst())
    except ValueError as e:
        raise OshibkaGitHub(f"{chto}: не JSON ({e})") from e


def _poisk(istochnik: dict, nachalo: datetime | None, poluchit, zagolovki: dict) -> list[dict]:
    f = istochnik.get("filtr") or {}
    q = f"repo:{f.get('repozitoriy', 'anthropics/claude-code')} is:issue reactions:>={f.get('reakcii_ot', 100)}"
    if nachalo is not None:
        q += " updated:>=" + v_stroku(nachalo)
    adres = istochnik.get("adres") or f"{API}/search/issues"
    issue = []
    for stranica in range(1, STRANIC_POISKA + 1):
        url = f"{adres}?q={quote(q)}&sort=updated&order=desc&per_page=100&page={stranica}"
        dannye = _json(poluchit(url, zagolovki), "поиск issue")
        chast = dannye.get("items") or []
        issue.extend(i for i in chast if "pull_request" not in i)
        if len(chast) < 100:
            break
    return issue


def _poisk_s_povtorom(istochnik: dict, nachalo: datetime | None, poluchit, zagolovki: dict,
                      itog: dict) -> list[dict]:
    """Поиск; пуст с токеном — один повтор без Authorization; пуст и так — OshibkaGitHub.

    itog: iz_poiska — сколько issue дал поиск, bez_tokena — был ли повтор без токена."""
    issue = _poisk(istochnik, nachalo, poluchit, zagolovki)
    itog["bez_tokena"] = False
    if not issue and "Authorization" in zagolovki:
        itog["bez_tokena"] = True
        issue = _poisk(istochnik, nachalo, poluchit,
                       {k: v for k, v in zagolovki.items() if k != "Authorization"})
    if not issue:
        raise OshibkaGitHub("поиск issue: 0 результатов"
                            + (" (с токеном и повтором без токена)" if itog["bez_tokena"] else ""))
    itog["iz_poiska"] = len(issue)
    return issue


def _iz_komandy(k: dict, associacii: set, loginy: set) -> bool:
    login = ((k.get("user") or {}).get("login") or "").lower()
    return k.get("author_association") in associacii or login in loginy


def _pervyy_otvet_komandy(i: dict, associacii: set, loginy: set, poluchit, zagolovki: dict) -> dict | None:
    """Первый по времени комментарий команды или None."""
    if not i.get("comments"):
        return None
    adres = i.get("comments_url") or f"{i['url']}/comments"
    for stranica in range(1, STRANIC_KOMMENTARIEV + 1):
        chast = _json(poluchit(f"{adres}?per_page=100&page={stranica}", zagolovki),
                      f"комментарии #{i.get('number')}")
        for k in chast:
            if _iz_komandy(k, associacii, loginy):
                return k
        if len(chast) < 100:
            return None
    return None


def _utc(s) -> str:
    """Строка времени GitHub → UTC-строка файла; неразбираемая остаётся как есть."""
    kogda = data_iso(s) if isinstance(s, str) else None
    return v_stroku(kogda) if kogda else str(s or "")


def _zapis(i: dict, otvet: dict) -> dict:
    """Issue и первый ответ команды → запись по схеме файла issue."""
    return {
        "url": i.get("html_url") or "",
        "zagolovok": i.get("title") or "",
        "obnovleno": _utc(i.get("updated_at")),
        "reakcii": (i.get("reactions") or {}).get("total_count", 0),
        "kommentariev": i.get("comments", 0),
        "sostoyanie": i.get("state") or "",
        "tekst": (i.get("body") or "")[:TEKST_ZNAKOV],
        "otvet_komandy": {
            "login": (otvet.get("user") or {}).get("login") or "",
            "data": _utc(otvet.get("created_at")),
            "tekst": (otvet.get("body") or "")[:TEKST_ZNAKOV],
        },
    }


def nayti_zapisi(istochnik: dict, sost: dict, nachalo: datetime | None, poluchit,
                 zagolovki: dict, zapisi: list | None = None, itog: dict | None = None) -> list[dict]:
    """Поиск issue → записи файла issue: только с ответом команды и не из vidennye (их комментарии
    не запрашиваются). Отказ запроса или пустой поиск — OshibkaGitHub или OshibkaZaprosa; найденное
    до отказа остаётся в переданном списке zapisi. itog — для вывода: iz_poiska, bez_tokena,
    videnyh (пропущено как увиденные)."""
    from .sostoyanie import videno

    f = istochnik.get("filtr") or {}
    associacii = set(f.get("avtor_komandy") or ["MEMBER", "COLLABORATOR"])
    loginy = {l.lower() for l in f.get("loginy_komandy") or []}
    zapisi = [] if zapisi is None else zapisi
    itog = {} if itog is None else itog
    itog["videnyh"] = 0
    for i in _poisk_s_povtorom(istochnik, nachalo, poluchit, zagolovki, itog):
        url = i.get("html_url") or ""
        if url and videno(sost, url):
            itog["videnyh"] += 1
        if not url or videno(sost, url):
            continue
        otvet = _pervyy_otvet_komandy(i, associacii, loginy, poluchit, zagolovki)
        if otvet is not None:
            zapisi.append(_zapis(i, otvet))
    return zapisi


def _kandidat(istochnik: dict, z: dict, period: Period) -> Kandidat:
    o = z["otvet_komandy"]
    tekst = (f"Реакций: {z['reakcii']}. Комментариев: {z['kommentariev']}. Состояние: {z['sostoyanie']}.\n"
             f"Ответ команды ({o['login'] or '?'}, {o['data']}): {o['tekst']}\n\n"
             f"Issue: {z['tekst']}")
    return Kandidat(identifikator=z["url"], istochnik=istochnik["imya"], zagolovok=z["zagolovok"] or z["url"],
                    url=z["url"], data=data_iso(z["obnovleno"]) or period.konec, tekst=tekst,
                    skoro=True, novyy=True)


def kandidaty_iz_zapisey(istochnik: dict, zapisi: list[dict], sost: dict,
                         period: Period) -> tuple[list[Kandidat], list[str]]:
    """Записи (из API или из файла issue) → (кандидаты, id только в увиденные) по общим правилам.

    vidennye не берутся; без действующей отметки github_obhod первый ответ команды раньше 24 часов
    до начала прогона — только в увиденные; иначе — все остальные, без отбора по дате обновления.
    """
    from .sostoyanie import videno

    polnyy = not obhod_deystvuet(sost)
    granica_polnogo = period.konec - timedelta(hours=nastrojki.PERVYY_PERIOD_CHASOV)
    kandidaty, tolko = [], []
    for z in zapisi:
        if not z["url"] or videno(sost, z["url"]):
            continue
        if polnyy:
            kogda_otvet = data_iso(z["otvet_komandy"]["data"])
            if kogda_otvet is None or kogda_otvet < granica_polnogo:
                tolko.append(z["url"])
                continue
        kandidaty.append(_kandidat(istochnik, z, period))
    return kandidaty, tolko


def _vremya_ili_none(znachenie) -> datetime | None:
    if not isinstance(znachenie, str):
        return None
    try:
        return iz_stroki(znachenie)
    except ValueError:
        return None


def _shema(dannye) -> str | None:
    """Чем файл не совпадает со схемой, или None."""
    from .sostoyanie import eto_issue_github

    if not isinstance(dannye, dict) or set(dannye) != {"sobrano", "nachalo_poiska", "issue"}:
        return "ждём объект с полями sobrano, nachalo_poiska, issue"
    if _vremya_ili_none(dannye["sobrano"]) is None:
        return "sobrano — не время UTC"
    if dannye["nachalo_poiska"] is not None and _vremya_ili_none(dannye["nachalo_poiska"]) is None:
        return "nachalo_poiska — не время UTC и не null"
    if not isinstance(dannye["issue"], list):
        return "issue — не список"
    for n, z in enumerate(dannye["issue"], 1):
        if not isinstance(z, dict) or set(z) != set(POLYA_ISSUE):
            return f"issue {n}: ждём поля {', '.join(POLYA_ISSUE)}"
        o = z["otvet_komandy"]
        if not isinstance(o, dict) or set(o) != set(POLYA_OTVETA):
            return f"issue {n}: otvet_komandy — ждём поля {', '.join(POLYA_OTVETA)}"
        if not isinstance(z["url"], str) or not eto_issue_github(z["url"]):
            return f"issue {n}: url — не адрес issue GitHub"
        for pole in ("reakcii", "kommentariev"):
            if not isinstance(z[pole], int) or isinstance(z[pole], bool) or z[pole] < 0:
                return f"issue {n}: {pole} — не число"
        if z["sostoyanie"] not in ("open", "closed"):
            return f"issue {n}: sostoyanie — не open и не closed"
        for gde, pole in ((z, "obnovleno"), (o, "data")):
            if _vremya_ili_none(gde[pole]) is None:
                return f"issue {n}: {pole} — не время UTC"
        for gde, pole in ((z, "zagolovok"), (z, "tekst"), (o, "login"), (o, "tekst")):
            if not isinstance(gde[pole], str):
                return f"issue {n}: {pole} — не строка"
        if len(z["tekst"]) > TEKST_ZNAKOV or len(o["tekst"]) > TEKST_ZNAKOV:
            return f"issue {n}: tekst длиннее {TEKST_ZNAKOV} знаков"
    return None


def prochitat_fayl_issue(put: Path, sost: dict, period: Period) -> dict:
    """Годный файл issue для прогона с началом period.konec, иначе NegodnyyFayl с причиной."""
    put = Path(put)
    if not put.exists():
        raise NegodnyyFayl("файла нет")
    try:
        dannye = json.loads(put.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NegodnyyFayl(f"не читается: {type(e).__name__}") from None
    oshibka = _shema(dannye)
    if oshibka:
        raise NegodnyyFayl(f"не по схеме: {oshibka}")
    sobrano = iz_stroki(dannye["sobrano"])
    rannyaya = period.konec - timedelta(hours=nastrojki.ISSUE_GODEN_CHASOV)
    if sobrano < rannyaya:
        raise NegodnyyFayl(f"собран {v_stroku(sobrano)} — раньше {v_stroku(rannyaya)}, "
                           f"за {nastrojki.ISSUE_GODEN_CHASOV} часа до начала прогона {v_stroku(period.konec)}")
    if sobrano > period.konec:
        raise NegodnyyFayl(f"собран {v_stroku(sobrano)} — позже начала прогона {v_stroku(period.konec)}")
    nachalo = dannye["nachalo_poiska"]
    if not obhod_deystvuet(sost):
        if nachalo is not None:
            raise NegodnyyFayl(f"поиск с {v_stroku(iz_stroki(nachalo))}, а полного обхода ещё не было — "
                               f"нужен поиск без периода")
    elif nachalo is not None and iz_stroki(nachalo) > period.nachalo:
        raise NegodnyyFayl(f"поиск с {v_stroku(iz_stroki(nachalo))} — позже начала периода "
                           f"{v_stroku(period.nachalo)}")
    return dannye


def zapisat_fayl_issue(put: Path, sobrano: datetime, nachalo_poiska: datetime | None,
                       zapisi: list[dict]) -> None:
    """Файл issue целиком: через временный файл и замену — обрыв не оставит половину [техника]."""
    put = Path(put)
    put.parent.mkdir(parents=True, exist_ok=True)
    dannye = {"sobrano": v_stroku(sobrano),
              "nachalo_poiska": v_stroku(nachalo_poiska) if nachalo_poiska else None,
              "issue": zapisi}
    vremennyy = put.with_name(put.name + ".tmp")
    vremennyy.write_text(json.dumps(dannye, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                         newline="\n")
    os.replace(vremennyy, put)


def sobrat(istochnik: dict, sost: dict, period: Period, poluchit,
           fayl_issue: Path | None = None) -> tuple[list[Kandidat], list[str], str | None, bool, str]:
    """→ (кандидаты со skoro и novyy, id только в увиденные, ошибка или None, удался ли полный обход,
    строка отчёта).

    period — период выпуска; его конец — начало прогона.

    Обычный прогон: найденное до ошибки не теряется. Полный обход: при ошибке — ничего; пустой
    поиск — тоже ошибка, полным обходом не засчитывается.
    fayl_issue — путь к файлу сборщика (только у сбора агента): запрос к API не прошёл и файл
    годен — кандидаты из файла, найденное запросами отброшено. Без пути файл не читается.
    """
    polnyy = not obhod_deystvuet(sost)
    token = token_github()
    zagolovki = _zagolovki(token)
    zapisi = []
    try:
        nayti_zapisi(istochnik, sost, nachalo_poiska(sost, period.nachalo, period.konec), poluchit,
                     zagolovki, zapisi)
    except (OshibkaGitHub, OshibkaZaprosa) as e:
        oshibka = str(e)
        if not token and "исчерпан лимит" in oshibka:
            oshibka += f" (нет {PEREMENNAYA_TOKENA}: без него 60 запросов в час)"
        if fayl_issue is not None:
            try:
                fayl = prochitat_fayl_issue(fayl_issue, sost, period)
            except NegodnyyFayl as nf:
                oshibka += f"; файл сборщика issue не годен: {nf}"
            else:
                kandidaty, tolko = kandidaty_iz_zapisey(istochnik, fayl["issue"], sost, period)
                return kandidaty, tolko, None, False, f"из файла сборщика, собран {fayl['sobrano']}"
        if polnyy:
            return [], [], oshibka, False, ""
        return kandidaty_iz_zapisey(istochnik, zapisi, sost, period)[0], [], oshibka, False, ""
    kandidaty, tolko = kandidaty_iz_zapisey(istochnik, zapisi, sost, period)
    return kandidaty, tolko, None, polnyy, ""


def sbor_issue(poluchit=None, sobrano: datetime | None = None, put_istochnikov: Path | None = None,
               put_sostoyaniya: Path | None = None, put_issue: Path | None = None) -> int:
    """Команда `sbor-issue`: поиск issue по строке «GitHub Claude Code» → файл issue.

    Без действующей отметки github_obhod — поиск без периода, иначе — с более раннего из начала
    периода по состоянию и момента сбора минус 7 дней. Коды: 0 — файл записан; 1 — состояние или
    источники не читаются; 2 — запрос к API не прошёл или поиск пуст и после повтора без токена,
    прежний файл не тронут. Состояние не пишется. Вывод — для лога Actions: сколько дал поиск, был ли
    повтор без токена, сколько с ответом команды, nachalo_poiska.
    """
    from . import sostoyanie
    from .zaprosy import poluchit as poluchit_po_seti

    poluchit = poluchit or poluchit_po_seti
    sobrano = sobrano or seychas()
    put_issue = Path(put_issue) if put_issue else nastrojki.fayl_issue()
    try:
        sost = sostoyanie.zagruzit(put_sostoyaniya)
        put = Path(put_istochnikov) if put_istochnikov else nastrojki.FAYL_ISTOCHNIKOV
        istochnik = next(i for i in json.loads(put.read_text(encoding="utf-8"))["istochniki"]
                         if i.get("vid") == "github_issues")
    except (sostoyanie.OshibkaSostoyaniya, OSError, ValueError, KeyError, StopIteration) as e:
        print(f"newsai sbor-issue: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    nachalo = nachalo_poiska(sost, sostoyanie.period(sost, sobrano)[0], sobrano)
    token = token_github()
    itog = {}
    try:
        zapisi = nayti_zapisi(istochnik, sost, nachalo, poluchit, _zagolovki(token), itog=itog)
    except (OshibkaGitHub, OshibkaZaprosa) as e:
        print(f"newsai sbor-issue: {e}; файл issue не переписан", file=sys.stderr)
        return 2
    zapisat_fayl_issue(put_issue, sobrano, nachalo, zapisi)
    print(f"issue: {len(zapisi)}")
    print(f"nachalo_poiska: {v_stroku(nachalo) if nachalo else 'null'}")
    print(f"поиск: {itog['iz_poiska']}, из них уже в vidennye: {itog['videnyh']}, "
          f"с ответом команды: {len(zapisi)}")
    print("токен: " + ("нет" if not token else
                       "поиск с ним пуст — повтор без токена" if itog["bez_tokena"] else "есть"))
    print(f"файл issue: {put_issue}")
    return 0
