# Movidesk MCP (somente leitura)

Servidor MCP em Python (FastMCP, transporte stdio) para a API pública do Movidesk
(`https://api.movidesk.com/public/v1`). Ele lê qualquer dado que a API expõe, com
todos os parâmetros OData (`$select`, `$filter`, `$expand` aninhado, `$orderby`,
`$top`, `$skip`) e os parâmetros próprios de cada rota. Nunca cria, altera ou exclui nada.

## Início rápido

1. Instalar

```bash
pip install movidesk-mcp-server
```

2. Configurar o Claude Desktop

Adicione ao `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "movidesk": {
      "command": "movidesk-mcp-server",
      "env": {
        "MOVIDESK_TOKEN": "seu-token-aqui"
      }
    }
  }
}
```

3. Ou registrar no Claude Code

```bash
claude mcp add movidesk -e MOVIDESK_TOKEN=seu-token-aqui -- movidesk-mcp-server
```

O token é gerado no Movidesk em **Configurações > Conta > Parâmetros > aba Ambiente > Gerar nova chave**.
Gerar uma chave nova invalida a anterior.

## Estrutura

| Arquivo | Conteúdo |
|---|---|
| `pyproject.toml` | Pacote e comando `movidesk-mcp-server` |
| `src/movidesk_mcp/server.py` | Servidor MCP e ferramentas |
| `src/movidesk_mcp/ENDPOINTS.md` | Mapeamento da API (endpoints, parâmetros, campos, expansões e limites). O bloco JSON no fim é lido pelo servidor. |
| `test_server.py` | Teste de cobertura contra a API real (`$top=1` em cada endpoint) e teste offline |

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `movidesk_get(endpoint, params, paginar, max_registros, permitir_nao_listados, arquivo_saida)` | Chama qualquer GET e repassa os parâmetros sem restrição. Com `paginar=True` percorre as páginas sozinho (OData, cursor ou `page`, conforme a rota) até `max_registros`. |
| `listar_endpoints(endpoint, completo)` | Devolve o mapeamento para o modelo saber o que pode pedir |
| `listar_tickets(...)` | Lista tickets com filtro, campos, expansões e período; `incluir_antigos=True` junta `tickets/past` |
| `buscar_ticket(id)` | Ticket completo (todas as coleções) por número ou protocolo; procura em `tickets/past` se precisar; `incluir_html=True` traz o HTML das ações |
| `listar_pessoas(...)` | Pessoas, empresas e departamentos, com busca por nome |
| `resumo_tickets(data_inicio, data_fim)` | Contagens por status, equipe, responsável, categoria, urgência, serviço, origem e dia, mais tempo de resolução e cumprimento de SLA |

Também há o recurso `movidesk://endpoints` com o conteúdo do `ENDPOINTS.md`.

Exemplo de chamada genérica:

```json
{
  "endpoint": "tickets",
  "params": {
    "$select": "id,subject,status,createdDate",
    "$filter": "createdDate ge 2026-09-01T00:00:00.00z and ownerTeam eq 'Suporte'",
    "$expand": "owner,actions($select=id,origin;$expand=timeAppointments($expand=createdBy)),customFieldValues($expand=items)",
    "$orderby": "id desc"
  },
  "paginar": true,
  "max_registros": 500
}
```

## Instalação: detalhes

Requer Python 3.10 ou superior.

| Forma | Comando |
|---|---|
| PyPI | `pip install movidesk-mcp-server` |
| Sem instalar, com uv | `uvx movidesk-mcp-server` |
| GitHub | `pip install git+https://github.com/jpedrocrc/movidesk-mcp` |
| Código local | `pip install -e .` na pasta do projeto (para editar o código sem reinstalar) |

**Windows:** o `pip` coloca o executável em `...\Python3xx\Scripts`. Se essa pasta
não estiver no PATH, o Claude Desktop não encontra `movidesk-mcp-server`. Nesse caso,
use o caminho completo do `.exe` em `"command"`, ou `"command": "python"` com
`"args": ["-m", "movidesk_mcp"]`.

Para publicar no PyPI (é preciso ter conta em pypi.org):

```bash
uv build
uv publish
```

### Testar com o MCP Inspector

`mcp dev` usa o `uv` e o `npx` (Node.js).

```bash
mcp dev src/movidesk_mcp/server.py
```

No Inspector, defina a variável `MOVIDESK_TOKEN` na seção *Environment Variables* antes de conectar.

## Teste de cobertura

```bash
python test_server.py --offline
```

Esse modo não usa rede: valida o mapeamento, o registro das ferramentas e as travas (escrita bloqueada, `$select` obrigatório, token ocultado).

```bash
python test_server.py
```

Esse modo precisa de `MOVIDESK_TOKEN` no ambiente. Ele chama cada endpoint mapeado com `$top=1` (ou `limit=1`/`pageSize=1`) e usa os IDs que encontra para testar as rotas que pedem um id (HTML das ações, pergunta da pesquisa, artigo, anexo, consumo do contrato). No horário limitado leva cerca de 2 minutos.

## Limites e comportamento

- **Rate limit:** 10 req/min das 07:01 às 18:59 (Brasília); livre das 19:00 às 07:00. O servidor segura as chamadas localmente nesse horário para não estourar o limite.
- **Bloqueio por erro:** 3 requisições com erro bloqueiam a API por 60 s, depois 120 s, depois 300 s. Por isso o servidor valida localmente o que consegue antes de chamar a API (por exemplo, `$select` obrigatório nas listas de tickets) e, num 429, espera o tempo do header `retry-after` e tenta de novo.
- **Erros:** 401 (token), 404, 400 (com a mensagem da API) e timeout voltam como JSON `{"erro": ..., "mensagem": ...}`. Timeout e 5xx são repetidos com backoff.
- **Respostas grandes:** acima de `MOVIDESK_MAX_CHARS` a resposta é cortada, com um aviso que sugere `$select`/`$filter`. Use `arquivo_saida` para gravar o resultado completo em disco.
- **Tickets antigos:** `/tickets` só traz tickets com `lastUpdate` nos últimos 90 dias. Os demais estão em `/tickets/past`.
- **Segurança:** só GET. As rotas de telefonia que usam GET mas registram chamadas (`asterisk_*`) estão bloqueadas, mesmo com `permitir_nao_listados=True`. O token só é lido do ambiente, nunca vai para o log, e qualquer ocorrência dele é removida das respostas.

### Variáveis de ambiente opcionais

| Variável | Padrão | Uso |
|---|---|---|
| `MOVIDESK_RATE_LIMIT` | `10` | Requisições por minuto (0 desliga o limitador local) |
| `MOVIDESK_RATE_LIMIT_MODO` | `horario` | `horario` limita só das 07:01 às 18:59; `sempre` limita 24 h |
| `MOVIDESK_PAGE_SIZE` | `100` | Tamanho da página na paginação automática |
| `MOVIDESK_MAX_RETRIES` | `3` | Novas tentativas em 429/5xx/timeout |
| `MOVIDESK_MAX_ESPERA` | `320` | Maior espera (s) aceita num retry de 429 |
| `MOVIDESK_TIMEOUT` | `60` | Timeout por requisição (s) |
| `MOVIDESK_MAX_CHARS` | `60000` | Tamanho máximo da resposta antes de cortar |
| `MOVIDESK_LOG_LEVEL` | `WARNING` | Nível de log (sempre em stderr) |

## Licença

MIT. Veja [LICENSE](LICENSE).
