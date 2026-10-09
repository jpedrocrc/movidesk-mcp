# Mapeamento da API pública do Movidesk (somente leitura)

Base: `https://api.movidesk.com/public/v1`
Levantado em 09/10/2026 a partir da base de conhecimento oficial (`atendimento.movidesk.com/kb`).

> O bloco JSON no final deste arquivo é a fonte de verdade do `server.py`: o servidor
> lê esse bloco na inicialização para validar endpoints, saber como paginar e
> responder `listar_endpoints()`. Se a API ganhar um endpoint novo, basta
> acrescentá-lo ao JSON.

---

## 1. Regras gerais

| Item | Valor documentado | Fonte |
|---|---|---|
| Autenticação | Parâmetro de query `token` (as APIs de Base de Conhecimento e Anexos também aceitam o token no header) | todos os artigos |
| Formato | JSON (exceto `storage/download`, que devolve o arquivo binário) | |
| Rate limit | **10 requisições/minuto** das **07:01 às 18:59** (horário de Brasília) para tickets, pessoas e pesquisa de satisfação. **Acesso irrestrito das 19:00 às 07:00.** Os demais artigos também citam 10 req/min. | [Horário e limite de acesso da API](https://atendimento.movidesk.com/kb/pt-br/article/272370/horario-e-limite-de-acesso-da-api) |
| Bloqueio por erro | Depois de **3 requisições com erro** a API devolve `429 - Too many failed requests` e bloqueia por **60 s**. Mais 3 erros: **120 s**. Mais 3: **300 s**. O tempo restante vem no header `retry-after`. | [API de Tickets](https://atendimento.movidesk.com/kb/pt-br/article/256/movidesk-ticket-api) |
| Replicação | Registros novos ou alterados podem levar alguns minutos para aparecer nas consultas. | API de Tickets |
| OData | `$filter`, `$orderby`, `$top`, `$skip`, `$select`, `$expand`. **`$count` e `$inlinecount` NÃO são suportados.** | API de Tickets |
| `$select` | **Obrigatório** nas listagens de `tickets`, `tickets/past` e `timeAgreement`. Sem ele: `400 "The $select parameter is required."` | API de Tickets, Contrato de horas, [Erros frequentes](https://atendimento.movidesk.com/kb/pt-br/article/306536/erros-frequentes-api-de-tickets) |
| `$select` dentro de `$expand` | Funciona em tickets (`actions($select=id,origin)`). Em pessoas a doc diz que é ignorado (vêm todas as propriedades). | API de Tickets / Pessoas |
| Tamanho de página OData | **A documentação não informa um máximo de `$top`.** Os exemplos oficiais paginam com `$top=100&$skip=N`. O servidor usa 100 por padrão (`MOVIDESK_PAGE_SIZE`). | |
| Paginação por cursor | `activity` e `survey/responses`: `limit` (1–100, padrão 100) + `startingAfter` (id do último item); a resposta traz `hasMore`. | |

### 1.1 Dicas de OData (da documentação)

- Datas sempre em UTC: `$filter=createdDate ge 2024-01-01T00:00:00.00z and createdDate le 2024-01-31T23:59:59.00z`
- Operadores: `eq ne gt lt ge le`, `and`, `or`, `not`, parênteses.
- Coleções com lambda: `clients/any(c: c/id eq '123')`, `statusHistories/any(s: s/status eq 'Resolvido')`, `contacts/any()`, `not emails/any()`, `clients/all(c: c/profileType eq Movidesk.Core.Data.Enums.ProfileType'3')`
- **Enums precisam do nome do tipo** (comparar com inteiro gera 400 "incompatible types"):
  `origin eq Movidesk.Core.Data.Enums.TicketOrigin'9'`, `profileType eq Movidesk.Core.Data.Enums.ProfileType'3'`
- `$expand` aninhado: `customFieldValues($expand=items)`, `actions($expand=timeAppointments($expand=createdBy))`
- Strings com aspas simples: `baseStatus eq 'Resolved'`, `ownerTeam eq 'Suporte'`

### 1.2 Endpoints que existem mas NÃO são expostos

| Endpoint | Motivo |
|---|---|
| `asterisk_startTransferedCall`, `asterisk_completedCall`, `asterisk_startCanceledCall` (GET) | Apesar de serem GET, **registram/alteram chamadas** na telefonia (escrita). Bloqueados sempre. |
| `asterisk_receivedCall`, `asterisk_transferedCall`, `asterisk_lostCall`, `asterisk_canceledCall`, `setMadeCallLink` | POST (telefonia) |
| `ticketFileUpload`, `addTeamsToActivity`, `persons/relationships` (POST) | Escrita |
| `ticketCustomFieldValue/InsertValues`, `/UpdateValues`, `/DeleteValues` | Escrita (POST) |
| POST/PATCH/DELETE de `tickets`, `persons`, `services`, `activity`, `timeAgreement` | Escrita |

---

## 2. Endpoints de leitura

### 2.1 `tickets` — tickets com `lastUpdate` nos últimos 90 dias
Doc: [API do Movidesk - Tickets](https://atendimento.movidesk.com/kb/pt-br/article/256/movidesk-ticket-api)

| Uso | Parâmetros |
|---|---|
| Um ticket | `id` **ou** `protocol`; opcional `includeDeletedItems=true` (traz ações, clientes, pais e filhos excluídos). Devolve o objeto completo, com todas as coleções. |
| Lista | `$select` (**obrigatório**), `$filter`, `$orderby`, `$top`, `$skip`, `$expand`, `includeDeletedItems` |

Campos: `id, protocol, type (1 interno, 2 público), subject, category, urgency, status, baseStatus (New, InAttendance, Stopped, Canceled, Resolved, Closed), justification, origin, createdDate, originEmailAccount, owner*, ownerTeam, createdBy*, serviceFull, serviceFirstLevelId, serviceFirstLevel, serviceSecondLevel, serviceThirdLevel, contactForm, tags, cc, resolvedIn, reopenedIn, closedIn, lastActionDate, actionCount, lastUpdate, lifetimeWorkingTime, stoppedTime, stoppedTimeWorkingTime, resolvedInFirstCall, chatWidget, chatGroup, chatTalkTime, chatWaitingTime, sequence, slaAgreement, slaAgreementRule, slaSolutionTime, slaResponseTime, slaSolutionChangedByUser, slaSolutionChangedBy*, slaSolutionDate, slaSolutionDateIsPaused, slaResponseDate, slaRealResponseDate, jiraIssueKey, redmineIssueId, clients*, actions*, parentTickets*, childrenTickets*, ownerHistories*, statusHistories*, customFieldValues*, assets*`
(* = objeto ou coleção; numa lista só vem com `$expand`)

`origin`: 1 web cliente, 2 web agente, 3 e-mail, 4 gatilho, 5 chat online, 6 chat offline (ações), 7 e-mail do sistema, 8 formulário de contato, 9 web API, 10 agendamento automático, 11 Jira, 12 Redmine, 13 ReceivedCall, 14 MadeCall, 15 LostCall, 16 DropoutCall, 17 acesso remoto, 18 WhatsApp, 19 MovideskIntegration, 20 ZenviaChat, 21 NotAnsweredCall, 22 FacebookMessenger, 23 WhatsApp Business Movidesk, 24 Altu, 25 WhatsApp Ativo.

Sub-entidades:

| Coleção | Campos |
|---|---|
| `owner`, `createdBy`, `slaSolutionChangedBy`, `clients[].organization`, `actions[].createdBy`, `…timeAppointments[].createdBy` (person) | `id, businessName, email, phone, personType (1 pessoa, 2 empresa, 4 departamento), profileType (1 agente, 2 cliente, 3 ambos)` |
| `clients[]` | person + `isDeleted, organization` |
| `actions[]` | `id, type, origin, description (texto), htmlDescription (só na busca por id), status, justification, createdDate, createdBy, isDeleted, timeAppointments, expenses, attachments, tags` |
| `actions[].timeAppointments[]` | `id, activity, date, periodStart, periodEnd, workTime, accountedTime, workTypeName, createdBy, createdByTeam {id, name}` |
| `actions[].expenses[]` | `id, type, serviceReport, createdBy, createdByTeam, date, quantity, value` |
| `actions[].attachments[]` | `fileName, path (hash para storage/download), createdBy, createdDate` |
| `parentTickets[]`, `childrenTickets[]` | `id, subject, isDeleted` |
| `ownerHistories[]` | `ownerTeam, owner, permanencyTimeFullTime, permanencyTimeWorkingTime (s), changedBy, changedDate` |
| `statusHistories[]` | `status, justification, permanencyTimeFullTime, permanencyTimeWorkingTime (s), changedBy, changedDate` |
| `customFieldValues[]` | `customFieldId, customFieldRuleId, line, value, items[] {personId, clientId, team, customFieldItem, storageFileGuid, fileName}`. Só aparecem campos cujas regras de exibição são atendidas no momento da consulta. |
| `assets[]` | `id, name, label, serialNumber, categoryFull, categoryFirstLevel, categorySecondLevel, categoryThirdLevel, isDeleted` |
| `satisfactionSurveyResponses[]` | **Depreciado**: não é mais retornado; use `survey/responses`. |

### 2.2 `tickets/past` — tickets antigos
Doc: [API do Movidesk - Tickets/Past](https://atendimento.movidesk.com/kb/pt-br/article/446011/movidesk-ticket-api-past)
Mesmos parâmetros, campos e expansões de `tickets`. A doc diz que a rota traz os tickets "no formato D-1" pela data de atualização; os tickets atualizados hoje ficam em `/tickets`. Tickets com `lastUpdate` acima de 90 dias **só** existem aqui.

### 2.3 `tickets/htmldescription` — HTML das ações
Parâmetros: `id` ou `protocol` (obrigatório), `actionId` (opcional). Retorno: `{id, description (HTML)}`.
A doc de Tickets/Past cita `tickets/past/htmldescription`, mas essa rota **não existe**: a API responde
com a página HTML de 404 do servidor web (testado em 09/10/2026). Por isso ela não está no mapeamento.
`tickets/htmldescription` atende também tickets antigos (testado com um id de `tickets/past`).

### 2.4 `tickets/merged` — tickets mesclados
Doc: [API do Movidesk - Tickets/Merged](https://atendimento.movidesk.com/kb/pt-br/article/488900/api-do-movidesk-tickets-merged)

| Uso | Parâmetros | Retorno |
|---|---|---|
| Por ticket | `id` (número ou protocolo); os demais são ignorados | `{ticketId, mergedTickets, mergedTicketsIds ("a;b;c"), lastUpdate}` |
| Por período | `startDate`, `endDate` (UTC), `page` | `{startDate, endDate, count, pageNumber ("1 of 333"), mergedTickets[]}` |

Erros: 404 se o id não existe; 400 se `startDate > endDate`.

### 2.5 `persons` — pessoas, empresas e departamentos
Doc: [API do Movidesk - Pessoas](https://atendimento.movidesk.com/kb/pt-br/article/189/movidesk-person-api)
Parâmetros: `id` (uma pessoa) ou OData completo (lista).

Campos: `id, codeReferenceAdditional, isActive, personType, profileType, accessProfile, corporateName, businessName, businessBranch, cpfCnpj, userName, role, bossId, bossName, classification, cultureId, timeZoneId, authenticateOn, createdDate, createdBy, changedDate, changedBy, observations, addresses*, contacts*, emails*, teams, relationships*, customFieldValues*, atAssets*` (`password` é somente escrita).

| Coleção | Campos |
|---|---|
| `addresses[]` | `addressType, country, postalCode, state, city, district, street, number, complement, reference, isDefault` |
| `contacts[]` | `contactType, contact, isDefault` |
| `emails[]` | `emailType, email, isDefault` |
| `relationships[]` | `id, name, slaAgreement, forceChildrenToHaveSomeAgreement, allowAllServices, includeInParents, loadChildOrganizations, services[] {id, name, copyToChildren}` |
| `customFieldValues[]` | `customFieldId, customFieldRuleId, line, value, items[] {personId, clientId, team, customFieldItem}` |
| `atAssets[]` | `id, name, label` |

### 2.6 `services` — catálogo de serviços
Doc: [API do Movidesk - Serviços](https://atendimento.movidesk.com/kb/pt-br/article/7440/api-servicos)
Parâmetros: `id` ou OData completo.
Campos: `id, name, description, parentServiceId, serviceForTicketType (0 público, 1 interno, 2 ambos), isVisible (1 agente, 2 cliente, 3 ambos), allowSelection, allowFinishTicket, isActive, automationMacro, defaultCategory, defaultUrgency, allowAllCategories, categories (array de strings)`.

### 2.7 `activity` — atividades (para apontamento de horas)
Doc: [API do Movidesk - Atividades](https://atendimento.movidesk.com/kb/pt-br/article/55923/api-de-atividades)
Parâmetros: `id` (uma atividade) **ou** `limit` (1–100), `startingAfter` (cursor), `name`. Sem OData.
Lista: `{hasMore, items[]}`. Campos: `id, name, isActive, isAllowsAllTeams, teams[] {name}`.

### 2.8 `timeAgreement` — contratos de horas
Doc: [Cadastro de contrato de horas](https://atendimento.movidesk.com/kb/pt-br/article/39513/api-do-movidesk-cadastro-de-contrato-de-horas)
Parâmetros: `name` (um contrato) **ou** OData, com `$select` **obrigatório** na lista.
Campos: `id, name, emailSubject, emailDescription, emailAccount, isActive, differentiateHoursFranchise, differentiateHoursConsumption, accumulateUnusedHours, renewalDay, contractedHours, consumptionDeadline, emailSendDay, baseAmount, createdOn, disabledDate, typeActivities*, timeTypeConsumption*, emailDestinations*, clients*`.

| Coleção | Campos |
|---|---|
| `typeActivities[]` | `id, workingTimeType, activity, franchise, value, valueExceededHour, shootdownContract, allowHoursExcedent, activityId` |
| `timeTypeConsumption[]` | `id, workingTimeType, value, workingTimeAgreementId, typeActivities` |
| `emailDestinations[]` | `type, email` |
| `clients[]` | `id, businessName, email, phone, personType, profileType, isDeleted, organization` |

### 2.9 `timeAgreementConsumption` — consumo de horas do contrato
Doc: [Contrato de horas - Consumo de horas](https://atendimento.movidesk.com/kb/pt-br/article/38447/api-do-movidesk-contrato-de-horas-consumo-de-horas)
Parâmetros: `name` (**obrigatório**; sem ele a API pede `$select`), `startPeriod`, `endPeriod` (ex. `2019-10-01T00:00:00`), `$filter`. **Se `$select` for enviado, os filtros de período são ignorados.**
Campos: `id (0 = período aberto), name, startPeriod, endPeriod, baseAmount, exceededHourAmount, renewalDay, contractedHours, differentiateHoursFranchise, differentiateHoursConsumption, period, consumedHours (só em período fechado), discount, discountType (0 %, 1 moeda), timeAppointments[], expenses[], clients[]`.
`timeAppointments[]`: `id, activity, date, periodStart, periodEnd, workTime, workTypeName, createdBy, createdByTeam, ticketNumber, actionNumber`. `expenses[]`: `id, type, serviceReport, createdBy, createdByTeam, date, quantity, value`.

### 2.10 `survey/questions` e `survey/questions/{id}` — perguntas da pesquisa de satisfação
Doc: [Pesquisa de satisfação - Perguntas](https://atendimento.movidesk.com/kb/pt-br/article/75373/api-pesquisa-de-satisfacao-perguntas)
Parâmetros: `type` (opcional). Campos: `id, isActive, type (1 satisfeito/insatisfeito, 2 carinhas, 3 NPS, 4 sim/não), languages[] {cultureId, description}`.

### 2.11 `survey/responses` — respostas da pesquisa de satisfação
Doc: [Pesquisa de satisfação - Respostas](https://atendimento.movidesk.com/kb/pt-br/article/75374/api-pesquisa-de-satisfacao-respostas)
Parâmetros: `type`, `clientId`, `ticketId`, `responseDateGreaterThan`, `responseDateLessThan` (ex. `2019-05-13`), `limit` (1–100), `startingAfter`.
Lista: `{hasMore, items[]}`. Campos: `id, questionId, clientId, type, ticketId, responseDate, commentary, value` (NPS 0–10; sim/não: 1 sim, 2 não).

### 2.12 `article/{id}` — artigo da base de conhecimento
Doc: [Consulta de Artigos da base de conhecimento](https://atendimento.movidesk.com/kb/pt-br/article/382259/api-consulta-de-artigos-da-base-de-conhecimento)
Campos: `id, articleStatus (1 publicado, 2 suspenso), attachments[] {id, name, hash}, categories[] {id, name}, contentHtml, contentText, createdDate, updatedDate, menu {id, name}, relateds[] {id, title, path}, revisionId, services[] {id, name}, slug, summary, tags[], title, readingTime`.

### 2.13 `kb/article` — busca de artigos da base de conhecimento
Mesma doc. Parâmetros: `q` (título, resumo ou conteúdo), `title`, `updatedDateFrom`, `updatedDateTo`, `createdDateFrom`, `createdDateTo`, `status` (1 publicado, 4 suspenso), `tags`, `category`, `menu`, `page` (começa em 0), `pageSize` (padrão 30).
Retorno: `{pageSize, totalSize, items[] {id, title, summary, updatedAt, createdAt, status, tags, menu, categories}}`.
A doc mostra a URL de exemplo como `/kb/article` e, na tabela, como `/article/`. **Testado em 09/10/2026: a rota correta é `/kb/article`**; `/article` sem id devolve 404 de rota.

### 2.14 `storage/download` — download de anexo
Doc: [API de anexos](https://atendimento.movidesk.com/kb/pt-br/article/518585/api-de-anexos)
Parâmetros: `id` = hash do arquivo (`actions[].attachments[].path` dos tickets, `attachments[].hash` dos artigos). Retorna o arquivo binário com o `Content-Type` dele; 404 se o hash não existe.

---

## 3. Mapeamento usado pelo código

<!-- ENDPOINTS-JSON:INICIO -->
```json
{
  "base_url": "https://api.movidesk.com/public/v1",
  "limites": {
    "requisicoes_por_minuto": 10,
    "janela_limitada_brasilia": "07:01-18:59",
    "janela_irrestrita_brasilia": "19:00-07:00",
    "bloqueio_por_erros": "3 erros seguidos => 429 'Too many failed requests' por 60s; depois 120s; depois 300s. Header retry-after informa o tempo.",
    "odata_suportado": ["$filter", "$orderby", "$top", "$skip", "$select", "$expand"],
    "odata_nao_suportado": ["$count", "$inlinecount"],
    "tamanho_pagina_odata": "não documentado; exemplos oficiais usam $top=100",
    "replicacao": "registros novos/alterados podem levar alguns minutos para aparecer"
  },
  "dicas_odata": [
    "Datas em UTC: $filter=createdDate ge 2024-01-01T00:00:00.00z and createdDate le 2024-01-31T23:59:59.00z",
    "Enums com o nome do tipo: origin eq Movidesk.Core.Data.Enums.TicketOrigin'9' ; profileType eq Movidesk.Core.Data.Enums.ProfileType'3'",
    "Lambda em coleções: clients/any(c: c/id eq '123') ; statusHistories/any(s: s/status eq 'Resolvido') ; not emails/any()",
    "Expand aninhado: customFieldValues($expand=items) ; actions($select=id,origin;$expand=timeAppointments($expand=createdBy))",
    "Strings com aspas simples: baseStatus eq 'Resolved' ; ownerTeam eq 'Suporte'"
  ],
  "inexistentes_na_api": ["tickets/past/htmldescription"],
  "bloqueados": [
    "asterisk_startTransferedCall", "asterisk_completedCall", "asterisk_startCanceledCall",
    "asterisk_receivedCall", "asterisk_transferedCall", "asterisk_lostCall", "asterisk_canceledCall",
    "setMadeCallLink", "ticketFileUpload", "addTeamsToActivity", "persons/relationships",
    "ticketCustomFieldValue/InsertValues", "ticketCustomFieldValue/UpdateValues", "ticketCustomFieldValue/DeleteValues"
  ],
  "endpoints": [
    {
      "path": "tickets",
      "descricao": "Tickets com lastUpdate nos últimos 90 dias. Com id/protocol devolve um ticket completo (todas as coleções); sem id devolve lista e exige $select.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/256/movidesk-ticket-api",
      "odata": true,
      "paginacao": "odata",
      "parametro_unitario": ["id", "protocol"],
      "select_obrigatorio_em_lista": true,
      "parametros": {
        "id": "Número do ticket (retorna um objeto)",
        "protocol": "Protocolo do ticket (retorna um objeto)",
        "includeDeletedItems": "true para incluir ações, clientes, pais e filhos excluídos (padrão false)",
        "$select": "OBRIGATÓRIO em listas. Ex.: id,subject,status,createdDate",
        "$filter": "Ex.: createdDate ge 2024-01-01T00:00:00.00z and baseStatus eq 'Resolved'",
        "$orderby": "Ex.: id desc",
        "$top": "Itens por página (exemplos usam 100)",
        "$skip": "Itens a pular",
        "$expand": "Coleções/objetos: veja 'expansoes'"
      },
      "campos": ["id", "protocol", "type", "subject", "category", "urgency", "status", "baseStatus", "justification", "origin", "createdDate", "originEmailAccount", "owner", "ownerTeam", "createdBy", "serviceFull", "serviceFirstLevelId", "serviceFirstLevel", "serviceSecondLevel", "serviceThirdLevel", "contactForm", "tags", "cc", "resolvedIn", "reopenedIn", "closedIn", "lastActionDate", "actionCount", "lastUpdate", "lifetimeWorkingTime", "stoppedTime", "stoppedTimeWorkingTime", "resolvedInFirstCall", "chatWidget", "chatGroup", "chatTalkTime", "chatWaitingTime", "sequence", "slaAgreement", "slaAgreementRule", "slaSolutionTime", "slaResponseTime", "slaSolutionChangedByUser", "slaSolutionChangedBy", "slaSolutionDate", "slaSolutionDateIsPaused", "slaResponseDate", "slaRealResponseDate", "jiraIssueKey", "redmineIssueId", "clients", "actions", "parentTickets", "childrenTickets", "ownerHistories", "statusHistories", "customFieldValues", "assets"],
      "expansoes": {
        "owner": ["id", "businessName", "email", "phone", "personType", "profileType"],
        "createdBy": ["id", "businessName", "email", "phone", "personType", "profileType"],
        "slaSolutionChangedBy": ["id", "businessName", "email", "phone", "personType", "profileType"],
        "clients": ["id", "businessName", "email", "phone", "personType", "profileType", "isDeleted", "organization"],
        "clients($expand=organization)": ["id", "businessName", "email", "phone", "personType", "profileType"],
        "actions": ["id", "type", "origin", "description", "status", "justification", "createdDate", "createdBy", "isDeleted", "timeAppointments", "expenses", "attachments", "tags"],
        "actions($expand=createdBy)": ["id", "businessName", "email", "phone", "personType", "profileType"],
        "actions($expand=timeAppointments($expand=createdBy,createdByTeam))": ["id", "activity", "date", "periodStart", "periodEnd", "workTime", "accountedTime", "workTypeName", "createdBy", "createdByTeam"],
        "actions($expand=expenses)": ["id", "type", "serviceReport", "createdBy", "createdByTeam", "date", "quantity", "value"],
        "actions($expand=attachments)": ["fileName", "path", "createdBy", "createdDate"],
        "parentTickets": ["id", "subject", "isDeleted"],
        "childrenTickets": ["id", "subject", "isDeleted"],
        "ownerHistories": ["ownerTeam", "owner", "permanencyTimeFullTime", "permanencyTimeWorkingTime", "changedBy", "changedDate"],
        "statusHistories": ["status", "justification", "permanencyTimeFullTime", "permanencyTimeWorkingTime", "changedBy", "changedDate"],
        "customFieldValues($expand=items)": ["customFieldId", "customFieldRuleId", "line", "value", "items{personId,clientId,team,customFieldItem,storageFileGuid,fileName}"],
        "assets": ["id", "name", "label", "serialNumber", "categoryFull", "categoryFirstLevel", "categorySecondLevel", "categoryThirdLevel", "isDeleted"]
      },
      "teste": {"params": {"$select": "id,subject,lastUpdate", "$top": 1, "$orderby": "lastUpdate desc"}}
    },
    {
      "path": "tickets/past",
      "descricao": "Tickets antigos (D-1 pela data de atualização). Única rota para tickets com lastUpdate acima de 90 dias. Mesmos parâmetros, campos e expansões de 'tickets'.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/446011/movidesk-ticket-api-past",
      "odata": true,
      "paginacao": "odata",
      "parametro_unitario": ["id", "protocol"],
      "select_obrigatorio_em_lista": true,
      "mesmo_layout_de": "tickets",
      "parametros": {
        "id": "Número do ticket",
        "protocol": "Protocolo do ticket",
        "includeDeletedItems": "true para incluir itens excluídos",
        "$select": "OBRIGATÓRIO em listas",
        "$filter": "", "$orderby": "", "$top": "", "$skip": "", "$expand": ""
      },
      "teste": {"params": {"$select": "id,subject,lastUpdate", "$top": 1}}
    },
    {
      "path": "tickets/htmldescription",
      "descricao": "HTML das ações de um ticket, recente ou antigo. A rota tickets/past/htmldescription citada na doc não existe (404 de rota, testado em 09/10/2026).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/256/movidesk-ticket-api",
      "odata": false,
      "paginacao": "nenhuma",
      "obrigatorios_um_de": ["id", "protocol"],
      "parametros": {"id": "Número do ticket", "protocol": "Protocolo do ticket", "actionId": "Id da ação (opcional)"},
      "campos": ["id", "description"],
      "teste": {"depende_de": "tickets", "params": {"id": "<id de ticket>"}}
    },
    {
      "path": "tickets/merged",
      "descricao": "Tickets mesclados: por ticket (id) ou por período (startDate/endDate, paginado por page).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/488900/api-do-movidesk-tickets-merged",
      "odata": false,
      "paginacao": "pagina_merged",
      "lista_em": "mergedTickets",
      "parametros": {
        "id": "Número ou protocolo do ticket (ignora os demais)",
        "startDate": "Data inicial UTC (ex. 2024-05-01)",
        "endDate": "Data final UTC",
        "page": "Página (resposta informa pageNumber 'N of TOTAL')"
      },
      "campos": ["startDate", "endDate", "count", "pageNumber", "mergedTickets{ticketId,mergedTickets,mergedTicketsIds,lastUpdate}"],
      "teste": {"params": {"startDate": "<hoje-30d>"}}
    },
    {
      "path": "persons",
      "descricao": "Pessoas, empresas e departamentos (agentes e clientes).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/189/movidesk-person-api",
      "odata": true,
      "paginacao": "odata",
      "parametro_unitario": ["id"],
      "select_obrigatorio_em_lista": false,
      "parametros": {
        "id": "Cód. ref. da pessoa (retorna um objeto)",
        "$select": "Ex.: id,businessName,profileType,isActive",
        "$filter": "Ex.: profileType eq 2 ; emails/any(e: e/email eq 'x@y.com') ; relationships/any(o: o/name eq 'Empresa')",
        "$orderby": "", "$top": "", "$skip": "",
        "$expand": "addresses, contacts, emails, relationships($expand=services), customFieldValues($expand=items), atAssets"
      },
      "campos": ["id", "codeReferenceAdditional", "isActive", "personType", "profileType", "accessProfile", "corporateName", "businessName", "businessBranch", "cpfCnpj", "userName", "role", "bossId", "bossName", "classification", "cultureId", "timeZoneId", "authenticateOn", "createdDate", "createdBy", "changedDate", "changedBy", "observations", "addresses", "contacts", "emails", "teams", "relationships", "customFieldValues", "atAssets"],
      "expansoes": {
        "addresses": ["addressType", "country", "postalCode", "state", "city", "district", "street", "number", "complement", "reference", "isDefault"],
        "contacts": ["contactType", "contact", "isDefault"],
        "emails": ["emailType", "email", "isDefault"],
        "relationships($expand=services)": ["id", "name", "slaAgreement", "forceChildrenToHaveSomeAgreement", "allowAllServices", "includeInParents", "loadChildOrganizations", "services{id,name,copyToChildren}"],
        "customFieldValues($expand=items)": ["customFieldId", "customFieldRuleId", "line", "value", "items{personId,clientId,team,customFieldItem}"],
        "atAssets": ["id", "name", "label"]
      },
      "teste": {"params": {"$select": "id,businessName,profileType", "$top": 1}}
    },
    {
      "path": "services",
      "descricao": "Catálogo de serviços (árvore via parentServiceId).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/7440/api-servicos",
      "odata": true,
      "paginacao": "odata",
      "parametro_unitario": ["id"],
      "select_obrigatorio_em_lista": false,
      "parametros": {"id": "Id do serviço", "$select": "", "$filter": "Ex.: isActive eq true", "$orderby": "", "$top": "", "$skip": "", "$expand": ""},
      "campos": ["id", "name", "description", "parentServiceId", "serviceForTicketType", "isVisible", "allowSelection", "allowFinishTicket", "isActive", "automationMacro", "defaultCategory", "defaultUrgency", "allowAllCategories", "categories"],
      "teste": {"params": {"$select": "id,name", "$top": 1}}
    },
    {
      "path": "activity",
      "descricao": "Atividades usadas em apontamentos de horas. Lista paginada por cursor.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/55923/api-de-atividades",
      "odata": false,
      "paginacao": "cursor",
      "lista_em": "items",
      "parametro_unitario": ["id"],
      "parametros": {"id": "Id da atividade", "limit": "1 a 100 (padrão 100)", "startingAfter": "Id do último item da página anterior", "name": "Filtra pelo nome"},
      "campos": ["id", "name", "isActive", "isAllowsAllTeams", "teams{name}"],
      "teste": {"params": {"limit": 1}}
    },
    {
      "path": "timeAgreement",
      "descricao": "Contratos de horas. Um contrato por name; lista exige $select.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/39513/api-do-movidesk-cadastro-de-contrato-de-horas",
      "odata": true,
      "paginacao": "odata",
      "parametro_unitario": ["name", "id"],
      "select_obrigatorio_em_lista": true,
      "parametros": {"name": "Nome do contrato (retorna um objeto)", "$select": "OBRIGATÓRIO em listas", "$filter": "Ex.: contractedHours gt 60", "$orderby": "", "$top": "", "$skip": "", "$expand": "typeActivities, timeTypeConsumption, emailDestinations, clients"},
      "campos": ["id", "name", "emailSubject", "emailDescription", "emailAccount", "isActive", "differentiateHoursFranchise", "differentiateHoursConsumption", "accumulateUnusedHours", "renewalDay", "contractedHours", "consumptionDeadline", "emailSendDay", "baseAmount", "createdOn", "disabledDate", "typeActivities", "timeTypeConsumption", "emailDestinations", "clients"],
      "expansoes": {
        "typeActivities": ["id", "workingTimeType", "activity", "franchise", "value", "valueExceededHour", "shootdownContract", "allowHoursExcedent", "activityId"],
        "timeTypeConsumption": ["id", "workingTimeType", "value", "workingTimeAgreementId", "typeActivities"],
        "emailDestinations": ["type", "email"],
        "clients": ["id", "businessName", "email", "phone", "personType", "profileType", "isDeleted", "organization"]
      },
      "teste": {"params": {"$select": "id,name", "$top": 1}}
    },
    {
      "path": "timeAgreementConsumption",
      "descricao": "Consumo de horas de um contrato por período. Exige name; se $select for enviado, o período é ignorado.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/38447/api-do-movidesk-contrato-de-horas-consumo-de-horas",
      "odata": false,
      "paginacao": "nenhuma",
      "obrigatorios": ["name"],
      "parametros": {"name": "OBRIGATÓRIO: nome do contrato", "startPeriod": "Ex.: 2024-01-01T00:00:00", "endPeriod": "Ex.: 2024-01-31T00:00:00", "$filter": "Filtro de período"},
      "campos": ["id", "name", "startPeriod", "endPeriod", "baseAmount", "exceededHourAmount", "renewalDay", "contractedHours", "differentiateHoursFranchise", "differentiateHoursConsumption", "period", "consumedHours", "discount", "discountType", "timeAppointments", "expenses", "clients"],
      "expansoes": {
        "timeAppointments": ["id", "activity", "date", "periodStart", "periodEnd", "workTime", "workTypeName", "createdBy", "createdByTeam", "ticketNumber", "actionNumber"],
        "expenses": ["id", "type", "serviceReport", "createdBy", "createdByTeam", "date", "quantity", "value"],
        "clients": ["id", "businessName", "email", "phone", "personType", "profileType", "isDeleted", "organization"]
      },
      "teste": {"depende_de": "timeAgreement", "params": {"name": "<nome de contrato>"}}
    },
    {
      "path": "survey/questions",
      "descricao": "Perguntas da pesquisa de satisfação.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/75373/api-pesquisa-de-satisfacao-perguntas",
      "odata": false,
      "paginacao": "nenhuma",
      "parametros": {"type": "1 satisfeito/insatisfeito, 2 carinhas, 3 NPS, 4 sim/não"},
      "campos": ["id", "isActive", "type", "languages{cultureId,description}"],
      "teste": {"params": {}}
    },
    {
      "path": "survey/questions/{id}",
      "descricao": "Uma pergunta da pesquisa de satisfação.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/75373/api-pesquisa-de-satisfacao-perguntas",
      "odata": false,
      "paginacao": "nenhuma",
      "parametros": {},
      "campos": ["id", "isActive", "type", "languages{cultureId,description}"],
      "teste": {"depende_de": "survey/questions"}
    },
    {
      "path": "survey/responses",
      "descricao": "Respostas da pesquisa de satisfação (substitui ticket.satisfactionSurveyResponses). Paginada por cursor.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/75374/api-pesquisa-de-satisfacao-respostas",
      "odata": false,
      "paginacao": "cursor",
      "lista_em": "items",
      "parametros": {"type": "1 a 4", "clientId": "Id do cliente", "ticketId": "Número do ticket", "responseDateGreaterThan": "Ex.: 2024-01-01", "responseDateLessThan": "Ex.: 2024-01-31", "limit": "1 a 100 (padrão 100)", "startingAfter": "Id do último item da página anterior"},
      "campos": ["id", "questionId", "clientId", "type", "ticketId", "responseDate", "commentary", "value"],
      "teste": {"params": {"limit": 1}}
    },
    {
      "path": "kb/article",
      "descricao": "Busca de artigos da base de conhecimento (listagem simplificada, paginada por page/pageSize).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/382259/api-consulta-de-artigos-da-base-de-conhecimento",
      "odata": false,
      "paginacao": "pagina",
      "lista_em": "items",
      "parametros": {"q": "Texto em título, resumo ou conteúdo", "title": "Texto só no título", "updatedDateFrom": "", "updatedDateTo": "", "createdDateFrom": "", "createdDateTo": "", "status": "1 publicado, 4 suspenso", "tags": "Tag", "category": "Categoria", "menu": "Menu", "page": "Página, começa em 0", "pageSize": "Itens por página (padrão 30)"},
      "campos": ["pageSize", "totalSize", "items{id,title,summary,updatedAt,createdAt,status,tags,menu,categories}"],
      "teste": {"params": {"pageSize": 1}}
    },
    {
      "path": "article/{id}",
      "descricao": "Um artigo completo da base de conhecimento (HTML, texto, anexos, categorias, relacionados).",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/382259/api-consulta-de-artigos-da-base-de-conhecimento",
      "odata": false,
      "paginacao": "nenhuma",
      "parametros": {},
      "campos": ["id", "articleStatus", "attachments{id,name,hash}", "categories{id,name}", "contentHtml", "contentText", "createdDate", "updatedDate", "menu{id,name}", "relateds{id,title,path}", "revisionId", "services{id,name}", "slug", "summary", "tags", "title", "readingTime"],
      "teste": {"depende_de": "kb/article"}
    },
    {
      "path": "storage/download",
      "descricao": "Download de anexo pelo hash (tickets: actions[].attachments[].path; artigos: attachments[].hash). Retorna binário.",
      "doc": "https://atendimento.movidesk.com/kb/pt-br/article/518585/api-de-anexos",
      "odata": false,
      "paginacao": "nenhuma",
      "obrigatorios": ["id"],
      "resposta_binaria": true,
      "parametros": {"id": "Hash do arquivo"},
      "campos": [],
      "teste": {"depende_de": "anexo de ticket"}
    }
  ]
}
```
<!-- ENDPOINTS-JSON:FIM -->
