# handlers/my_data.py
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
import datetime
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from db.session import get_session
from db.models import Profile, Account, Debt, DebtStatus, Transaction, TransactionType
from db.auth import auth

def parse_amount(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "."))
    except Exception:
        raise ValueError("valor inválido")

def format_money(v: Decimal) -> str:
    v = Decimal(v or 0)
    q = v.quantize(Decimal("0.01"))
    s = f"{q:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")

def unsettled_clause():
    if hasattr(Transaction, "is_settled"):
        return (Transaction.is_settled == False)
    if hasattr(Transaction, "settlement_id"):
        return (Transaction.settlement_id == None)
    return ~Transaction.description.like("%PAID_BY:%")

async def unpaid_card_total(session, account_id, start_date=None, end_date=None):
    clause = unsettled_clause()
    q = select(func.coalesce(func.sum(Transaction.value), 0)).where(
        Transaction.account_id == account_id,
        Transaction.type == TransactionType.SAIDA,
        Transaction.is_transfer == False,
        clause,
    )
    if start_date:
        q = q.where(Transaction.date >= start_date)
    if end_date:
        q = q.where(Transaction.date < end_date)
    res = await session.execute(q)
    return to_decimal(res.scalar() or 0)

ACCOUNT_TYPE_ACCOUNT = "bank"
ACCOUNT_TYPE_CARD = "credit_card"
DEFAULT_ACCOUNTS = ("disponível", "principal")

async def compute_avg_monthly(session, profile_id: int, months: int = 6):
    today = datetime.date.today()
    incomes, expenses = [], []
    for m in range(months):
        start_date = (today.replace(day=1) - relativedelta(months=m))
        end_date = start_date + relativedelta(months=1)
        settled_filter = unsettled_clause()

        res_inc = await session.execute(
            select(func.coalesce(func.sum(Transaction.value), 0)).where(
                Transaction.profile_id == profile_id,
                Transaction.type == TransactionType.ENTRADA,
                Transaction.date >= start_date,
                Transaction.date < end_date,
                Transaction.is_transfer == False,
                settled_filter,
            )
        )
        inc = to_decimal(res_inc.scalar() or 0)

        res_out = await session.execute(
            select(func.coalesce(func.sum(Transaction.value), 0)).where(
                Transaction.profile_id == profile_id,
                Transaction.type == TransactionType.SAIDA,
                Transaction.date >= start_date,
                Transaction.date < end_date,
                Transaction.is_transfer == False,
                settled_filter,
            )
        )
        out = -to_decimal(res_out.scalar() or 0)
        if inc != 0 or out != 0:
            incomes.append(inc)
            expenses.append(out)

    count = max(1, len(incomes))
    avg_income = sum(incomes)/Decimal(count) if incomes else Decimal("0")
    avg_expense = sum(expenses)/Decimal(count) if expenses else Decimal("0")
    return (avg_income, avg_expense)

async def my_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return
    text = (update.message.text or "").strip()
    async with get_session() as session:
        profile = await session.get(Profile, profile.id, options=[selectinload(Profile.accounts), selectinload(Profile.debts)])
        all_accounts = list(profile.accounts or [])
        accounts_list = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == ACCOUNT_TYPE_ACCOUNT]
        cards_list = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == ACCOUNT_TYPE_CARD]

        if "mydata_step" not in context.user_data or context.user_data["mydata_step"] == "show_summary":
            await update.message.reply_text("Carregando...", reply_markup=ReplyKeyboardRemove())
            avg_income, avg_expense = await compute_avg_monthly(session, profile.id, months=6)
            accounts_text = "\n".join(f"- {a.name}: {format_money(a.balance)}" for a in accounts_list) or "Nenhuma conta cadastrada."
            cards_text = "\n\n".join(f"- {c.name}: Saldo {format_money(c.balance)} \nFatura aberta: {format_money(await unpaid_card_total(session, c.id))}" for c in cards_list) or "Nenhum cartão cadastrado."
            debts_text = "\n".join(f"• {d.creditor} - {format_money(d.monthly_payment)} x {d.months} meses (Total: {format_money(d.total_amount)})" for d in profile.debts) or "Nenhuma dívida cadastrada."

            summary = (
                f"💼 **Meus Dados**\n\n"
                f"Nome: {profile.name}\n"
                f"Reserva de emergência: {format_money(profile.emergency_fund)}\n\n"
                f"🏦 Contas:\n{accounts_text}\n\n"
                f"💳 Cartões:\n{cards_text}\n\n"
                f"📈 Média (últimos 6 meses)\nReceita: {format_money(avg_income)}\nDespesa: {format_money(avg_expense)}\n\n"
                f"💳 Dívidas:\n{debts_text}\n\nO que deseja editar?"
            )
            options = [["Nome"], ["Contas", "Cartões"], ["Transferência"], ["Dívidas", "Nada"]]
            context.user_data["mydata_step"] = "edit_option"
            await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            return

        if context.user_data.get("mydata_step") == "edit_option":
            choice = text.lower()
            if choice == "nome":
                context.user_data["mydata_step"] = "edit_name"
                await update.message.reply_text("Digite o novo nome:")
            elif choice == "contas":
                context.user_data["accounts_scope"] = ACCOUNT_TYPE_ACCOUNT
                context.user_data["mydata_step"] = "accounts_menu"
                options = [[a.name] for a in accounts_list] + [["Adicionar Conta"], ["Voltar"]]
                await update.message.reply_text("Escolha uma conta ou 'Adicionar Conta':", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            elif choice in ("cartões", "cartoes"):
                context.user_data["accounts_scope"] = ACCOUNT_TYPE_CARD
                context.user_data["mydata_step"] = "accounts_menu"
                options = [[c.name] for c in cards_list] + [["Voltar"]]
                await update.message.reply_text("Escolha um cartão:", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            elif choice == "transferência":
                context.user_data["mydata_step"] = "transfer_from"
                options = [[a.name] for a in all_accounts] + [["Voltar"]]
                await update.message.reply_text("Escolha a conta de ORIGEM:", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            elif choice == "dívidas":
                context.user_data["mydata_step"] = "edit_debts_menu"
                await my_data(update, context)
            else:
                context.user_data.clear()
                await update.message.reply_text("Ok, nada será alterado.", reply_markup=ReplyKeyboardRemove())
            return

        if context.user_data.get("mydata_step") == "accounts_menu":
            scope = context.user_data.get("accounts_scope", ACCOUNT_TYPE_ACCOUNT)
            items = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == scope]
            choice = text.strip()
            add_label = "Adicionar Conta" if scope == ACCOUNT_TYPE_ACCOUNT else None

            if add_label and choice.lower() == add_label.lower():
                context.user_data["mydata_step"] = "create_account_name"
                await update.message.reply_text(f"Digite o nome do novo {'conta' if scope == ACCOUNT_TYPE_ACCOUNT else 'cartão'}:")
                return
            elif choice.lower() == "voltar":
                context.user_data["mydata_step"] = "show_summary"
                await my_data(update, context)
                return

            acc = next((a for a in items if a.name.lower() == choice.lower()), None)
            if not acc:
                await update.message.reply_text("Item não encontrado. Escolha do menu.")
                return

            context.user_data["editing_account_id"] = acc.id
            options = [["Renomear"], ["Remover"], ["Voltar"]]
            context.user_data["mydata_step"] = "account_action"
            await update.message.reply_text(f"O que deseja fazer com '{acc.name}'?", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            return

        if context.user_data.get("mydata_step") == "account_action":
            acc_id = context.user_data.get("editing_account_id")
            acc = await session.get(Account, acc_id)
            choice = text.strip().lower()
            if choice == "renomear":
                context.user_data["mydata_step"] = "rename_account"
                await update.message.reply_text(f"Digite o novo nome para {acc.name}:")
            elif choice == "remover":
                if acc.name.lower() in DEFAULT_ACCOUNTS:
                    await update.message.reply_text("Essa conta é padrão e não pode ser removida.")
                    context.user_data["mydata_step"] = "accounts_menu"
                    await my_data(update, context)
                    return
                if to_decimal(acc.balance) != 0:
                    await update.message.reply_text("Não é possível remover um item com saldo diferente de zero. Zere o saldo antes de remover.")
                    context.user_data["mydata_step"] = "accounts_menu"
                    await my_data(update, context)
                    return
                await session.delete(acc)
                await session.commit()
                context.user_data["mydata_step"] = "show_summary"
                await update.message.reply_text(f"Item '{acc.name}' removido.", reply_markup=ReplyKeyboardRemove())
                await my_data(update, context)
            elif choice == "voltar":
                context.user_data["mydata_step"] = "accounts_menu"
                await my_data(update, context)
            else:
                await update.message.reply_text("Escolha inválida. Use o menu.")
            return
