# Bot Financeiro (Telegram)

## Visão geral
O **Bot Financeiro** é um bot para Telegram desenvolvido para ajudar no controle financeiro do dia a dia.  
Ele permite registrar transações (entradas e saídas), gerenciar débitos e contas, definir metas diárias de gasto e gerar resumos mensais de receitas e despesas, diferenciando gastos fixos e variáveis.

---

## Tecnologias principais
- **Python 3.11+**
- **python-telegram-bot** — integração com a API do Telegram  
- **SQLAlchemy** — ORM para persistência de dados  

## Comandos disponíveis
- `/start` — criação do usuário e configuração das contas principais  
- `/comprarapida` — adicionar uma compra rápida  
- `/add` — adicionar transação (entrada ou saída)  
- `/carteira` — definir ou consultar a meta diária de gastos  
- `/listacategorias` — listar ou adicionar categorias (fixas ou variáveis)  
- `/resumo` — exibir resumo mensal de receitas e despesas  
- `/meusdados` — visualizar dados do usuário (contas, dívidas, cartões)  

---

## Configuração e execução local

1. Clone o repositório:
   ```bash
   git clone https://github.com/seu-usuario/bot-financeiro.git
   cd bot-financeiro

   python -m venv .venv
   source .venv/bin/activate   # Linux / macOS
   .venv\Scripts\activate      # Windows

   TELEGRAM_BOT_TOKEN=seu_token_aqui
   DATABASE_URL=postgresql+asyncpg://usuario:senha@localhost:5432/financas

   python main.py


