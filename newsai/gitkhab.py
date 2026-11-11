"""Issue Claude Code от 100 реакций с ответом команды — строка списка с пометкой `скоро` (Р31).

Ответ команды — комментарий с author_association из avtor_komandy или от логина из loginy_komandy
(Р41). Каждый issue выходит один раз — когда впервые набрал оба условия: issue из vidennye не
проверяется. Пока в состоянии нет отметки github_obhod, поиск идёт без периода: все набравшие оба
условия — в tolko_v_vidennye, в выпуск — только те, где первый комментарий команды за последние
24 часа; сбой посреди такого обхода не отдаёт ничего.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import quote

from . import nastrojki, sekrety
from .progon import Kandidat, Period
from .razbor import data_iso
from .vremya import v_stroku
from .zaprosy import OshibkaZaprosa, Otvet

API = "https://api.github.com"
STRANIC_POISKA = 10
STRANIC_KOMMENTARIEV = 30
PEREMENNAYA_TOKENA = "GITHUB_TOKEN"


class OshibkaGitHub(Exception):
    pass


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


def sobrat(istochnik: dict, sost: dict, period: Period,
           poluchit) -> tuple[list[Kandidat], list[str], str | None, bool]:
    """→ (кандидаты со skoro и novyy, id только в увиденные, ошибка или None, удался ли полный обход).

    period — период выпуска; его конец — начало прогона.

    Обычный прогон: найденное до ошибки не теряется. Полный обход: при ошибке — ничего.
    """
    from .sostoyanie import videno

    f = istochnik.get("filtr") or {}
    associacii = set(f.get("avtor_komandy") or ["MEMBER", "COLLABORATOR"])
    loginy = {l.lower() for l in f.get("loginy_komandy") or []}
    polnyy = not sost.get("github_obhod")
    granica_polnogo = period.konec - timedelta(hours=nastrojki.PERVYY_PERIOD_CHASOV)
    token = token_github()
    zagolovki = _zagolovki(token)
    kandidaty, tolko_v_vidennye = [], []
    try:
        for i in _poisk(istochnik, None if polnyy else period.nachalo, poluchit, zagolovki):
            url = i.get("html_url") or ""
            if not url or videno(sost, url):
                continue
            otvet = _pervyy_otvet_komandy(i, associacii, loginy, poluchit, zagolovki)
            if otvet is None:
                continue
            kogda_otvet = data_iso(otvet.get("created_at", ""))
            if polnyy and (kogda_otvet is None or kogda_otvet < granica_polnogo):
                tolko_v_vidennye.append(url)
                continue
            reakcii = (i.get("reactions") or {}).get("total_count", 0)
            tekst = (f"Реакций: {reakcii}. Комментариев: {i.get('comments', 0)}. Состояние: {i.get('state')}.\n"
                     f"Ответ команды ({(otvet.get('user') or {}).get('login', '?')}, "
                     f"{otvet.get('created_at', '')}): {otvet.get('body') or ''}\n\n"
                     f"Issue: {i.get('body') or ''}")
            kandidaty.append(Kandidat(identifikator=url, istochnik=istochnik["imya"],
                                      zagolovok=i.get("title") or url, url=url,
                                      data=data_iso(i.get("updated_at", "")) or period.konec,
                                      tekst=tekst, skoro=True, novyy=True))
    except (OshibkaGitHub, OshibkaZaprosa) as e:
        oshibka = str(e)
        if not token and "исчерпан лимит" in oshibka:
            oshibka += f" (нет {PEREMENNAYA_TOKENA}: без него 60 запросов в час)"
        if polnyy:
            return [], [], oshibka, False
        return kandidaty, tolko_v_vidennye, oshibka, False
    return kandidaty, tolko_v_vidennye, None, polnyy
