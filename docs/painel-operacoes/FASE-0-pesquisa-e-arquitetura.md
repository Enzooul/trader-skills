# Painel de Operações — Fase 0: pesquisa e arquitetura

> Documento de decisão, escrito antes de qualquer código.
> Data da pesquisa: 01/11/2026. As fontes oficiais estão no fim, com o que foi verificado e o que **não** foi.
> Este repositório é público: aqui não entram saldo, número de conta, operações reais nem tokens.

---

## 1. Resumo em uma página

**O que você tem:** conta individual IBKR Pro, ações dos EUA, day trade só na compra (long), conta real, moeda da conta USD.

**O que a IBKR oferece para um sistema como o seu (conta individual):**

| Caminho | Tipo | Tempo real? | Precisa de algo rodando? | Só leitura? | Papel no projeto |
|---|---|---|---|---|---|
| **Flex Web Service** (relatórios Flex) | HTTP simples, 2 endpoints, resposta em XML | Não. "Trade Confirmation" atualiza ao longo do dia; "Activity" é o fechamento | **Não**. Só um token, gerado no Client Portal | **Sim, por natureza** (só gera relatórios) | **Coluna vertebral do histórico** e da conferência com o extrato |
| **Client Portal Web API + Client Portal Gateway** | REST + WebSocket | Sim (WebSocket de ordens, execuções e P&L) | **Sim**: programa Java rodando num computador seu ou servidor, com login manual + 2FA | **Não existe chave só-leitura** documentada para conta individual (ver §3) | Painel ao vivo (Fase 2) |
| **TWS API / IB Gateway** | Socket TCP + biblioteca (Python etc.) | Sim | Sim: TWS ou IB Gateway aberto | Tem o modo "Read-Only API", **mas nele as informações de ordens não chegam à API** | Alternativa ao Gateway, com a ressalva acima |
| **Exportação manual** (CSV/XML do Client Portal) | Arquivo | Não | Não | Sim | Reserva universal (Fase 1) |
| **Conector IBKR do Claude** (o que as skills `trader-*` usam) | Ferramenta do Claude | — | — | Tem ferramentas de ordem | **Não serve para o site**: só o Claude chama, o site não. Pode servir para o resumo com IA (Fase 4) |

**Recomendação:** começar pelo **Flex Web Service** (histórico automático, oficial, sem programa rodando, só leitura), deixar a **importação de arquivo** como reserva desde o MVP e adicionar o **Gateway** só para o painel ao vivo na Fase 2, rodando no seu computador durante o pregão.

---

## 2. Respostas às perguntas da Fase 0

### a) Existe API? De qual tipo?

Sim, quatro portas de entrada (tabela acima). Para conta **individual**, a documentação oficial diz que a autenticação da Web API é feita pelo **Client Portal Gateway**, "um pequeno programa Java que roteia requisições locais". O OAuth 1.0a (que dispensaria o Gateway) aparece na documentação oficial só para advisors, brokers, fundos e desenvolvedores terceiros. Há relatos de comunidade (biblioteca `ibind`) de contas individuais que conseguiram se registrar no OAuth 1.0a pelo portal de autoatendimento, **mas não achei confirmação oficial**. Não vou basear o projeto nisso; fica como item a testar.

Requisito comum: a Web API só funciona com conta **IBKR Pro** aberta e com saldo.

### b) Dá para ter tempo real? Com que atraso e limites?

**Gateway / Web API — limites oficiais:**

- Limite global: **10 requisições por segundo**. Passou disso: erro 429, e o IP fica **15 minutos de castigo**; reincidência pode bloquear o IP de vez.
- Limites por endpoint relevantes para nós:

| Endpoint | Para quê | Limite |
|---|---|---|
| `/iserver/account/orders` | ordens abertas | 1 a cada 5 s |
| `/iserver/account/trades` | execuções (hoje + 6 dias anteriores) | 1 a cada 5 s; a doc recomenda chamar **uma vez por sessão** |
| `/iserver/account/pnl/partitioned` | P&L do dia | 1 a cada 5 s |
| `/portfolio/accounts` | contas | 1 a cada 5 s |
| `/iserver/marketdata/snapshot` | cotação | 10 por segundo |
| `/iserver/marketdata/history` | barras (para MAE/MFE) | 10 por segundo ou 50 por minuto |
| `/tickle` | manter a sessão viva | 1 por segundo |

- **WebSocket** (`wss://localhost:5000/v1/api/ws`): é o jeito certo de ter "ao vivo" sem estourar limites. Tópicos úteis:
  - `sor` (atualização de ordens) e `str` (execuções) — exigem sessão de corretagem ativa;
  - `spl` (P&L), `ssd` (resumo da conta), `sld` (razão/ledger) — não exigem sessão de corretagem;
  - `sts` avisa quando a autenticação cai (alimenta o status "Desconectado").
- **Não achei limite específico documentado para o WebSocket.** Um podcast da própria IBKR diz que ele segue "as mesmas restrições" da API; vamos medir na prática.

**Flex Web Service:**

- `SendRequest` (pede o relatório): **1 por segundo, no máximo 10 por minuto**.
- O relatório "Trade Confirmation" **atualiza ao longo do dia** conforme as execuções ocorrem, e a própria IBKR recomenda puxá-lo "intermitentemente ao longo do dia". **Não achei o atraso exato** entre a execução e ela aparecer no Flex; vamos medir nas primeiras semanas e mostrar esse atraso na tela.
- Aceita período (`p`, em dias) ou intervalo de datas (`fd` e `td`). **Não confirmei o tamanho máximo do intervalo** por requisição; vamos testar ao puxar seu histórico.

**Cotação ao vivo (para P&L não realizado):** depende da sua assinatura de dados. Hoje (domingo) o conector devolveu o status `FROZEN` para a AAPL, que costuma indicar assinatura em tempo real com o mercado fechado — **confirmar num dia de pregão**. Requisitos oficiais para dados via API: conta Pro, **mínimo de US$ 500 de patrimônio além do custo das assinaturas**, assinatura feita no Client Portal, **"Market Data API Acknowledgement" marcado** e questionários de conformidade preenchidos. Se o patrimônio ficar abaixo do mínimo, a IBKR pode encerrar as assinaturas de dados. Isso pesa no seu caso (ver §6).

### c) Precisa de algo rodando no computador ou num servidor?

- **Flex:** não. Uma função agendada no servidor (Vercel Cron ou Supabase) chama a IBKR com o token.
- **Gateway:** sim, e com três atritos documentados:
  1. **Login manual** com usuário, senha e 2FA no navegador (`https://localhost:5000`). Métodos de 2FA aceitos na API: IB Key, app de celular, SMS e cartão DSC+. Cartão de códigos "bingo" **não** funciona.
  2. A sessão dura **no máximo 24 horas**, reinicia à **meia-noite** (Nova York, Zug ou Hong Kong, conforme o servidor), e a manutenção diária da IBKR pode derrubá-la antes.
  3. Cai por inatividade em **cerca de 6 minutos** se o programa não chamar `/tickle`. A IBKR recomenda chamar uma vez por minuto. Se cair, `/iserver/auth/ssodh/init` reabre a sessão de corretagem sem novo login, enquanto a conexão de fundo existir.
- Na prática: **um login por dia de pregão** no computador que roda o Gateway. Existem ferramentas da comunidade (ex.: IBeam) que automatizam o login guardando usuário e senha; **não recomendo** para conta real, porque guardar a senha da corretora num servidor é o maior risco de segurança do projeto inteiro.

### d) Custos

| Item | Custo | Certeza |
|---|---|---|
| Flex Web Service | sem cobrança que eu tenha encontrado na doc | média: não achei menção a preço |
| Client Portal Gateway / Web API | software gratuito | alta |
| Dados de mercado em tempo real | assinatura mensal na IBKR; **não sei o preço atual** e não vou chutar. Ver em Client Portal → Configurações → Assinaturas de dados | — |
| Mínimo para manter dados via API | US$ 500 de patrimônio + custo da assinatura | alta (doc oficial) |
| Supabase e Vercel | ambos têm plano gratuito; **não vou citar limites e preços de memória**, conferimos nas páginas oficiais antes de criar | — |
| Servidor (VPS) para rodar o Gateway 24h | só se você optar por isso; preço varia por provedor | — |
| API de IA (resumo diário, Fase 4) | por uso; estimamos na Fase 4 | — |

---

## 3. A questão do "somente leitura"

Seu princípio nº 1 é inegociável, e a IBKR não facilita para conta individual. O que a documentação mostra:

- **Flex:** só leitura por natureza. O token pode ter validade de **6 horas a 1 ano** e pode ser **restrito a um IP**. Gerar um token novo invalida o anterior.
- **Client Portal Gateway:** a sessão é a mesma do seu usuário; **não existe chave só-leitura** documentada para conta individual. Encontrei duas proteções oficiais:
  - **Restrição de IP por usuário** (Client Portal → Settings → IP Restrictions), vale para todos os endpoints `/iserver`, leva 1–2 dias úteis para valer;
  - **Pre-Trade Compliance (PTC)**, que pode bloquear ordens por usuário. Bloquear o seu próprio usuário bloquearia também as suas ordens manuais, então só faria sentido com um **segundo usuário** dedicado ao painel. **Não confirmei** se conta individual pode criar um segundo usuário com essa restrição; é item a verificar com a IBKR.
- **TWS / IB Gateway:** o modo "Read-Only API" vem ligado por padrão e bloqueia ordens, **mas a doc oficial diz que nele as informações de ordens não ficam disponíveis para a API**. Ou seja: com ele ligado, a página "Ordens abertas" não funcionaria.

**Defesa que vamos construir de qualquer jeito (em camadas):**

1. O coletor só conhece uma **lista fechada de endpoints de leitura** (allow-list). Qualquer outro caminho, e qualquer método que não seja GET (com as exceções explícitas `/tickle` e `/iserver/auth/ssodh/init`), é recusado no código **antes** de sair da máquina, com teste automatizado provando isso.
2. O Gateway escuta só em `localhost`; o site nunca fala com ele, só o coletor.
3. O site e o banco **não têm** nenhuma função de ordem. Não existe botão, rota nem tabela para isso.
4. Log de auditoria de toda chamada feita à IBKR.

---

## 4. Arquitetura proposta

```
                    ┌───────────────────────── IBKR ─────────────────────────┐
                    │  Flex Web Service (relatórios)   Web API via Gateway    │
                    └──────────┬──────────────────────────────┬──────────────┘
                               │ token (servidor)             │ localhost:5000
                               │                              │ (seu computador, só no pregão)
              ┌────────────────▼──────────┐      ┌────────────▼──────────────┐
              │ Funções agendadas         │      │ Coletor Python (Fase 2)   │
              │ (Vercel Cron / Supabase)  │      │ allow-list de leitura,    │
              │ Flex: dia + confirmações  │      │ WebSocket sor/str/spl/sts │
              └────────────────┬──────────┘      └────────────┬──────────────┘
                               │ dado bruto + normalizado     │
                    ┌──────────▼──────────────────────────────▼──────────────┐
                    │ Supabase (Postgres + Auth com 2FA + RLS + Realtime)     │
                    │ raw_imports → fills → trades → métricas → diagnóstico   │
                    └──────────┬─────────────────────────────────────────────┘
                               │ Realtime / consultas
                    ┌──────────▼──────────┐   Também alimentam:
                    │ Site Next.js        │   • Importação CSV/XML (Fase 1)
                    │ (Vercel), celular   │   • Lançamento manual (Fase 1)
                    └─────────────────────┘
```

### 4.1 Decisões de engenharia (e por quê)

1. **Dado bruto primeiro.** Toda resposta da IBKR (XML do Flex, JSON do Gateway, arquivo importado) é guardada como veio em `raw_imports`, com hash, origem e horário. Tudo o que aparece na tela é **derivado** disso e pode ser recalculado. É o que garante a rastreabilidade (princípio 3) e permite corrigir um bug de cálculo sem perder nada.
2. **Uma única implementação dos cálculos**, em TypeScript, num pacote isolado (`packages/core`) com testes (Vitest). O coletor Python só transporta dado bruto; ele não calcula P&L. Assim não existem duas fórmulas que possam discordar.
3. **Aritmética decimal**, nunca `float`: `numeric` no Postgres e `decimal.js` no TypeScript. Isso é o que permite bater com o extrato "no centavo".
4. **Deduplicação pela chave da corretora**: `execution_id` / `tradeID` da IBKR é único. Importar o mesmo arquivo duas vezes não duplica nada.
5. **Conferência contra o número da própria IBKR.** A IBKR já informa o P&L realizado de cada execução, já com comissão (o conector mostrou isso; o Flex tem o campo equivalente). O sistema calcula o seu próprio P&L **e** compara com o da IBKR em cada fechamento. Qualquer diferença acima de US$ 0,01 vira alerta de reconciliação. Esse é o critério de aceite automatizado de cada fase.
6. **Dia de pregão = dia de Nova York**, exibido no seu fuso (America/Bahia, UTC−3). Atenção ao horário de verão americano: com Nova York em EDT a abertura (9h30) é **10h30 na Bahia**; em EST é **11h30**. O horário de verão americano termina hoje, 01/11/2026, então a partir de amanhã a abertura é às 11h30 na Bahia. O sistema converte pelo banco de fusos (IANA), nunca por um número fixo.
7. **Moeda:** a conta é em USD, então os números nascem em USD. A tela mostra **US$ 1.234,56** (formato brasileiro, moeda americana). Conversão para R$ é opcional e sempre rotulada com a cotação usada e a data (para impostos, a fonte natural é a PTAX do Banco Central; confirmamos na Fase 4).

### 4.2 Como uma "operação" é montada

- **Execução (fill):** cada pedaço executado. Uma ordem pode virar várias execuções em mercados diferentes (ex.: parte na DARK, parte na IBKRATS, mesmo `order_id`).
- **Operação (trade):** do momento em que a posição sai de zero até voltar a zero, por ativo. Parciais de entrada e saída entram na mesma operação.
- **Preço médio:** configurável (médio ou FIFO). Como você opera só na compra e zera no dia, os dois devem dar o mesmo resultado na maioria dos casos; o motor suporta venda a descoberto e virada de mão mesmo assim (pedido seu), com testes, mesmo que `allow_short` esteja desligado hoje.
- **R-múltiplo** precisa do **stop planejado na entrada**. Fontes, em ordem: a ordem de stop enviada junto (Gateway, Fase 2), o lançamento manual, ou "indisponível". Nunca estimado sem rótulo.
- **MAE/MFE** (pior e melhor momento da operação) precisam de barras intradiárias. Na Fase 2 o Gateway fornece (`/iserver/marketdata/history`); antes disso, "indisponível".
- **Slippage** só com preço planejado registrado (lançamento manual ou ordem limite).

### 4.3 Banco de dados (tabelas)

`accounts`, `instruments` (multiplicador, tick, moeda e custos **por ativo**, nada fixo no código), `raw_imports`, `orders`, `fills`, `trades`, `trade_fills` (ligação), `position_snapshots`, `equity_snapshots` (intradiário e fechamento), `cash_movements` (aportes e saques: **nunca contam como lucro**; a curva de capital e o drawdown são calculados descontando-os), `fees` (comissão, taxas regulatórias, juros), `setups` e `trade_tags`, `journal_entries`, `risk_rules` e `risk_violations`, `alerts` e `alert_deliveries`, `diagnostic_reports` e `diagnostic_findings`, `sync_log`, `audit_log`.

Todas com RLS (Row Level Security: o próprio banco recusa linhas que não sejam do usuário logado).

### 4.4 Rotinas automáticas

| Quando | O quê | Fonte |
|---|---|---|
| Pregão, a cada N minutos (configurável, respeitando 10/min) | Puxa "Trade Confirmation" e atualiza execuções e operações | Flex |
| Pregão, contínuo | Ordens, execuções e P&L ao vivo (WebSocket) | Gateway (Fase 2) |
| Após o fechamento de Nova York | Puxa o relatório "Activity" do dia: trades, comissões, caixa, posições, patrimônio | Flex |
| Após o "Activity" | Snapshot diário, recalcula métricas, gera relatório diário | Banco |
| Após o "Activity" | **Reconciliação**: posição e P&L calculados × informados pela IBKR; divergência vira alerta | Banco |
| Todo dia | Backup do banco para um local fora do Supabase | Script agendado |

### 4.5 Stack

A sugerida no seu pedido é adequada e eu a manteria: **Next.js + TypeScript + Tailwind + shadcn/ui**, gráficos com **Recharts** (curva de capital, drawdown, calendário) e **TradingView Lightweight Charts** (gráfico da operação com entrada e saída), **Supabase** (Postgres, Auth com 2FA por TOTP, RLS, Realtime), **Vercel**, coletor em **Python** na Fase 2. A sessão tem conectores do Supabase e da Vercel, o que permite criar e configurar os dois daqui, com a sua autorização a cada passo.

**Repositório do código: precisa ser PRIVADO e separado.** Este `trader-skills` é público. Não posso criar repositórios daqui; você cria um repositório privado no GitHub (ex.: `trading-ops`) e eu o adiciono à sessão.

---

## 5. Plano de fases (ajustado ao que a IBKR permite)

| Fase | Entrega | Fonte de dados | Funciona sem Gateway? |
|---|---|---|---|
| **1 — MVP** | Importação de arquivo (Flex XML/CSV, com mapeamento de colunas e deduplicação) + lançamento manual + histórico + métricas + página de Resultados. Tela marca dados de exemplo como **FICTÍCIOS**. | Arquivo + manual | Sim |
| **1.5 — Flex automático** | O mesmo de cima, mas puxado sozinho todo dia e durante o pregão; reconciliação contra o P&L da IBKR | Flex Web Service | Sim |
| **2 — Ao vivo** | Coletor Python + Gateway: ordens abertas, posições, P&L ao vivo, status "Ao vivo / Atrasado / Desconectado", MAE/MFE | Gateway | Não |
| **3 — Diagnóstico** | Motor de regras, achados com evidência clicável, simulação "e se" rotulada, alertas (navegador, e-mail, Telegram), relatório diário | Banco | Sim |
| **4 — Diário, impostos, IA** | Diário, apuração configurável (com aviso de que não substitui contador), resumo diário com IA que só interpreta números já calculados | Banco | Sim |

**Por que a fase 1.5:** o Flex entrega quase tudo que a Fase 1 precisa, de forma automática e oficial, sem programa rodando. Ele resolve o "registrar automaticamente o histórico" já no começo; o Gateway fica só para o que é realmente ao vivo.

---

## 6. Observações sobre o seu caso (sem dados da conta)

Estes pontos não mudam a arquitetura, mas o diagnóstico vai precisar tratá-los:

1. **Custos fixos por ordem pesam muito em conta pequena.** As comissões observadas sugerem uma tarifa mínima por ordem por volta de US$ 1 (isto é uma **inferência** a partir das execuções, não confirmei o seu plano de tarifas). Com posições de poucas centenas de dólares, uma ida e volta custa cerca de US$ 2, o que pode transformar uma operação vencedora no bruto em perdedora no líquido. A métrica "custos como % do resultado bruto" vai ser uma das mais importantes para você.
2. **Mínimo de US$ 500 para dados via API.** Se o patrimônio estiver abaixo disso, a cotação ao vivo via Gateway pode não estar disponível ou ser cortada. Nesse caso o painel ao vivo mostra P&L não realizado como "indisponível" ou "atrasado", nunca inventado.
3. **Amostra pequena:** com poucas operações por mês, quase todo recorte vai mostrar "amostra pequena — conclusão pouco confiável" por um bom tempo. Isso é o comportamento correto.
4. **Seus limites atuais** (`trader/config.json`): perda máxima por operação 7,5% do saldo, perda máxima no dia 22,5%, até 3 operações por dia, stop obrigatório, sem entradas antes das 9h45 nem depois das 16h00 (Nova York), zerar até 15h55. O módulo de risco importa essas regras desse arquivo para não haver duas versões delas.
5. **Regra de day trade americana (PDT) e liquidação:** se a conta for de margem com menos de US$ 25 mil, a regra de "pattern day trader" limita day trades; se for conta caixa, a preocupação passa a ser usar dinheiro ainda não liquidado. **Não sei** se houve mudança recente nessa regra da FINRA e não sei o tipo da sua conta; vamos confirmar antes de criar o alerta.

---

## 7. Perguntas abertas (respostas mudam o projeto)

Respondidas em 01/11/2026:

| # | Pergunta | Resposta | Consequência no projeto |
|---|---|---|---|
| 1 | Onde opera no pregão | **Celular** | Gateway no computador pessoal não serve. Ao vivo passa a depender de servidor (VPS) com login diário pelo celular, ou do OAuth 1.0a se a IBKR liberar. Até lá: Flex intradiário, com o atraso exibido. Decisão na Fase 2 |
| 2 | Repositório privado | Criado: `trading-ops` (privado) | Código do site vai para lá; este repositório só guarda documentação |
| 3 | Supabase e Vercel | Pode criar tudo que for necessário | Projeto novo em cada um, plano gratuito |
| 4 | Setups | Confirmados os das skills (ex.: ORB) | Lista inicial editável em Configurações |
| 5 | Meta mensal | **30% ao mês** | Barra de progresso da meta; o diagnóstico mostra a meta ao lado da expectância medida |
| 6 | Moeda | US$ com conversão em R$ ao lado | R$ sempre rotulado com a cotação e a data usadas |
| 7 | Histórico | Só 2 dias de operação | Importação inicial pequena; amostra pequena por bastante tempo |
| 8 | Conta caixa ou margem | Não sabe | Verificar no Client Portal (Configurações → Tipo de conta). O alerta de PDT ou de liquidação depende disso |
| 9 | Alertas | Navegador e e-mail | Telegram fica fora por enquanto |
| 10 | Residência fiscal | Brasil | Apuração da Fase 4 segue as regras brasileiras para aplicações no exterior, com alíquotas configuráveis e aviso de que não substitui contador |

Pendência do usuário: ativar o Flex Web Service (não encontrou a opção; ver o passo a passo na sessão).

---

## 8. Fontes consultadas

Verificadas na documentação oficial da IBKR (páginas em `interactivebrokers.com/docs`, lidas em 01/11/2026):

- Web API — Introdução (contas Pro, Gateway, OAuth): https://www.interactivebrokers.com/docs/web-api/v1/introduction
- Autenticação — tipos de conta por método: https://www.interactivebrokers.com/docs/web-api/authentication/introduction
- Getting Started (retail usa o Gateway): https://www.interactivebrokers.com/docs/web-api/getting-started
- Instalação e autenticação do Gateway: https://www.interactivebrokers.com/docs/web-api/authentication/cpgw/installation-authentication
- Limites de requisição: https://www.interactivebrokers.com/docs/web-api/v1/pacing-limitations
- WebSocket (tópicos): https://www.interactivebrokers.com/docs/web-api/ws/introduction
- Endpoint de trades (hoje + 6 dias): https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/trades
- Status de autenticação: https://www.interactivebrokers.com/docs/web-api/v1/endpoints/session/authentication-status
- Recursos de segurança (PTC, restrição de IP): https://www.interactivebrokers.com/docs/web-api/authentication/additional-security-features
- Flex Web Service — introdução, geração e leitura do relatório, token: https://www.interactivebrokers.com/docs/web-api/flex-web-service/introduction · https://www.interactivebrokers.com/docs/web-api/flex-web-service/using-flex-web-service/generate-the-report · https://www.interactivebrokers.com/docs/web-api/flex-web-service/using-flex-web-service/retrieve-the-report · https://www.interactivebrokers.com/docs/web-api/flex-web-service/client-portal-configuration/enable-and-create-access-token
- Requisitos de dados de mercado e saldo mínimo: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-requirements · https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-subscription-minimum-equity-balance-requirements
- 2FA aceito pela API: https://www.interactivebrokers.com/docs/third-party-integrations/requirements-limitations/supported-two-factor-authentication-2-fa
- TWS API — modo Read-Only: https://interactivebrokers.github.io/tws-api/initial_setup.html

Vistas só por resumo de busca (o site devolveu bloqueio ao leitor automático), tratadas como prováveis:

- FAQ de autenticação (sessão de 24 h, meia-noite, ~6 min sem `/tickle`): https://www.interactivebrokers.com/docs/web-api/web-api-v-1-0-documentation/authentication/authentication-frequently-asked-questions
- Validade do token Flex de 6 h a 1 ano e restrição de IP: confirmada também na página oficial do token acima.

Fontes de comunidade (não oficiais, só como pista):

- OAuth 1.0a para contas individuais (ibind): https://github.com/Voyz/ibind/wiki/OAuth-1.0a
- Problemas de sessão do Gateway (IBeam): https://github.com/Voyz/ibeam/issues/152

**Não verificado / não sei:** preço atual das assinaturas de dados; limites dos planos gratuitos de Supabase e Vercel; atraso real do Flex "Trade Confirmation"; intervalo máximo de datas por requisição Flex; se conta individual pode ter um segundo usuário com bloqueio de ordens; limites do WebSocket; mudanças recentes na regra PDT.
