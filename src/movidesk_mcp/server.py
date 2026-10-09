"""MCP server (stdio) somente leitura para a API pública do Movidesk.

O mapeamento de endpoints vem do bloco JSON em ENDPOINTS.md (fonte de verdade).
O token é lido de MOVIDESK_TOKEN e nunca é logado nem devolvido nas respostas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import Counter, deque
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import mean, median
from typing import Any
from urllib.parse import parse_qsl

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

AQUI = Path(__file__).resolve().parent
ENDPOINTS_MD = AQUI / "ENDPOINTS.md"


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


BASE_URL = os.environ.get("MOVIDESK_BASE_URL", "https://api.movidesk.com/public/v1").rstrip("/")
TIMEOUT_S = _env_int("MOVIDESK_TIMEOUT", 60)
PAGE_SIZE = _env_int("MOVIDESK_PAGE_SIZE", 100)
RATE_LIMIT = _env_int("MOVIDESK_RATE_LIMIT", 10)  # req/min; 0 desliga o limitador local
# "horario" = só limita das 07:01 às 18:59 (Brasília), como a doc; "sempre" = limita 24h
RATE_LIMIT_MODO = os.environ.get("MOVIDESK_RATE_LIMIT_MODO", "horario").lower()
MAX_TENTATIVAS = _env_int("MOVIDESK_MAX_RETRIES", 3)
MAX_ESPERA_S = _env_int("MOVIDESK_MAX_ESPERA", 320)  # maior espera aceita num único retry
MAX_CHARS = _env_int("MOVIDESK_MAX_CHARS", 60000)  # acima disso a resposta é truncada
BRASILIA = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019

# stdout é o canal do protocolo MCP: log só em stderr. httpx loga a URL completa
# (com o token) em INFO, por isso fica em WARNING.
logging.basicConfig(level=os.environ.get("MOVIDESK_LOG_LEVEL", "WARNING"), stream=sys.stderr)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("movidesk-mcp")


def _token() -> str:
    token = os.environ.get("MOVIDESK_TOKEN", "").strip()
    if not token:
        raise MovideskErro(
            "configuracao",
            "Variável de ambiente MOVIDESK_TOKEN não definida. Gere a chave em "
            "Configurações > Conta > Parâmetros > Ambiente no Movidesk.",
        )
    return token


def _ocultar(texto: str) -> str:
    """Remove o token de qualquer texto que possa sair do servidor."""
    token = os.environ.get("MOVIDESK_TOKEN", "").strip()
    if len(token) >= 8:  # tokens reais são GUIDs; evita mascarar pedaços de palavras
        texto = texto.replace(token, "***")
    return re.sub(r"(?i)(token=)[^&\s\"']+", r"\1***", texto)


# ---------------------------------------------------------------------------
# Mapeamento (ENDPOINTS.md)
# ---------------------------------------------------------------------------


def carregar_mapeamento(caminho: Path = ENDPOINTS_MD) -> dict[str, Any]:
    texto = caminho.read_text(encoding="utf-8")
    m = re.search(
        r"<!-- ENDPOINTS-JSON:INICIO -->\s*```json\s*(.*?)```\s*<!-- ENDPOINTS-JSON:FIM -->",
        texto,
        re.S,
    )
    if not m:
        raise RuntimeError(f"Bloco ENDPOINTS-JSON não encontrado em {caminho}")
    return json.loads(m.group(1))


MAPA = carregar_mapeamento()
ENDPOINTS: dict[str, dict[str, Any]] = {e["path"]: e for e in MAPA["endpoints"]}
BLOQUEADOS = {b.lower() for b in MAPA.get("bloqueados", [])}
# Prefixos/verbos que indicam escrita mesmo em GET (ex.: telefonia asterisk_*).
_ESCRITA_RE = re.compile(
    r"(^|/)((?i:asterisk_|setmadecall|ticketfileupload|insert|update|delete|remove|create|upload)"
    r"|add[A-Z])"
)


def _template_regex(path: str) -> re.Pattern[str]:
    partes = [r"[^/]+" if re.fullmatch(r"\{\w+\}", p) else re.escape(p) for p in path.split("/")]
    return re.compile("^" + "/".join(partes) + "$", re.I)


_TEMPLATES = [(e["path"], _template_regex(e["path"])) for e in MAPA["endpoints"]]


def normalizar_endpoint(endpoint: str) -> tuple[str, dict[str, str]]:
    """Aceita 'tickets', '/tickets', '/public/v1/tickets', URL completa e query string."""
    ep = endpoint.strip()
    extras: dict[str, str] = {}
    if "?" in ep:
        ep, qs = ep.split("?", 1)
        extras = dict(parse_qsl(qs, keep_blank_values=True))
    ep = re.sub(r"^https?://[^/]+", "", ep)
    ep = re.sub(r"^/?public/v1/", "", ep.lstrip("/"))
    return ep.strip("/"), extras


def resolver_endpoint(ep: str) -> dict[str, Any] | None:
    for path, rx in _TEMPLATES:
        if rx.match(ep):
            return ENDPOINTS[path]
    return None


def checar_bloqueio(ep: str) -> None:
    if ep.lower() in BLOQUEADOS or _ESCRITA_RE.search(ep):
        raise MovideskErro(
            "bloqueado",
            f"O endpoint '{ep}' altera dados no Movidesk (mesmo sendo GET, como a telefonia) "
            "e não é permitido neste servidor somente leitura.",
        )


# ---------------------------------------------------------------------------
# Cliente HTTP com rate limit, retry e tratamento de erros
# ---------------------------------------------------------------------------


class MovideskErro(Exception):
    def __init__(self, tipo: str, mensagem: str, status: int | None = None, detalhe: Any = None):
        super().__init__(mensagem)
        self.tipo = tipo
        self.mensagem = mensagem
        self.status = status
        self.detalhe = detalhe

    def como_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"erro": self.tipo, "mensagem": _ocultar(self.mensagem)}
        if self.status is not None:
            d["status_http"] = self.status
        if self.detalhe is not None:
            d["detalhe"] = json.loads(_ocultar(json.dumps(self.detalhe, ensure_ascii=False, default=str)))
        return d


class LimitadorTaxa:
    """Janela deslizante de 60 s. Só atua no horário limitado (ou sempre, se configurado)."""

    def __init__(self, por_minuto: int):
        self.por_minuto = por_minuto
        self.chamadas: deque[float] = deque()
        self.lock = asyncio.Lock()

    @staticmethod
    def horario_limitado(agora: datetime | None = None) -> bool:
        agora = agora or datetime.now(BRASILIA)
        minutos = agora.hour * 60 + agora.minute
        return 7 * 60 + 1 <= minutos <= 18 * 60 + 59

    def ativo(self) -> bool:
        if self.por_minuto <= 0:
            return False
        return RATE_LIMIT_MODO == "sempre" or self.horario_limitado()

    async def aguardar(self) -> None:
        if not self.ativo():
            return
        async with self.lock:
            while True:
                agora = time.monotonic()
                while self.chamadas and agora - self.chamadas[0] >= 60:
                    self.chamadas.popleft()
                if len(self.chamadas) < self.por_minuto:
                    self.chamadas.append(agora)
                    return
                espera = 60 - (agora - self.chamadas[0]) + 0.05
                log.info("Rate limit local: aguardando %.1fs", espera)
                await asyncio.sleep(espera)


_limitador = LimitadorTaxa(RATE_LIMIT)
_cliente: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _cliente
    if _cliente is None:
        _cliente = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(TIMEOUT_S, connect=15),
            headers={"Accept": "application/json", "User-Agent": "movidesk-mcp/1.0"},
            follow_redirects=True,
        )
    return _cliente


def _retry_after(resp: httpx.Response, tentativa: int) -> float:
    valor = resp.headers.get("retry-after")
    if valor:
        try:
            return max(float(valor), 1.0)
        except ValueError:
            try:
                alvo = parsedate_to_datetime(valor)
                return max((alvo - datetime.now(alvo.tzinfo)).total_seconds(), 1.0)
            except (TypeError, ValueError):
                pass
    # Sem header: segue a escada de bloqueio documentada (60s, 120s, 300s).
    return [60, 120, 300][min(tentativa, 2)]


def _corpo(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000] or None


def _limpar_params(params: dict[str, Any] | None) -> dict[str, str]:
    limpos: dict[str, str] = {}
    for k, v in (params or {}).items():
        if k.lower() == "token" or v is None:
            continue  # o token vem só do ambiente
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        limpos[str(k)] = str(v)
    return limpos


async def requisitar(ep: str, params: dict[str, str]) -> httpx.Response:
    """GET com rate limit local, retry em 429/5xx/timeout e erros traduzidos."""
    url_params = {**params, "token": _token()}
    ultima_falha = ""
    for tentativa in range(MAX_TENTATIVAS + 1):
        await _limitador.aguardar()
        try:
            resp = await _http().get(f"/{ep}", params=url_params)
        except httpx.TimeoutException:
            ultima_falha = f"Timeout após {TIMEOUT_S}s"
        except httpx.HTTPError as e:
            ultima_falha = f"Falha de conexão: {type(e).__name__}"
        else:
            if resp.status_code < 400:
                return resp
            corpo = _corpo(resp)
            if resp.status_code == 401:
                raise MovideskErro(
                    "nao_autorizado",
                    "Token inválido, expirado ou sem permissão (401). Confira MOVIDESK_TOKEN; "
                    "gerar uma chave nova invalida a anterior.",
                    401, corpo,
                )
            if resp.status_code == 404:
                if isinstance(corpo, str) and corpo.lstrip()[:20].lower().startswith(("<!doctype", "<html")):
                    # Página de erro do servidor web: a rota em si não existe.
                    raise MovideskErro(
                        "rota_inexistente",
                        f"A API não tem a rota '{ep}' (404 do servidor web). Confira o caminho em "
                        "listar_endpoints().",
                        404,
                    )
                raise MovideskErro("nao_encontrado", f"Recurso não encontrado em '{ep}' (404).", 404, corpo)
            if resp.status_code == 429:
                espera = _retry_after(resp, tentativa)
                if tentativa >= MAX_TENTATIVAS or espera > MAX_ESPERA_S:
                    raise MovideskErro(
                        "limite_excedido",
                        f"Limite da API excedido (429). Tente de novo em ~{int(espera)}s. "
                        "Lembre: 3 requisições com erro bloqueiam por 60s/120s/300s; "
                        "e das 07:01 às 18:59 o limite é 10 req/min.",
                        429, {"retry_after_s": int(espera), "resposta": corpo},
                    )
                log.warning("429 recebido; aguardando %.0fs (tentativa %d)", espera, tentativa + 1)
                await asyncio.sleep(espera)
                continue
            if resp.status_code >= 500:
                ultima_falha = f"Erro do servidor Movidesk ({resp.status_code})"
            else:
                raise MovideskErro(
                    "requisicao_invalida",
                    f"A API recusou a consulta ({resp.status_code}). Verifique os nomes de campos "
                    "em listar_endpoints() e a sintaxe OData (enums usam "
                    "Movidesk.Core.Data.Enums.Tipo'valor').",
                    resp.status_code, corpo,
                )
        if tentativa < MAX_TENTATIVAS:
            espera = 2 ** (tentativa + 1)
            log.warning("%s; nova tentativa em %ds", ultima_falha, espera)
            await asyncio.sleep(espera)
    raise MovideskErro("falha_rede", f"{ultima_falha} (após {MAX_TENTATIVAS + 1} tentativas).")


def _decodificar(resp: httpx.Response) -> Any:
    tipo = resp.headers.get("content-type", "")
    if "json" in tipo or resp.content[:1] in (b"{", b"["):
        try:
            return resp.json()
        except ValueError:
            pass
    if tipo.startswith("text/"):
        return {"content_type": tipo, "texto": resp.text}
    import base64

    conteudo = resp.content
    d: dict[str, Any] = {
        "content_type": tipo or "application/octet-stream",
        "tamanho_bytes": len(conteudo),
        "nome_arquivo": _nome_arquivo(resp),
    }
    if len(conteudo) <= 512 * 1024:
        d["base64"] = base64.b64encode(conteudo).decode()
    else:
        d["aviso"] = "Arquivo grande demais para devolver inline; use arquivo_saida para salvá-lo."
    d["_bytes"] = conteudo
    return d


def _nome_arquivo(resp: httpx.Response) -> str | None:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Paginação
# ---------------------------------------------------------------------------


def _eh_unitaria(info: dict[str, Any] | None, params: dict[str, str]) -> bool:
    if not info:
        return False
    chaves = {k.lower() for k in params}
    return any(p.lower() in chaves for p in info.get("parametro_unitario", []))


async def _paginar(ep: str, info: dict[str, Any], params: dict[str, str], max_registros: int):
    modo = info.get("paginacao", "nenhuma")
    itens: list[Any] = []
    paginas = 0
    tem_mais = False
    extra: dict[str, Any] = {}

    if modo == "odata":
        tamanho = int(params.pop("$top", PAGE_SIZE) or PAGE_SIZE)
        skip = int(params.pop("$skip", 0) or 0)
        while len(itens) < max_registros:
            pedir = min(tamanho, max_registros - len(itens))
            lote = (await requisitar(ep, {**params, "$top": str(pedir), "$skip": str(skip)})).json()
            paginas += 1
            if not isinstance(lote, list):
                return lote, paginas, False, {"aviso_paginacao": "Resposta não é lista; paginação interrompida."}
            itens.extend(lote)
            skip += len(lote)
            if len(lote) < pedir:
                break
        else:
            tem_mais = True  # bateu max_registros com a última página cheia

    elif modo == "cursor":
        lista_em = info.get("lista_em", "items")
        limite = min(int(params.pop("limit", 100) or 100), 100)
        cursor = params.pop("startingAfter", None)
        while len(itens) < max_registros:
            q = {**params, "limit": str(min(limite, max_registros - len(itens)))}
            if cursor:
                q["startingAfter"] = cursor
            corpo = (await requisitar(ep, q)).json()
            paginas += 1
            lote = corpo.get(lista_em, []) if isinstance(corpo, dict) else corpo
            itens.extend(lote)
            tem_mais = bool(corpo.get("hasMore")) if isinstance(corpo, dict) else False
            if not tem_mais or not lote:
                break
            cursor = str(lote[-1].get("id"))

    elif modo == "pagina":
        lista_em = info.get("lista_em", "items")
        pagina = int(params.pop("page", 0) or 0)
        tamanho = int(params.pop("pageSize", PAGE_SIZE) or PAGE_SIZE)
        total = None
        while len(itens) < max_registros:
            corpo = (await requisitar(ep, {**params, "page": str(pagina), "pageSize": str(tamanho)})).json()
            paginas += 1
            lote = corpo.get(lista_em, []) if isinstance(corpo, dict) else []
            total = corpo.get("totalSize") if isinstance(corpo, dict) else None
            itens.extend(lote)
            pagina += 1
            if not lote or (total is not None and pagina * tamanho >= total):
                break
        tem_mais = total is not None and len(itens) < total
        extra["total_na_api"] = total

    elif modo == "pagina_merged":
        if "id" in params:
            return (await requisitar(ep, params)).json(), 1, False, {}
        lista_em = info.get("lista_em", "mergedTickets")
        pagina = int(params.pop("page", 1) or 1)
        total_paginas = None
        corpo: Any = {}
        while len(itens) < max_registros:
            corpo = (await requisitar(ep, {**params, "page": str(pagina)})).json()
            paginas += 1
            lote = corpo.get(lista_em, []) if isinstance(corpo, dict) else []
            itens.extend(lote)
            m = re.search(r"(\d+)\s*of\s*(\d+)", str(corpo.get("pageNumber", ""))) if isinstance(corpo, dict) else None
            total_paginas = int(m.group(2)) if m else None
            if not lote or total_paginas is None or pagina >= total_paginas:
                break
            pagina += 1
        tem_mais = total_paginas is not None and pagina < total_paginas
        if isinstance(corpo, dict):
            extra["total_na_api"] = corpo.get("count")

    else:
        return _decodificar(await requisitar(ep, params)), 1, False, {}

    if len(itens) > max_registros:
        itens = itens[:max_registros]
        tem_mais = True
    return itens, paginas, tem_mais, extra


# ---------------------------------------------------------------------------
# Controle de tamanho da resposta
# ---------------------------------------------------------------------------


def _tamanho(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str))


def _caber(dados: Any, limite: int) -> tuple[Any, str | None]:
    """Corta a resposta para caber em `limite` caracteres. Devolve (dados, aviso)."""
    total = _tamanho(dados)
    if total <= limite:
        return dados, None
    if isinstance(dados, list):
        # busca binária do maior prefixo que cabe
        lo, hi = 0, len(dados)
        while lo < hi:
            meio = (lo + hi + 1) // 2
            if _tamanho(dados[:meio]) <= limite:
                lo = meio
            else:
                hi = meio - 1
        return dados[:lo], (
            f"Resposta muito grande ({total:,} caracteres, {len(dados)} registros): "
            f"mostrando só os primeiros {lo}."
        )
    if isinstance(dados, dict):
        dados = dict(dados)
        originais: dict[str, int] = {}
        for _ in range(50):
            listas = [(k, v) for k, v in dados.items() if isinstance(v, list) and v]
            if not listas or _tamanho(dados) <= limite:
                break
            k, v = max(listas, key=lambda kv: _tamanho(kv[1]))
            originais.setdefault(k, len(v))
            dados[k] = v[: len(v) // 2]
        if originais:
            dados["_listas_truncadas"] = {k: f"{len(dados[k])} de {n}" for k, n in originais.items()}
        if _tamanho(dados) <= limite:
            return dados, f"Resposta muito grande ({total:,} caracteres): listas internas foram encurtadas (veja _listas_truncadas)."
    texto = json.dumps(dados, ensure_ascii=False, default=str)
    return {"json_truncado": texto[:limite]}, f"Resposta muito grande ({total:,} caracteres): JSON cortado."


SUGESTAO_TAMANHO = (
    "Para reduzir: use $select com só os campos necessários, $filter para restringir "
    "(ex.: por data), $expand só das coleções que precisa, um max_registros menor ou "
    "arquivo_saida para gravar o resultado completo em disco."
)


def _salvar(caminho: str, dados: Any) -> str:
    destino = Path(caminho).expanduser()
    if not destino.is_absolute():
        destino = Path.cwd() / destino  # nunca dentro do pacote instalado
    destino.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(dados, dict) and "_bytes" in dados:
        destino.write_bytes(dados["_bytes"])
    else:
        destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(destino)


# ---------------------------------------------------------------------------
# Núcleo da ferramenta genérica
# ---------------------------------------------------------------------------


async def consultar(
    endpoint: str,
    params: dict[str, Any] | None = None,
    paginar: bool = False,
    max_registros: int = 1000,
    permitir_nao_listados: bool = False,
    arquivo_saida: str | None = None,
    limite_chars: int | None = None,
) -> dict[str, Any]:
    """Executa a consulta e devolve um dict pronto para o MCP (nunca lança exceção)."""
    try:
        ep, extras = normalizar_endpoint(endpoint)
        if not ep:
            raise MovideskErro("endpoint_invalido", "Informe um endpoint, ex.: 'tickets'.")
        checar_bloqueio(ep)
        q = _limpar_params({**extras, **(params or {})})
        info = resolver_endpoint(ep)
        avisos: list[str] = []

        if info is None:
            if not permitir_nao_listados:
                raise MovideskErro(
                    "endpoint_nao_mapeado",
                    f"'{ep}' não está em ENDPOINTS.md. Use listar_endpoints() para ver os disponíveis "
                    "ou passe permitir_nao_listados=True para tentar mesmo assim (só GET).",
                    detalhe={"disponiveis": sorted(ENDPOINTS)},
                )
            avisos.append("Endpoint fora do mapeamento: chamado sem validação de parâmetros.")
        else:
            _validar(ep, info, q)

        unitaria = _eh_unitaria(info, q)
        if paginar and info is not None and not unitaria and info.get("paginacao", "nenhuma") != "nenhuma":
            dados, paginas, tem_mais, extra = await _paginar(ep, info, q, max(1, max_registros))
        else:
            dados = _decodificar(await requisitar(ep, q))
            paginas, tem_mais, extra = 1, False, {}
            if info and not unitaria and info.get("paginacao") == "odata" and isinstance(dados, list):
                tam = int(q.get("$top", 0) or 0)
                if tam and len(dados) >= tam:
                    tem_mais = True
                    avisos.append("Pode haver mais registros: use paginar=True ou $skip.")

        resultado: dict[str, Any] = {"endpoint": ep, "params": q, "paginas_lidas": paginas}
        if isinstance(dados, list):
            resultado["total_registros"] = len(dados)
            resultado["tem_mais"] = tem_mais
        resultado.update({k: v for k, v in extra.items() if v is not None})

        if arquivo_saida:
            resultado["arquivo_salvo"] = _salvar(arquivo_saida, dados)
            if isinstance(dados, dict) and "_bytes" in dados:
                dados = {k: v for k, v in dados.items() if k not in ("_bytes", "base64")}
            else:
                amostra = dados[:5] if isinstance(dados, list) else dados
                dados, _ = _caber(amostra, min(limite_chars or MAX_CHARS, 20000))
                avisos.append("Resultado completo gravado em arquivo_salvo; abaixo vai só uma amostra.")
        elif isinstance(dados, dict) and "_bytes" in dados:
            dados = {k: v for k, v in dados.items() if k != "_bytes"}

        dados, aviso = _caber(dados, limite_chars or MAX_CHARS)
        if aviso:
            avisos.append(aviso)
            avisos.append(SUGESTAO_TAMANHO)
        resultado["dados"] = dados
        if avisos:
            resultado["avisos"] = avisos
        return json.loads(_ocultar(json.dumps(resultado, ensure_ascii=False, default=str)))
    except MovideskErro as e:
        return e.como_dict()
    except Exception as e:  # nunca deixa vazar stack trace com URL
        log.exception("Erro inesperado")
        return {"erro": "inesperado", "mensagem": _ocultar(f"{type(e).__name__}: {e}")}


def _validar(ep: str, info: dict[str, Any], q: dict[str, str]) -> None:
    """Validações locais: evitam requisições com erro, que geram bloqueio 429 na API."""
    chaves = {k.lower() for k in q}
    for obrig in info.get("obrigatorios", []):
        if obrig.lower() not in chaves:
            raise MovideskErro(
                "parametro_obrigatorio",
                f"'{ep}' exige o parâmetro '{obrig}'.",
                detalhe={"parametros": info.get("parametros")},
            )
    um_de = info.get("obrigatorios_um_de")
    if um_de and not chaves & {p.lower() for p in um_de}:
        raise MovideskErro("parametro_obrigatorio", f"'{ep}' exige um destes parâmetros: {', '.join(um_de)}.")
    if info.get("select_obrigatorio_em_lista") and not _eh_unitaria(info, q) and "$select" not in chaves:
        campos = info.get("campos") or ENDPOINTS.get(info.get("mesmo_layout_de", ""), {}).get("campos", [])
        raise MovideskErro(
            "select_obrigatorio",
            f"Listagens de '{ep}' exigem $select (ex.: \"$select\": \"id,subject,status\"). "
            "Coleções (actions, clients, customFieldValues...) vêm via $expand.",
            detalhe={"campos_disponiveis": campos},
        )
    if "$count" in chaves or "$inlinecount" in chaves:
        raise MovideskErro("parametro_nao_suportado", "A API do Movidesk não suporta $count/$inlinecount.")


# ---------------------------------------------------------------------------
# Servidor MCP
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "movidesk",
    instructions=(
        "Acesso somente leitura à API pública do Movidesk. Chame listar_endpoints() antes "
        "de montar consultas para ver campos, expansões e limites. Listagens de tickets "
        "exigem $select. Tickets com lastUpdate acima de 90 dias estão em tickets/past. "
        "Datas de filtro em UTC. Limite: 10 req/min das 07:01 às 18:59 (Brasília); 3 "
        "requisições com erro bloqueiam a API por 60s ou mais."
    ),
)


@mcp.tool()
async def movidesk_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
    paginar: bool = False,
    max_registros: int = 1000,
    permitir_nao_listados: bool = False,
    arquivo_saida: str | None = None,
) -> dict[str, Any]:
    """Chama qualquer endpoint GET da API do Movidesk, repassando os parâmetros sem restrição.

    Args:
        endpoint: Caminho relativo a /public/v1, ex.: "tickets", "tickets/past", "persons",
            "services", "activity", "timeAgreement", "timeAgreementConsumption",
            "survey/questions", "survey/responses", "kb/article", "article/123",
            "tickets/merged", "tickets/htmldescription", "storage/download".
        params: Parâmetros de query, repassados como estão: $select, $filter, $expand
            (inclusive aninhado, ex. "actions($expand=timeAppointments($expand=createdBy))"),
            $orderby, $top, $skip, id, protocol, includeDeletedItems, limit, startingAfter,
            page, pageSize, name, startDate etc. O token é adicionado automaticamente.
        paginar: Se True, percorre as páginas sozinho (OData $top/$skip, cursor
            limit/startingAfter ou page/pageSize, conforme o endpoint) até max_registros.
            Nesse modo, $top/limit/pageSize no params definem o tamanho de cada página.
        max_registros: Teto de registros quando paginar=True.
        permitir_nao_listados: Permite endpoints fora do ENDPOINTS.md (novidades da API).
            Endpoints que alteram dados continuam bloqueados.
        arquivo_saida: Caminho opcional para gravar o resultado completo (JSON, ou o binário
            de storage/download). A resposta traz só uma amostra.
    """
    return await consultar(endpoint, params, paginar, max_registros, permitir_nao_listados, arquivo_saida)


@mcp.tool()
async def listar_endpoints(endpoint: str | None = None, completo: bool = False) -> dict[str, Any]:
    """Mostra o mapeamento da API: endpoints, parâmetros, campos, expansões e limites.

    Args:
        endpoint: Se informado (ex. "tickets"), devolve todos os detalhes desse endpoint.
        completo: Se True, devolve os detalhes de todos os endpoints de uma vez.
    """
    if endpoint:
        ep, _ = normalizar_endpoint(endpoint)
        info = resolver_endpoint(ep)
        if not info:
            return {"erro": "endpoint_nao_mapeado", "disponiveis": sorted(ENDPOINTS)}
        detalhe = dict(info)
        if "mesmo_layout_de" in info:
            base = ENDPOINTS[info["mesmo_layout_de"]]
            detalhe.setdefault("campos", base.get("campos"))
            detalhe.setdefault("expansoes", base.get("expansoes"))
        return detalhe
    base = {
        "base_url": MAPA["base_url"],
        "limites": MAPA["limites"],
        "dicas_odata": MAPA["dicas_odata"],
        "bloqueados_por_alterarem_dados": MAPA["bloqueados"],
    }
    if completo:
        base["endpoints"] = MAPA["endpoints"]
    else:
        base["endpoints"] = [
            {
                "path": e["path"],
                "descricao": e["descricao"],
                "paginacao": e.get("paginacao"),
                "odata": e.get("odata", False),
                "parametros": sorted(e.get("parametros", {})),
            }
            for e in MAPA["endpoints"]
        ]
        base["dica"] = "Chame listar_endpoints(endpoint='tickets') para ver campos e expansões."
    return base


CAMPOS_TICKET_LISTA = (
    "id,protocol,subject,type,status,baseStatus,category,urgency,origin,ownerTeam,"
    "serviceFirstLevel,createdDate,lastUpdate,resolvedIn,closedIn"
)


def _filtro_datas(campo: str, inicio: str | None, fim: str | None) -> str | None:
    partes = []
    if inicio:
        partes.append(f"{campo} ge {_utc_iso(inicio, fim_do_dia=False)}")
    if fim:
        partes.append(f"{campo} le {_utc_iso(fim, fim_do_dia=True)}")
    return " and ".join(partes) or None


def _utc_iso(valor: str, fim_do_dia: bool) -> str:
    """'2024-01-31' (dia em Brasília) ou ISO com hora -> literal OData em UTC."""
    valor = valor.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        d = date.fromisoformat(valor)
        local = datetime(d.year, d.month, d.day, tzinfo=BRASILIA)
        if fim_do_dia:
            local += timedelta(days=1, seconds=-1)
    else:
        local = _parse_data(valor)
        if local is None:
            raise MovideskErro("data_invalida", f"Data inválida: '{valor}'. Use AAAA-MM-DD.")
        if local.tzinfo is None:
            local = local.replace(tzinfo=BRASILIA)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.00z")


def _parse_data(valor: Any) -> datetime | None:
    if not valor or not isinstance(valor, str):
        return None
    s = valor.strip().replace("Z", "+00:00").replace("z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # API manda 7 casas decimais
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _juntar(filtros: list[str | None]) -> str | None:
    validos = [f for f in filtros if f]
    if not validos:
        return None
    return " and ".join(f"({f})" if len(validos) > 1 else f for f in validos)


async def _tickets_recentes_e_antigos(
    params: dict[str, Any], max_registros: int, incluir_antigos: bool
) -> dict[str, Any]:
    recentes = await consultar("tickets", params, True, max_registros, limite_chars=10**12)
    if "erro" in recentes or not incluir_antigos:
        return recentes
    antigos = await consultar("tickets/past", params, True, max_registros, limite_chars=10**12)
    if "erro" in antigos:
        recentes.setdefault("avisos", []).append(f"tickets/past falhou: {antigos.get('mensagem')}")
        return recentes
    vistos = {t.get("id") for t in recentes["dados"]}
    juntos = recentes["dados"] + [t for t in antigos["dados"] if t.get("id") not in vistos]
    recentes["dados"] = juntos[:max_registros]
    recentes["total_registros"] = len(recentes["dados"])
    recentes["paginas_lidas"] += antigos["paginas_lidas"]
    recentes["tem_mais"] = recentes["tem_mais"] or antigos["tem_mais"] or len(juntos) > max_registros
    recentes["endpoint"] = "tickets + tickets/past"
    return recentes


def _finalizar(resultado: dict[str, Any]) -> dict[str, Any]:
    if "erro" in resultado:
        return resultado
    dados, aviso = _caber(resultado.get("dados"), MAX_CHARS)
    resultado["dados"] = dados
    if aviso:
        resultado.setdefault("avisos", []).extend([aviso, SUGESTAO_TAMANHO])
    return resultado


@mcp.tool()
async def listar_tickets(
    filtro: str | None = None,
    campos: str = CAMPOS_TICKET_LISTA,
    expandir: str | None = None,
    ordenar: str = "id desc",
    criado_de: str | None = None,
    criado_ate: str | None = None,
    max_registros: int = 100,
    incluir_antigos: bool = False,
) -> dict[str, Any]:
    """Lista tickets com filtros OData, paginando automaticamente.

    Args:
        filtro: Expressão $filter, ex.: "baseStatus eq 'Resolved' and ownerTeam eq 'Suporte'".
        campos: $select (vírgulas). Padrão: campos principais.
        expandir: $expand, ex.: "owner,clients($expand=organization),customFieldValues($expand=items)".
        ordenar: $orderby. Padrão "id desc".
        criado_de: Data inicial de criação (AAAA-MM-DD, dia em horário de Brasília).
        criado_ate: Data final de criação (AAAA-MM-DD, inclusiva).
        max_registros: Máximo de tickets retornados.
        incluir_antigos: Também consulta tickets/past (lastUpdate acima de 90 dias) e junta sem repetir.
    """
    try:
        f = _juntar([filtro, _filtro_datas("createdDate", criado_de, criado_ate)])
    except MovideskErro as e:
        return e.como_dict()
    params: dict[str, Any] = {"$select": campos, "$orderby": ordenar}
    if f:
        params["$filter"] = f
    if expandir:
        params["$expand"] = expandir
    return _finalizar(await _tickets_recentes_e_antigos(params, max_registros, incluir_antigos))


@mcp.tool()
async def buscar_ticket(
    id: str,
    incluir_excluidos: bool = False,
    incluir_html: bool = False,
) -> dict[str, Any]:
    """Busca um ticket completo pelo número ou protocolo, com todas as coleções.

    A consulta por id devolve o objeto inteiro: clientes, ações (com apontamentos,
    despesas e anexos), tickets pais e filhos, históricos de responsável e de status,
    campos adicionais e ativos. Se o ticket não estiver em /tickets (lastUpdate acima
    de 90 dias), busca em /tickets/past.

    Args:
        id: Número do ticket ou protocolo (ex. "12345" ou "MOVI202109000001").
        incluir_excluidos: includeDeletedItems=true (ações, clientes, pais e filhos excluídos).
        incluir_html: Também busca o HTML das ações (tickets/htmldescription).
    """
    chave = "id" if str(id).strip().isdigit() else "protocol"
    params: dict[str, Any] = {chave: str(id).strip()}
    if incluir_excluidos:
        params["includeDeletedItems"] = True
    origem = "tickets"
    r = await consultar("tickets", params)
    if r.get("erro") == "nao_encontrado" or (r.get("dados") in (None, [], {}) and "erro" not in r):
        origem = "tickets/past"
        r = await consultar("tickets/past", params)
    if "erro" in r:
        return r
    r["origem"] = origem
    if incluir_html:
        # tickets/past/htmldescription não existe na API (apesar da doc); usa a rota única.
        html = await consultar("tickets/htmldescription", {chave: str(id).strip()})
        r["html_acoes"] = html.get("dados") if "erro" not in html else html
    return r


CAMPOS_PESSOA_LISTA = "id,businessName,corporateName,personType,profileType,isActive,cpfCnpj,role,classification,createdDate"


@mcp.tool()
async def listar_pessoas(
    filtro: str | None = None,
    campos: str = CAMPOS_PESSOA_LISTA,
    expandir: str | None = "emails",
    ordenar: str | None = None,
    busca: str | None = None,
    max_registros: int = 100,
) -> dict[str, Any]:
    """Lista pessoas, empresas e departamentos (agentes e clientes), paginando sozinho.

    Args:
        filtro: $filter, ex.: "isActive eq true", "relationships/any(o: o/name eq 'ACME')".
        campos: $select. Padrão: campos principais.
        expandir: $expand, ex.: "emails,contacts,addresses,relationships($expand=services),customFieldValues($expand=items)".
        ordenar: $orderby, ex.: "businessName".
        busca: Atalho que filtra businessName contendo o texto (contains).
        max_registros: Máximo de registros.
    """
    filtros = [filtro]
    if busca:
        texto = busca.replace("'", "''")
        filtros.append(f"contains(businessName, '{texto}')")
    params: dict[str, Any] = {"$select": campos}
    f = _juntar(filtros)
    if f:
        params["$filter"] = f
    if expandir:
        params["$expand"] = expandir
    if ordenar:
        params["$orderby"] = ordenar
    return _finalizar(await consultar("persons", params, True, max_registros, limite_chars=10**12))


def _horas(inicio: Any, fim: Any) -> float | None:
    """Horas entre duas datas da API (None se faltar alguma)."""
    a, b = _parse_data(inicio), _parse_data(fim)
    if not a or not b:
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return round((b - a).total_seconds() / 3600, 2)


def _conferir_sla(contagem: dict[str, int], realizado: Any, prazo: Any) -> None:
    if not prazo:
        contagem["sem_sla"] += 1
        return
    folga = _horas(realizado, prazo)
    if folga is not None:
        contagem["no_prazo" if folga >= 0 else "fora_do_prazo"] += 1


def _estat(valores: list[float]) -> dict[str, Any] | None:
    if not valores:
        return None
    return {"qtd": len(valores), "media_h": round(mean(valores), 2), "mediana_h": round(median(valores), 2)}


@mcp.tool()
async def resumo_tickets(
    data_inicio: str,
    data_fim: str,
    filtro: str | None = None,
    max_registros: int = 5000,
) -> dict[str, Any]:
    """Resumo estatístico dos tickets criados num período.

    Conta por status base, status, equipe, responsável, categoria, urgência, serviço,
    origem e dia; calcula tempo de resolução (média e mediana) e cumprimento de SLA
    de solução e de primeira resposta. Consulta também tickets/past se o período
    começar há mais de 90 dias.

    Args:
        data_inicio: AAAA-MM-DD (dia em horário de Brasília).
        data_fim: AAAA-MM-DD (inclusivo).
        filtro: $filter extra opcional, ex.: "ownerTeam eq 'Suporte'".
        max_registros: Teto de tickets analisados (cada 100 = 1 requisição).
    """
    try:
        f = _juntar([_filtro_datas("createdDate", data_inicio, data_fim), filtro])
        inicio = date.fromisoformat(data_inicio.strip()[:10])
    except (MovideskErro, ValueError) as e:
        return e.como_dict() if isinstance(e, MovideskErro) else {"erro": "data_invalida", "mensagem": str(e)}
    params = {
        "$select": "id,type,status,baseStatus,ownerTeam,category,urgency,origin,serviceFirstLevel,"
        "createdDate,resolvedIn,closedIn,slaSolutionDate,slaResponseDate,slaRealResponseDate",
        "$expand": "owner",
        "$filter": f,
        "$orderby": "id asc",
    }
    antigos = (date.today() - inicio).days > 85
    r = await _tickets_recentes_e_antigos(params, max_registros, antigos)
    if "erro" in r:
        return r
    tickets: list[dict[str, Any]] = r["dados"]

    def contar(chave) -> dict[str, int]:
        c = Counter((chave(t) if callable(chave) else t.get(chave)) or "(vazio)" for t in tickets)
        return dict(c.most_common())

    resolucao, primeira_resp = [], []
    sla_sol = {"no_prazo": 0, "fora_do_prazo": 0, "sem_sla": 0}
    sla_resp = {"no_prazo": 0, "fora_do_prazo": 0, "sem_sla": 0}
    for t in tickets:
        h = _horas(t.get("createdDate"), t.get("resolvedIn"))
        if h is not None and h >= 0:
            resolucao.append(h)
        h1 = _horas(t.get("createdDate"), t.get("slaRealResponseDate"))
        if h1 is not None and h1 >= 0:
            primeira_resp.append(h1)
        _conferir_sla(sla_sol, t.get("resolvedIn"), t.get("slaSolutionDate"))
        _conferir_sla(sla_resp, t.get("slaRealResponseDate"), t.get("slaResponseDate"))

    def pct(d: dict[str, int]) -> float | None:
        base = d["no_prazo"] + d["fora_do_prazo"]
        return round(100 * d["no_prazo"] / base, 1) if base else None

    resumo = {
        "periodo": {"inicio": data_inicio, "fim": data_fim, "filtro_aplicado": f},
        "fonte": r["endpoint"],
        "total_tickets": len(tickets),
        "amostra_incompleta": r.get("tem_mais", False),
        "por_status_base": contar("baseStatus"),
        "por_status": contar("status"),
        "por_equipe": contar("ownerTeam"),
        "por_responsavel": contar(lambda t: (t.get("owner") or {}).get("businessName")),
        "por_categoria": contar("category"),
        "por_urgencia": contar("urgency"),
        "por_servico": contar("serviceFirstLevel"),
        "por_origem": contar(lambda t: str(t.get("origin")) if t.get("origin") is not None else None),
        "por_tipo": contar(lambda t: {1: "Interno", 2: "Público"}.get(t.get("type"), t.get("type"))),
        "por_dia_criacao": dict(sorted(Counter(
            (t.get("createdDate") or "")[:10] or "(vazio)" for t in tickets).items())),
        "tempo_resolucao": _estat(resolucao),
        "tempo_primeira_resposta": _estat(primeira_resp),
        "sla_solucao": {**sla_sol, "percentual_no_prazo": pct(sla_sol)},
        "sla_primeira_resposta": {**sla_resp, "percentual_no_prazo": pct(sla_resp)},
        "requisicoes": r["paginas_lidas"],
    }
    if r.get("tem_mais"):
        resumo["aviso"] = f"Parou em {max_registros} tickets; aumente max_registros para analisar o período inteiro."
    if r.get("avisos"):
        resumo["avisos"] = r["avisos"]
    return resumo


@mcp.resource("movidesk://endpoints", mime_type="text/markdown")
def recurso_endpoints() -> str:
    """ENDPOINTS.md: mapeamento completo da API do Movidesk."""
    return ENDPOINTS_MD.read_text(encoding="utf-8")


def main() -> None:
    """Ponto de entrada do comando `movidesk-mcp-server` (transporte stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
