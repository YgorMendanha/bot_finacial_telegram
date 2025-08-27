# handlers/wallet.py  (acumulativo com "carregando" e resumo focado)
import datetime
import calendar
import traceback
from decimal import Decimal, ROUND_DOWN, InvalidOperation, getcontext
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from dateutil.relativedelta import relativedelta

from db.session import get_session
from db.models import Transaction, TransactionType, Account
from db.auth import auth

# precisão decimal suficiente
getcontext().prec = 18


def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def format_brl(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if q < 0 else ""
    return f"{sign}R$ {s}"


async def daily_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
  
    profile = await auth(update)
    if profile is None:
        return
    # envia mensagem de "carregando" imediatamente
    try:
        loading_msg = await update.message.reply_text("⌛ Gerando resumo — aguarde...")
    except Exception:
        loading_msg = None


    args = context.args or []
    account_id = int(args[0]) if args and args[0].isdigit() else None

    today = datetime.date.today()

    # Vars de saída
    cota_daily = Decimal("0")
    generated_until_yesterday = Decimal("0")
    carryover_before_today = Decimal("0")
    available_today = Decimal("0")
    spent_today = Decimal("0")
    accumulated_today = Decimal("0")
    entry_amount = Decimal("0")
    entry_date = None
    next_entry_date = None
    period_days = None
    txs_list = []

    try:
        async with get_session() as session:
            # pega account "Disponível" se não foi passado account_id
            if account_id is None:
                acc_stmt = select(Account).where(
                    Account.profile_id == profile.id,
                    Account.name == "Disponível"
                ).limit(1)
                acc_res = await session.execute(acc_stmt)
                acc_obj = acc_res.scalars().first()
                if not acc_obj:
                    # edita mensagem de loading com aviso e retorna
                    if loading_msg:
                        try:
                            await loading_msg.edit_text("⚠️ Não encontrei conta 'Disponível'. Crie-a ou informe account_id.")
                        except Exception:
                            pass
                    else:
                        await update.message.reply_text("⚠️ Não encontrei conta 'Disponível'. Crie-a ou informe account_id.")
                    return
                account_id = acc_obj.id
            else:
                # valida conta pertence ao profile
                acc_stmt = select(Account).where(Account.id == account_id, Account.profile_id == profile.id).limit(1)
                acc_res = await session.execute(acc_stmt)
                acc_obj = acc_res.scalars().first()
                if not acc_obj:
                    if loading_msg:
                        try:
                            await loading_msg.edit_text("⚠️ Conta não encontrada ou não pertence ao seu perfil.")
                        except Exception:
                            pass
                    else:
                        await update.message.reply_text("⚠️ Conta não encontrada ou não pertence ao seu perfil.")
                    return

            # --- 1) encontra a ÚLTIMA data de ENTRADA na conta ---
            stmt_last_date = select(func.max(Transaction.date)).where(
                Transaction.account_id == account_id,
                Transaction.profile_id == profile.id,
                Transaction.type == TransactionType.ENTRADA
            )
            res_last_date = await session.execute(stmt_last_date)
            last_entry_date = res_last_date.scalar()  # None se não houver

            if last_entry_date is not None:
                # --- buscar a ÚLTIMA transação do tipo ENTRADA naquela data ---
                stmt_last_entry_tx = select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.profile_id == profile.id,
                    Transaction.type == TransactionType.ENTRADA,
                    Transaction.date == last_entry_date
                ).order_by(Transaction.id.desc()).limit(1)
                res_last_tx = await session.execute(stmt_last_entry_tx)
                last_entry_tx = res_last_tx.scalars().first()

                if last_entry_tx:
                    # calcula o saldo disponível naquele momento: balance_before + value
                    balance_before_tx = to_decimal(getattr(last_entry_tx, "balance_before", None))
                    value_tx = to_decimal(getattr(last_entry_tx, "value", 0))
                    balance_after = balance_before_tx + value_tx
                    entry_amount = balance_after
                else:
                    # fallback: soma entradas daquele dia
                    stmt_entry_amount = select(func.coalesce(func.sum(Transaction.value), 0)).where(
                        Transaction.account_id == account_id,
                        Transaction.profile_id == profile.id,
                        Transaction.type == TransactionType.ENTRADA,
                        Transaction.date == last_entry_date
                    )
                    res_amt = await session.execute(stmt_entry_amount)
                    entry_amount = to_decimal(res_amt.scalar() or 0)

                entry_date = last_entry_date
                next_entry_date = entry_date + relativedelta(months=1)
                period_days = (next_entry_date - entry_date).days
                if period_days <= 0:
                    period_days = 1

                cota_daily = (entry_amount / Decimal(period_days))

                # dias desde entry (inclusivo)
                days_since_entry_inclusive = (today - entry_date).days + 1
                if days_since_entry_inclusive < 1:
                    days_since_entry_inclusive = 0
                if days_since_entry_inclusive > period_days:
                    days_since_entry_inclusive = period_days

                days_until_yesterday = max(0, days_since_entry_inclusive - 1)
                generated_until_yesterday = cota_daily * Decimal(days_until_yesterday)

                # SAIDAS desde entry_date até < today
                stmt_spent_until_yesterday = select(func.coalesce(func.sum(Transaction.value), 0)).where(
                    Transaction.account_id == account_id,
                    Transaction.profile_id == profile.id,
                    Transaction.type == TransactionType.SAIDA,
                    Transaction.date >= entry_date,
                    Transaction.date < today
                )
                res_spent_prior = await session.execute(stmt_spent_until_yesterday)
                spent_until_yesterday_raw = res_spent_prior.scalar() or 0
                spent_until_yesterday = -to_decimal(spent_until_yesterday_raw)

                carryover_before_today = generated_until_yesterday - spent_until_yesterday

                # cota de hoje (se ainda dentro do período)
                if days_since_entry_inclusive <= 0:
                    todays_cota = Decimal("0")
                else:
                    todays_cota = cota_daily if days_since_entry_inclusive <= period_days else Decimal("0")

                available_today = carryover_before_today + todays_cota

                # gasto hoje
                stmt_spent_today = select(func.coalesce(func.sum(Transaction.value), 0)).where(
                    Transaction.account_id == account_id,
                    Transaction.profile_id == profile.id,
                    Transaction.type == TransactionType.SAIDA,
                    Transaction.date >= today,
                    Transaction.date < (today + relativedelta(days=1))
                )
                res_spent_today = await session.execute(stmt_spent_today)
                spent_today = -to_decimal(res_spent_today.scalar() or 0)

                accumulated_today = available_today - spent_today

                # calcular disponível amanhã:
                # se amanhã ainda dentro do período, tomorrow_cota = cota_daily else 0
                days_since_entry_tomorrow = days_since_entry_inclusive + 1
                if days_since_entry_tomorrow <= 0:
                    tomorrow_cota = Decimal("0")
                else:
                    tomorrow_cota = cota_daily if days_since_entry_tomorrow <= period_days else Decimal("0")

                available_tomorrow = accumulated_today + tomorrow_cota

            else:
                # fallback: sem entrada registrada -> usar balance atual e dividir dias restantes mês
                acc_stmt = select(Account).where(Account.id == account_id)
                acc_res = await session.execute(acc_stmt)
                acc = acc_res.scalars().first()
                balance = to_decimal(acc.balance) if acc else Decimal("0")

                # dias restantes mês (incluindo hoje)
                year = today.year
                month = today.month
                last_day = calendar.monthrange(year, month)[1]
                last_day_of_month = datetime.date(year, month, last_day)
                days_remaining_incl = (last_day_of_month - today).days + 1
                if days_remaining_incl <= 0:
                    days_remaining_incl = 1

                cota_daily = balance / Decimal(days_remaining_incl)
                carryover_before_today = Decimal("0")
                available_today = cota_daily

                # gasto hoje
                stmt_spent_today = select(func.coalesce(func.sum(Transaction.value), 0)).where(
                    Transaction.account_id == account_id,
                    Transaction.profile_id == profile.id,
                    Transaction.type == TransactionType.SAIDA,
                    Transaction.date >= today,
                    Transaction.date < (today + relativedelta(days=1))
                )
                res_spent_today = await session.execute(stmt_spent_today)
                spent_today = -to_decimal(res_spent_today.scalar() or 0)

                accumulated_today = available_today - spent_today

                # amanhã (simples): accumulated + cota
                available_tomorrow = accumulated_today + cota_daily

            # extrato do dia (para exibir)
            stmt_txs = select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.profile_id == profile.id,
                Transaction.date >= today,
                Transaction.date < (today + relativedelta(days=1))
            ).order_by(Transaction.id).limit(50)
            res_txs = await session.execute(stmt_txs)
            txs = res_txs.scalars().all()

            for t in txs:
                val = to_decimal(t.value)
                if t.type == TransactionType.SAIDA:
                    val = -val
                txs_list.append({
                    "id": t.id,
                    "type": t.type.name if t.type else None,
                    "description": t.description or "",
                    "value": val,
                })

        # montar resumo enxuto
        lines = []
        lines.append(f"📅 Dia: {today.isoformat()}")
        lines.append("")  # separador
        lines.append(f"🟢 Disponível hoje: *{format_brl(available_today)}*")
        lines.append(f"🔴 Já gasto hoje: {format_brl(spent_today)}")
        lines.append(f"➡️ Disponível amanhã (sobra + cota): {format_brl(available_tomorrow)}")
        lines.append("")
        lines.append("📄 Extrato do dia:")
        if not txs_list:
            lines.append(" (sem transações hoje)")
        else:
            for tx in txs_list:
                typ = "ENTRADA" if tx["type"] == "ENTRADA" else "SAÍDA"
                lines.append(f" - [{typ}] {tx['description'] or 'sem descrição'} | {format_brl(tx['value'])}")

        final_text = "\n".join(lines)

        # edita a mensagem de carregando (se possível); se não, envia nova
        if loading_msg:
            try:
                await loading_msg.edit_text(final_text, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(final_text, parse_mode="Markdown")
        else:
            await update.message.reply_text(final_text, parse_mode="Markdown")

    except Exception as e:
        tb = traceback.format_exc()
        print("Erro no handler daily_budget (resumo focado):", str(e))
        print(tb)
        # tenta editar mensagem de loading com erro
        err_text = "❌ Ocorreu um erro ao gerar o resumo. Verifique os logs do  "
        if loading_msg:
            try:
                await loading_msg.edit_text(err_text)
            except Exception:
                try:
                    await update.message.reply_text(err_text)
                except Exception:
                    pass
        else:
            try:
                await update.message.reply_text(err_text)
            except Exception:
                pass
