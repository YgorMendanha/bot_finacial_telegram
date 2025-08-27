import asyncio
from datetime import date
from sqlalchemy import select
from  db.session import get_session
from  db.models import Profile, Account, Transaction, TransactionType, Category, CategoryType


async def seed():
    async with get_session() as session:
        # === PEGAR O PROFILE EXISTENTE ===
        profile = await session.scalar(select(Profile).limit(1))
        if not profile:
            raise RuntimeError("Nenhum profile encontrado. Crie antes de rodar o seed.")

        # === PEGAR A CONTA PRINCIPAL ===
        account_main = await session.scalar(
            select(Account).where(Account.name == "Principal", Account.profile_id == profile.id)
        )
        if not account_main:
            raise RuntimeError("Conta 'Principal' não encontrada. Crie antes de rodar o seed.")

        # === FUNÇÃO PARA PEGAR/CRIAR CATEGORIA ===
        async def get_or_create_category(name: str):
            category = await session.scalar(
                select(Category).where(Category.name == name, Category.profile_id == profile.id)
            )
            if not category:
                category = Category(
                    name=name,
                    type=CategoryType.VARIAVEL,
                    profile_id=profile.id
                )
                session.add(category)
                await session.flush()
            return category

        # === TRANSAÇÕES ===
        transactions_data = [
            # 20/08 - entrada
            {"type": TransactionType.ENTRADA, "value": 4800, "date": date(2025, 8, 20), "desc": "i9tv", "category": None},

            # 22/08 - saídas
            {"type": TransactionType.SAIDA, "value": 20, "date": date(2025, 8, 22), "desc": "gabi", "category": "Pessoal"},
            {"type": TransactionType.SAIDA, "value": 200, "date": date(2025, 8, 22), "desc": "calyton", "category": "Pessoal"},
            {"type": TransactionType.SAIDA, "value": 769, "date": date(2025, 8, 22), "desc": "fatura cartão pf", "category": "Cartão de Crédito"},
            {"type": TransactionType.SAIDA, "value": 62, "date": date(2025, 8, 22), "desc": "celular", "category": "Telefonia"},
            {"type": TransactionType.SAIDA, "value": 68, "date": date(2025, 8, 22), "desc": "internet", "category": "Internet"},
            {"type": TransactionType.SAIDA, "value": 371, "date": date(2025, 8, 22), "desc": "supermercado", "category": "Mercado"},
            {"type": TransactionType.SAIDA, "value": 443, "date": date(2025, 8, 22), "desc": "bemol conta", "category": "Contas"},
            {"type": TransactionType.SAIDA, "value": 44, "date": date(2025, 8, 22), "desc": "bemol remédio", "category": "Saúde"},
            {"type": TransactionType.SAIDA, "value": 81, "date": date(2025, 8, 22), "desc": "das", "category": "Impostos"},
            {"type": TransactionType.SAIDA, "value": 1350, "date": date(2025, 8, 22), "desc": "aluguel", "category": "Moradia"},
            {"type": TransactionType.SAIDA, "value": 500, "date": date(2025, 8, 22), "desc": "energia", "category": "Energia"},
            {"type": TransactionType.SAIDA, "value": 944, "date": date(2025, 8, 22), "desc": "nubanck conta", "category": "Contas"},
        ]

        # === INSERIR TRANSAÇÕES ===
        for tx in transactions_data:
            category_id = None
            if tx["category"]:
                category = await get_or_create_category(tx["category"])
                category_id = category.id

            transaction = Transaction(
                account_id=account_main.id,
                profile_id=profile.id,
                category_id=category_id,
                type=tx["type"],
                value=tx["value"],
                date=tx["date"],
                description=tx["desc"],
                is_transfer=False,
                balance_before=account_main.balance,
            )

            # Atualizar saldo
            if tx["type"] == TransactionType.ENTRADA:
                account_main.balance += tx["value"]
            else:
                account_main.balance -= tx["value"]

            session.add(transaction)

        await session.commit()
        print(f"Seed finalizado! Saldo da conta 'Principal': {account_main.balance}")


if __name__ == "__main__":
    asyncio.run(seed())
