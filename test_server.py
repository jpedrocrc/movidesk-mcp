"""Confere a cobertura do servidor contra a API real do Movidesk.

Uso:
    python test_server.py             # chama cada endpoint mapeado com $top=1 (ou equivalente)
    python test_server.py --offline   # só valida mapeamento, ferramentas e travas (sem rede)

Precisa de MOVIDESK_TOKEN no ambiente para o modo online. Com o limite de 10 req/min
(07:01-18:59), o teste completo leva cerca de 2 minutos; à noite é imediato.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from typing import Any

try:
    from movidesk_mcp import server
except ImportError:  # rodando do repositório sem `pip install .`
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
    from movidesk_mcp import server

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, FALHA, PULADO = "OK", "FALHA", "PULADO"


def _resumo(r: dict[str, Any]) -> str:
    if "erro" in r:
        detalhe = r.get("detalhe")
        extra = f" | {str(detalhe)[:160]}" if detalhe else ""
        return f"{r['erro']} ({r.get('status_http', '-')}): {r['mensagem'][:160]}{extra}"
    dados = r.get("dados")
    if isinstance(dados, list):
        return f"{len(dados)} registro(s)"
    if isinstance(dados, dict):
        if "content_type" in dados:
            return f"arquivo {dados.get('content_type')} ({dados.get('tamanho_bytes')} bytes)"
        listas = {k: len(v) for k, v in dados.items() if isinstance(v, list)}
        texto = f"objeto ({len(dados)} campos)"
        if listas:
            texto += ": " + ", ".join(f"{k}={n}" for k, n in list(listas.items())[:6])
        return texto
    return type(dados).__name__


def _primeiro(r: dict[str, Any], chave: str = "id", lista_em: str | None = None) -> Any:
    dados = r.get("dados")
    if isinstance(dados, dict) and lista_em:
        dados = dados.get(lista_em)
    if isinstance(dados, list) and dados:
        return dados[0].get(chave) if isinstance(dados[0], dict) else None
    return None


def _hash_anexo(ticket: dict[str, Any]) -> str | None:
    for acao in ticket.get("actions") or []:
        for anexo in acao.get("attachments") or []:
            if anexo.get("path"):
                return anexo["path"]
    return None


async def online() -> int:
    if not os.environ.get("MOVIDESK_TOKEN"):
        print("Defina MOVIDESK_TOKEN para o teste online (ou use --offline).")
        return 2

    resultados: list[tuple[str, str, str]] = []
    testados: set[str] = set()

    async def chamar(rotulo: str, endpoint: str, params: dict[str, Any] | None = None, **kw) -> dict[str, Any]:
        print(f"-> {rotulo} ...", flush=True)
        r = await server.consultar(endpoint, params, **kw)
        resultados.append((rotulo, FALHA if "erro" in r else OK, _resumo(r)))
        return r

    def pular(rotulo: str, motivo: str) -> None:
        resultados.append((rotulo, PULADO, motivo))

    teste = {path: (e.get("teste") or {}).get("params", {}) for path, e in server.ENDPOINTS.items()}

    # tickets (lista + objeto completo) e HTML das ações
    r = await chamar("tickets (lista)", "tickets", teste["tickets"])
    testados.add("tickets")
    ticket_id = _primeiro(r)
    hash_anexo = None
    if ticket_id:
        rt = await chamar("tickets?id= (objeto completo)", "tickets", {"id": ticket_id})
        if isinstance(rt.get("dados"), dict):
            hash_anexo = _hash_anexo(rt["dados"])
        await chamar("tickets/htmldescription", "tickets/htmldescription", {"id": ticket_id})
    else:
        pular("tickets/htmldescription", "nenhum ticket recente encontrado")
    testados.add("tickets/htmldescription")

    r = await chamar("tickets/past (lista)", "tickets/past", teste["tickets/past"])
    testados.add("tickets/past")
    antigo_id = _primeiro(r)
    # tickets/past/htmldescription não existe (404 de rota); confere se a rota única atende tickets antigos.
    if antigo_id:
        await chamar("tickets/htmldescription (ticket antigo)", "tickets/htmldescription", {"id": antigo_id})
    else:
        pular("tickets/htmldescription (ticket antigo)", "nenhum ticket em tickets/past")

    inicio = (date.today() - timedelta(days=30)).isoformat()
    await chamar("tickets/merged (período)", "tickets/merged", {"startDate": inicio})
    testados.add("tickets/merged")

    for ep in ("persons", "services", "activity", "survey/responses"):
        await chamar(ep, ep, teste[ep])
        testados.add(ep)

    r = await chamar("timeAgreement (lista)", "timeAgreement", teste["timeAgreement"])
    testados.add("timeAgreement")
    contrato = _primeiro(r, "name")
    if contrato:
        mes = date.today().replace(day=1).isoformat()
        await chamar("timeAgreementConsumption", "timeAgreementConsumption",
                     {"name": contrato, "startPeriod": f"{mes}T00:00:00"})
    else:
        pular("timeAgreementConsumption", "nenhum contrato de horas cadastrado")
    testados.add("timeAgreementConsumption")

    r = await chamar("survey/questions", "survey/questions", {})
    testados.add("survey/questions")
    pergunta = _primeiro(r)
    if pergunta:
        await chamar("survey/questions/{id}", f"survey/questions/{pergunta}")
    else:
        pular("survey/questions/{id}", "nenhuma pergunta cadastrada")
    testados.add("survey/questions/{id}")

    r = await chamar("kb/article (busca)", "kb/article", teste["kb/article"])
    testados.add("kb/article")
    artigo = _primeiro(r, lista_em="items")
    if artigo:
        ra = await chamar("article/{id}", f"article/{artigo}")
        dados = ra.get("dados") if isinstance(ra.get("dados"), dict) else {}
        if not hash_anexo:
            hash_anexo = next((a.get("hash") for a in dados.get("attachments") or [] if a.get("hash")), None)
    else:
        pular("article/{id}", "a busca não retornou artigos (base de conhecimento vazia?)")
    testados.add("article/{id}")

    if hash_anexo:
        await chamar("storage/download", "storage/download", {"id": hash_anexo})
    else:
        pular("storage/download", "nenhum anexo no ticket/artigo de teste")
    testados.add("storage/download")

    faltando = sorted(set(server.ENDPOINTS) - testados)
    for ep in faltando:
        pular(ep, "sem caso de teste em test_server.py")

    largura = max(len(r[0]) for r in resultados)
    print("\n" + "=" * 100)
    for rotulo, status, obs in resultados:
        print(f"{rotulo:<{largura}}  {status:<7} {obs}")
    print("=" * 100)
    falhas = sum(1 for r in resultados if r[1] == FALHA)
    print(f"{len(server.ENDPOINTS)} endpoints mapeados | "
          f"{sum(1 for r in resultados if r[1] == OK)} OK | {falhas} falha(s) | "
          f"{sum(1 for r in resultados if r[1] == PULADO)} pulado(s)")
    return 1 if falhas else 0


async def offline() -> int:
    falhas = 0

    def conferir(descricao: str, condicao: bool) -> None:
        nonlocal falhas
        print(f"[{'OK' if condicao else 'FALHA'}] {descricao}")
        falhas += 0 if condicao else 1

    conferir(f"ENDPOINTS.md carregado ({len(server.ENDPOINTS)} endpoints)", len(server.ENDPOINTS) >= 15)
    ferramentas = {t.name for t in await server.mcp.list_tools()}
    esperadas = {"movidesk_get", "listar_endpoints", "listar_tickets", "buscar_ticket",
                 "listar_pessoas", "resumo_tickets"}
    conferir(f"ferramentas registradas: {sorted(ferramentas)}", esperadas <= ferramentas)

    conferir("normaliza URL completa com query",
             server.normalizar_endpoint("https://api.movidesk.com/public/v1/tickets?$top=1&token=x")
             == ("tickets", {"$top": "1", "token": "x"}))
    conferir("resolve template article/{id}", server.resolver_endpoint("article/123")["path"] == "article/{id}")
    conferir("resolve survey/questions/{id}",
             server.resolver_endpoint("survey/questions/abc")["path"] == "survey/questions/{id}")

    for ep in ("asterisk_completedCall", "asterisk_startTransferedCall", "addTeamsToActivity",
               "ticketCustomFieldValue/InsertValues", "ticketFileUpload"):
        r = await server.consultar(ep, permitir_nao_listados=True)
        conferir(f"bloqueia escrita: {ep}", r.get("erro") == "bloqueado")
    r = await server.consultar("tickets", {"$top": 1})
    conferir("exige $select em listas de tickets (sem chamar a API)",
             r.get("erro") in ("select_obrigatorio", "configuracao"))
    r = await server.consultar("naoexiste")
    conferir("recusa endpoint não mapeado", r.get("erro") == "endpoint_nao_mapeado")
    conferir("remove token dos params", "token" not in server._limpar_params({"token": "x", "a": 1}))

    falso = "valor-falso-de-teste"
    os.environ["MOVIDESK_TOKEN"] = falso
    texto = server._ocultar(f"GET https://api/x?token={falso}&$top=1 | erro com {falso} no corpo")
    conferir("oculta token em mensagens", falso not in texto)
    print(f"\n{'Tudo certo.' if not falhas else f'{falhas} falha(s).'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    modo = offline if "--offline" in sys.argv else online
    sys.exit(asyncio.run(modo()))
