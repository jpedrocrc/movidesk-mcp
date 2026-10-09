# Movidesk MCP (somente leitura)

Servidor MCP em Python (transporte stdio) para a API pública do Movidesk
(`https://api.movidesk.com/public/v1`). Ele lê qualquer dado que a API expõe, com
todos os parâmetros OData (`$select`, `$filter`, `$expand` aninhado, `$orderby`,
`$top`, `$skip`) e os parâmetros próprios de cada rota. Nunca cria, altera ou exclui nada.

## Início rápido

1. Instalar

```bash
pip install movidesk-mcp-server
```

2. Configurar o Claude Desktop

Adicione ao `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`;
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

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

Depois feche o Claude Desktop por completo (no Windows: ícone perto do relógio > Sair) e abra de novo.

3. Ou registrar no Claude Code

```bash
claude mcp add movidesk -e MOVIDESK_TOKEN=seu-token-aqui -- movidesk-mcp-server
```

O token é gerado no Movidesk em **Configurações > Conta > Parâmetros > aba Ambiente > Gerar nova chave**.
Gerar uma chave nova invalida a anterior.

### Atualizar

```bash
pip install --upgrade movidesk-mcp-server
```

Depois reinicie o Claude Desktop. A configuração não muda.

### Exemplos de pedidos ao Claude

- "Liste os endpoints do Movidesk e o que cada um traz"
- "Busque o ticket 12345 com todas as ações e o histórico de status"
- "Faça um resumo dos tickets criados em setembro: status, equipes e SLA"
- "Liste os tickets resolvidos pela equipe Suporte nesta semana"
- "Quais clientes da empresa ACME estão ativos?"

## Dados disponíveis

Todos os endpoints abaixo foram testados contra a API real em 09/10/2026.
Os campos, expansões e limites de cada um estão em [`ENDPOINTS.md`](src/movidesk_mcp/ENDPOINTS.md).

| Endpoint | Conteúdo |
|---|---|
| `tickets` | Tickets com última atualização nos últimos 90 dias: dados, clientes, ações, apontamentos, despesas, anexos, históricos de responsável e status, campos adicionais, ativos |
| `tickets/past` | Tickets mais antigos, com o mesmo layout |
| `tickets/htmldescription` | HTML das ações de um ticket (recente ou antigo) |
| `tickets/merged` | Tickets mesclados, por ticket ou por período |
| `persons` | Pessoas, empresas e departamentos (agentes e clientes), com e-mails, contatos, endereços e organizações |
| `services` | Catálogo de serviços |
| `activity` | Atividades usadas em apontamento de horas |
| `timeAgreement` | Contratos de horas |
| `timeAgreementConsumption` | Consumo de horas de um contrato por período |
| `survey/questions`, `survey/questions/{id}` | Perguntas da pesquisa de satisfação |
| `survey/responses` | Respostas da pesquisa de satisfação (NPS, carinhas etc.) |
| `kb/article`, `article/{id}` | Busca e conteúdo de artigos da base de conhecimento |
| `storage/download` | Download de anexos pelo hash |

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

## Instalação: outras formas

Requer Python 3.10 ou superior.

| Forma | Comando |
|---|---|
| PyPI | `pip install movidesk-mcp-server` |
| Sem instalar, com uv | `uvx movidesk-mcp-server` (no `claude_desktop_config.json`: `"command": "uvx", "args": ["movidesk-mcp-server"]`) |
| GitHub (exige acesso ao repositório) | `pip install git+https://github.com/jpedrocrc/movidesk-mcp` |

**Windows:** o `pip` coloca o executável em `...\Python3xx\Scripts`. Se essa pasta
não estiver no PATH, o Claude Desktop não encontra `movidesk-mcp-server`. Nesse caso,
use o caminho completo do `.exe` em `"command"`, ou `"command": "python"` com
`"args": ["-m", "movidesk_mcp"]`.

## Desenvolvimento

### Estrutura

| Arquivo | Conteúdo |
|---|---|
| `pyproject.toml` | Pacote, dependências e comando `movidesk-mcp-server` |
| `src/movidesk_mcp/server.py` | Servidor MCP e ferramentas |
| `src/movidesk_mcp/ENDPOINTS.md` | Mapeamento da API (endpoints, parâmetros, campos, expansões e limites). O bloco JSON no fim é lido pelo servidor; para cobrir um endpoint novo, basta acrescentá-lo ali. |
| `test_server.py` | Testes offline e de cobertura contra a API real |

### Ambiente local

```bash
git clone https://github.com/jpedrocrc/movidesk-mcp
cd movidesk-mcp
pip install -e .
```

Com `-e`, as mudanças no código valem sem reinstalar: basta reiniciar o Claude.
Para voltar à versão publicada: `pip uninstall -y movidesk-mcp-server` e depois `pip install movidesk-mcp-server`.

O pacote exige `mcp<2`: o SDK `mcp` 2.x renomeou o `FastMCP`, e a migração ainda não foi feita.

### Testes

```bash
python test_server.py --offline
```

Esse modo não usa rede: valida o mapeamento, o registro das ferramentas e as travas (escrita bloqueada, `$select` obrigatório, token ocultado).

```bash
python test_server.py
```

Esse modo precisa de `MOVIDESK_TOKEN` no ambiente. Ele chama cada endpoint mapeado com `$top=1` (ou `limit=1`/`pageSize=1`) e usa os IDs que encontra para testar as rotas que pedem um id (HTML das ações, pergunta da pesquisa, artigo, anexo, consumo do contrato). No horário limitado leva cerca de 2 minutos.

Para testar com o MCP Inspector (usa `uv` e `npx`), rode o comando abaixo e defina `MOVIDESK_TOKEN` em *Environment Variables* antes de conectar:

```bash
mcp dev src/movidesk_mcp/server.py
```

### Publicar uma nova versão

1. Aumente a versão em `pyproject.toml` (`version`) e em `src/movidesk_mcp/__init__.py` (`__version__`). O PyPI não aceita reenviar um número de versão já publicado.
2. Rode os dois testes acima.
3. Gere os pacotes:
   ```powershell
   Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue; uv build
   ```
4. Publique com o token do PyPI de escopo restrito ao projeto `movidesk-mcp-server`:
   ```powershell
   $env:UV_PUBLISH_TOKEN = "pypi-..."; uv publish
   ```
5. Confira num ambiente limpo: `pip install --upgrade movidesk-mcp-server` e `pip show movidesk-mcp-server`.
6. Faça commit e push no GitHub.

Se uma versão publicada tiver problema, **retire-a (yank)** em pypi.org > projeto > Lançamentos > versão > *Retirar lançamento*, e publique a correção numa versão nova. Não exclua arquivos: a exclusão é permanente e o nome do arquivo não pode ser reutilizado.

## Histórico de versões

| Versão | Mudanças |
|---|---|
| 1.0.1 | Exige `mcp<2`. Na 1.0.0, uma instalação limpa trazia o `mcp` 2.x e o servidor não iniciava. |
| 1.0.0 | Primeira versão. **Retirada (yank)** do PyPI pelo problema acima. |

## Licença

MIT. Veja [LICENSE](LICENSE).
