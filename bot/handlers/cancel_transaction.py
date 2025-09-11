import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from sqlalchemy import select, and_, delete
from db.session import get_session
from db.models import Transaction, TransactionType, Category, Account
from db.auth import auth

async def cancel_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    text = (update.message.text or "").strip().lower()

    if "step_cancel" not in context.user_data:
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

            lines = ["📋 Últimas transações (responda apenas com a POSIÇÃO mostrada, ex: 1):\n"]
            tx_ids = []
            for i, tx in enumerate(transacoes, start=1):
                tx_ids.append(tx.id)
                try:
                    date_str = tx.date.strftime("%d/%m/%Y")
                except Exception:
                    if isinstance(tx.date, datetime.datetime):
                        date_str = tx.date.date().strftime("%d/%m/%Y")
                    else:
                        date_str = str(tx.date)

                if tx.type == TransactionType.SAIDA:
                    tipo_text = "SAÍDA"
                    emoji = "🔻"
                else:
                    tipo_text = "ENTRADA"
                    emoji = "🟢"

                category_name = ""
                if tx.category_id:
                    cat = await session.get(Category, tx.category_id)
                    if cat:
                        category_name = cat.name

                desc = (tx.description or "").strip() or "-"
                try:
                    value = float(tx.value)
                except Exception:
                    value = tx.value or 0.0
                display_value = f"{'-' if tx.type == TransactionType.SAIDA else '+'}R$ {abs(value):.2f}"

                lines.append(f"{i}. {date_str} • {emoji} {tipo_text} • {category_name}\n   {desc}\n   {display_value}\n")

            mensagem = "\n".join(lines)
            context.user_data["cancel_transaction"] = tx_ids
            context.user_data["step_cancel"] = "await_choice"

            await update.message.reply_text(mensagem)
            await update.message.reply_text(
                "Qual POSIÇÃO deseja cancelar? Envie apenas o número da posição .\n"
                "Para abortar, envie 'cancelar'.",
                reply_markup=ReplyKeyboardMarkup([["cancelar"]], one_time_keyboard=True, resize_keyboard=True)
            )
        return

    if context.user_data.get("step_cancel") == "await_choice":
        if text in ("cancelar", "sair", "não", "nao"):
            await update.message.reply_text("Ok — operação de cancelamento abortada.", reply_markup=ReplyKeyboardRemove())
            context.user_data.pop("cancel_transaction", None)
            context.user_data.pop("step_cancel", None)
            return

        pending = context.user_data.get("cancel_transaction") or []
        if not pending:
            await update.message.reply_text("ℹ️ Estado expirado. Use /cancel para iniciar novamente.", reply_markup=ReplyKeyboardRemove())
            context.user_data.pop("step_cancel", None)
            return

        try:
            n = int(text)
        except Exception:
            await update.message.reply_text(f"❗ Envie apenas a POSIÇÃO mostrada (1 a {len(pending)}). Não envie o ID.", reply_markup=ReplyKeyboardRemove())
            return

        if not (1 <= n <= len(pending)):
            await update.message.reply_text(f"❗ Posição inválida. Envie um número entre 1 e {len(pending)}. NÃO envie o ID.", reply_markup=ReplyKeyboardRemove())
            return

        index = n - 1
        tx_id_to_cancel = pending[index]

        async with get_session() as session:
            tx = await session.get(Transaction, tx_id_to_cancel)
            if tx is None or tx.profile_id != profile.id:
                await update.message.reply_text("ℹ️ Transação não encontrada ou não pertence a você.", reply_markup=ReplyKeyboardRemove())
                context.user_data.pop("cancel_transaction", None)
                context.user_data.pop("step_cancel", None)
                return

            try:
                date_str = tx.date.strftime("%d/%m/%Y")
            except Exception:
                if isinstance(tx.date, datetime.datetime):
                    date_str = tx.date.date().strftime("%d/%m/%Y")
                else:
                    date_str = str(tx.date)

            tipo_text = "SAÍDA" if tx.type == TransactionType.SAIDA else "ENTRADA"
            emoji = "🔻" if tx.type == TransactionType.SAIDA else "🟢"
            desc = (tx.description or "").strip() or "-"
            try:
                value = float(tx.value)
            except Exception:
                value = tx.value or 0.0
            display_value = f"{'-' if tx.type == TransactionType.SAIDA else '+'}R$ {abs(value):.2f}"
            cat_name = ""
            if tx.category_id:
                cat = await session.get(Category, tx.category_id)
                if cat:
                    cat_name = cat.name

            resumo = (
                f"🔎 Transação selecionada (posição {n}):\n\n"
                f"ID: {tx.id}\nData: {date_str}\nTipo: {emoji} {tipo_text}\nCategoria: {cat_name or '-'}\n"
                f"Descrição: {desc}\nValor: {display_value}\n\n"
                "Confirmar cancelamento? (sim/não)"
            )

            context.user_data["pending_cancel_index"] = index
            context.user_data["step_cancel"] = "confirm_cancel"

            await update.message.reply_text(resumo, reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True))
        return

    if context.user_data.get("step_cancel") == "confirm_cancel":
        if text in ("não", "nao", "n"):
            await update.message.reply_text("Ok — cancelamento abortado.", reply_markup=ReplyKeyboardRemove())
            context.user_data.pop("cancel_transaction", None)
            context.user_data.pop("step_cancel", None)
            context.user_data.pop("pending_cancel_index", None)
            return

        if text not in ("sim", "s"):
            await update.message.reply_text("Responda 'sim' ou 'não'.", reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True))
            return

        index = context.user_data.get("pending_cancel_index")
        tx_ids = context.user_data.get("cancel_transaction") or []
        if index is None or index < 0 or index >= len(tx_ids):
            await update.message.reply_text("Estado inválido. Por favor, inicie novamente com /cancel.", reply_markup=ReplyKeyboardRemove())
            context.user_data.clear()
            return

        tx_id = tx_ids[index]
        await update.message.reply_text("⌛ Processando cancelamento da transação...", reply_markup=ReplyKeyboardRemove())

        async with get_session() as session:
            try:
                async with session.begin():
                    tx = await session.get(Transaction, tx_id)
                    if tx is None:
                        await update.message.reply_text("ℹ️ Transação não encontrada (já removida).")
                        context.user_data.clear()
                        return

                    if tx.profile_id != profile.id:
                        await update.message.reply_text("🚫 Você não tem permissão para cancelar essa transação.")
                        context.user_data.clear()
                        return

                    if getattr(tx, "is_settled", False):
                        await update.message.reply_text("⚠️ Não é permitido cancelar uma transação já liquidada/settled.")
                        context.user_data.clear()
                        return

                    account = await session.get(Account, tx.account_id)
                    if account is None:
                        await update.message.reply_text("❗ Conta associada à transação não encontrada.")
                        context.user_data.clear()
                        return

                    try:
                        raw_val = float(tx.value or 0.0)
                    except Exception:
                        raw_val = 0.0
                    delta = abs(raw_val)

                    messages = []

                    def revert_balance(acc: Account, tx_type: TransactionType, abs_val: float):
                        if tx_type == TransactionType.SAIDA:
                            acc.balance = (acc.balance or 0.0) + abs_val
                        else:
                            acc.balance = (acc.balance or 0.0) - abs_val

                    revert_balance(account, tx.type, delta)
                    session.add(account)
                    messages.append(f"➡️ Ajustando saldo da conta '{account.name}'... (aplicando {'+' if tx.type==TransactionType.SAIDA else '-'}{delta:.2f})")

                    counterpart_tx = None
                    if getattr(tx, "is_transfer", False):
                        if getattr(tx, "settlement_id", None):
                            counterpart_tx = await session.get(Transaction, tx.settlement_id)

                        if counterpart_tx is None and getattr(tx, "transfer_account_id", None):
                            stmt = (
                                select(Transaction)
                                .where(
                                    and_(
                                        Transaction.is_transfer == True,
                                        Transaction.profile_id == profile.id,
                                        Transaction.transfer_account_id == tx.account_id,
                                        Transaction.account_id == tx.transfer_account_id,
                                        Transaction.id != tx.id
                                    )
                                )
                                .limit(1)
                            )
                            res = await session.execute(stmt)
                            counterpart_tx = res.scalars().first()

                        if counterpart_tx:
                            if getattr(counterpart_tx, "is_settled", False):
                                await update.message.reply_text(
                                    "⚠️ A transação faz parte de uma transferência cuja contraparte está liquidada. Não é possível cancelar automaticamente."
                                )
                                context.user_data.clear()
                                return

                            dest_account = await session.get(Account, counterpart_tx.account_id)
                            if dest_account:
                                try:
                                    counterpart_val = abs(float(counterpart_tx.value or 0.0))
                                except Exception:
                                    counterpart_val = 0.0

                                if counterpart_tx.type == TransactionType.SAIDA:
                                    dest_account.balance = (dest_account.balance or 0.0) + counterpart_val
                                else:
                                    dest_account.balance = (dest_account.balance or 0.0) - counterpart_val

                                session.add(dest_account)
                                messages.append(f"➡️ Ajustando saldo da conta '{dest_account.name}' (contraparte) ... (aplicando {'+' if counterpart_tx.type==TransactionType.SAIDA else '-'}{counterpart_val:.2f})")

                            await session.execute(delete(Transaction).where(Transaction.id == counterpart_tx.id))
                            messages.append(f"🗑️ Contraparte da transferência (id={counterpart_tx.id}) removida.")
                        else:
                            if getattr(tx, "transfer_account_id", None):
                                dest_acc = await session.get(Account, tx.transfer_account_id)
                                if dest_acc:
                                    if tx.type == TransactionType.SAIDA:
                                        dest_acc.balance = (dest_acc.balance or 0.0) - delta
                                    else:
                                        dest_acc.balance = (dest_acc.balance or 0.0) + delta
                                    session.add(dest_acc)
                                    messages.append(f"➡️ Ajustando saldo da conta '{dest_acc.name}' (destino) para reverter a transferência.")
                                else:
                                    messages.append("⚠️ Conta destino da transferência não encontrada; não foi possível ajustar automaticamente.")

                    await session.execute(delete(Transaction).where(Transaction.id == tx.id))
                    await session.flush()

                refreshed_account = await session.get(Account, account.id)
                messages.insert(0, f"Saldo da conta '{refreshed_account.name}' agora é R$ {refreshed_account.balance:.2f}")

                await update.message.reply_text("\n".join(messages))

            except Exception as e:
                await update.message.reply_text(f"❌ Falha ao cancelar a transação: {e}")

        context.user_data.pop("cancel_transaction", None)
        context.user_data.pop("step_cancel", None)
        context.user_data.pop("pending_cancel_index", None)
        return
