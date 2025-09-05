import datetime
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from db.session import get_session
from db.models import Transaction, TransactionType, Category
from db.auth import auth

async def last_transitions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    await update.message.reply_text("⌛ Buscando suas últimas 10 transações...")

    async with get_session() as session:
        stmt = (
            select(Transaction)
            .where(Transaction.profile_id == profile.id)
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(10)
        )
        result = await session.execute(stmt)
        transacoes = result.scalars().all()

        if not transacoes:
            await update.message.reply_text("ℹ️ Nenhuma transação encontrada.")
            return

        lines = ["📋 Últimas 10 transações:\n"]
        for i, tx in enumerate(transacoes, start=1):
            # data
            try:
                date_str = tx.date.strftime("%d/%m/%Y")
            except Exception:
                # caso seja datetime ou string
                if isinstance(tx.date, datetime.datetime):
                    date_str = tx.date.date().strftime("%d/%m/%Y")
                else:
                    date_str = str(tx.date)

            # tipo e sinal
            if tx.type == TransactionType.SAIDA:
                tipo_text = "SAÍDA"
                sign = "-"
                emoji = "🔻"
            else:
                tipo_text = "ENTRADA"
                sign = "+"
                emoji = "🟢"

            # categoria
            category_name = ""
            if tx.category_id:
                cat = await session.get(Category, tx.category_id)
                if cat:
                    category_name = cat.name

            # descrição
            desc = (tx.description or "").strip() or "-"

            # valor (usar abs se guardou saídas como negativos)
            try:
                value = float(tx.value)
            except Exception:
                value = tx.value or 0
            display_value = f"{sign}R$ {abs(value):.2f}"

            lines.append(f"{i}. {date_str} • {emoji} {tipo_text} • {category_name}\n   {desc}\n   {display_value}\n")

        mensagem = "\n".join(lines)
        # enviar (Telegram tem limite ~4096 chars — 10 transações normalmente cabe)
        await update.message.reply_text(mensagem)
