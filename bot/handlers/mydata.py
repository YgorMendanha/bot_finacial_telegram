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

# ---------- helpers ----------
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


# utility: build a SQLAlchemy clause that matches UNSETTLED transactions
def unsettled_clause():
    # prefer explicit flags if model has them
    if hasattr(Transaction, "is_settled"):
        return (Transaction.is_settled == False)
    if hasattr(Transaction, "settlement_id"):
        return (Transaction.settlement_id == None)
    # fallback: use description pattern (assumes description exists)
    return ~Transaction.description.like("%PAID_BY:%")


# helper: sum of unpaid card expenses for an account (optionally by period)
async def unpaid_card_total(session, account_id, start_date=None, end_date=None):
    clause = unsettled_clause()
    q = select(func.coalesce(func.sum(Transaction.value), 0)).where(
        Transaction.account_id == account_id,
        Transaction.type == TransactionType.SAIDA,
        Transaction.is_transfer == False,
        clause,
    )
    if start_date is not None:
        q = q.where(Transaction.date >= start_date)
    if end_date is not None:
        q = q.where(Transaction.date < end_date)

    res = await session.execute(q)
    return to_decimal(res.scalar() or 0)


# account types (assumes Account has a 'type' attribute; fallback to 'account' when missing)
ACCOUNT_TYPE_ACCOUNT = "bank"
ACCOUNT_TYPE_CARD = "credit_card"
DEFAULT_ACCOUNTS = ("disponível", "principal")

# ---------- média mensal desconsiderando transferências ----------
async def compute_avg_monthly(session, profile_id: int, months: int = 6):
    today = datetime.date.today()
    incomes = []
    expenses = []
    for m in range(months):
        start = (today.replace(day=1) - relativedelta(months=m))
        start_date = start.replace(day=1)
        end_date = start_date + relativedelta(months=1)

        settled_filter = unsettled_clause()

        # somar entradas não-transferência (não-quitadas)
        res_inc = await session.execute(
            select(func.coalesce(func.sum(Transaction.value), 0))
            .where(
                Transaction.profile_id == profile_id,
                Transaction.type == TransactionType.ENTRADA,
                Transaction.date >= start_date,
                Transaction.date < end_date,
                Transaction.is_transfer == False,
                settled_filter,
            )
        )
        inc = to_decimal(res_inc.scalar() or 0)

        # somar saídas não-transferência (não-quitadas)
        res_out = await session.execute(
            select(func.coalesce(func.sum(Transaction.value), 0))
            .where(
                Transaction.profile_id == profile_id,
                Transaction.type == TransactionType.SAIDA,
                Transaction.date >= start_date,
                Transaction.date < end_date,
                Transaction.is_transfer == False,
                settled_filter,
            )
        )
        out = -to_decimal(res_out.scalar() or 0)  # torna positivo

        if inc != 0 or out != 0:
            incomes.append(inc)
            expenses.append(out)

    if not incomes and not expenses:
        return (Decimal("0"), Decimal("0"))

    count = max(1, len(incomes))
    avg_income = (sum(incomes) / Decimal(count)) if incomes else Decimal("0")
    avg_expense = (sum(expenses) / Decimal(count)) if expenses else Decimal("0")
    return (avg_income, avg_expense)


# ---------- handler principal ----------
async def my_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    text = (update.message.text or "").strip()

    async with get_session() as session:
        profile = await session.get(
            Profile,
            profile.id,
            options=[selectinload(Profile.accounts), selectinload(Profile.debts)],
        )

        # separate accounts and cards
        all_accounts = list(profile.accounts or [])
        accounts_list = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == ACCOUNT_TYPE_ACCOUNT]
        cards_list = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == ACCOUNT_TYPE_CARD]

        # ---------- STEP 1: mostrar resumo ----------
        if "mydata_step" not in context.user_data or context.user_data["mydata_step"] == "show_summary":
            await update.message.reply_text("Carregando...", reply_markup=ReplyKeyboardRemove())
            avg_income, avg_expense = await compute_avg_monthly(session, profile.id, months=6)

            accounts_text = "\n".join(f"- {a.name}: {format_money(a.balance)}" for a in accounts_list) or "Nenhuma conta cadastrada."

            # para cartões, mostramos o saldo e também soma das despesas não-quitadas (fatura)
            cards_lines = []
            for c in cards_list:
                unpaid = await unpaid_card_total(session, c.id)
                cards_lines.append(f"- {c.name}: \n Saldo {format_money(c.balance)} \n Fatura aberta: {format_money(unpaid)}")
            cards_text = "\n\n".join(cards_lines) or "Nenhum cartão cadastrado."

            debts_text = "\n".join(
                f"• {d.creditor}\n"
                f" Valor mensal: {format_money(d.monthly_payment)}\n"
                f" Meses: {d.months}\n"
                f" Total: {format_money(d.total_amount)}\n"
                for d in profile.debts
            ) or "Nenhuma dívida cadastrada."

            summary = (
                f"💼 **Meus Dados**\n\n"
                f"Nome: {profile.name}\n"
                f"Reserva de emergência: {format_money(profile.emergency_fund)}\n\n\n"
                f"🏦 Contas:\n"
                f"{accounts_text}\n\n\n"
                f"💳 Cartões:\n"
                f"{cards_text}\n\n\n"
                f"📈 Média (últimos 6 meses com registro)\n"
                f"Receita: {format_money(avg_income)}\n"
                f"Despesa: {format_money(avg_expense)}\n\n\n"
                f"💳 Dívidas:\n"
                f"{debts_text}\n\n"
                f"O que deseja editar?"
            )
            options = [["Nome"], ["Contas", "Cartões"], ["Transferência"], ["Dívidas", "Nada"]]
            context.user_data["mydata_step"] = "edit_option"
            await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            return

        # ---------- STEP 2: escolher opção ----------
        if context.user_data.get("mydata_step") == "edit_option":
            choice = text.lower()
            if choice == "nome":
                context.user_data["mydata_step"] = "edit_name"
                await update.message.reply_text("Digite o novo nome:")
            elif choice == "contas":
                # set scope to accounts
                context.user_data["accounts_scope"] = ACCOUNT_TYPE_ACCOUNT
                context.user_data["mydata_step"] = "accounts_menu"
                # each account in its own line for usability
                options = [[a.name] for a in accounts_list] + [["Adicionar Conta"], ["Voltar"]]
                await update.message.reply_text("Escolha uma conta ou 'Adicionar Conta':", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            elif choice == "cartões" or choice == "cartoes":
                # set scope to cards
                context.user_data["accounts_scope"] = ACCOUNT_TYPE_CARD
                context.user_data["mydata_step"] = "accounts_menu"
                options = [[c.name] for c in cards_list] + [["Adicionar Cartão"], ["Voltar"]]
                await update.message.reply_text("Escolha um cartão ou 'Adicionar Cartão':", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            elif choice == "transferência":
                context.user_data["mydata_step"] = "transfer_from"
                options = [[a.name] for a in all_accounts] + [["Voltar"]]
                await update.message.reply_text(
                    "Escolha a conta de ORIGEM:",
                    reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True),
                )
            elif choice == "dívidas":
                context.user_data["mydata_step"] = "edit_debts_menu"
                await my_data(update, context)
            else:
                context.user_data.clear()
                await update.message.reply_text("Ok, nada será alterado.", reply_markup=ReplyKeyboardRemove())
            return

        # ---------- CRUD contas/cartões ----------
        # menu: escolher conta/cartão, adicionar, voltar
        if context.user_data.get("mydata_step") == "accounts_menu":
            scope = context.user_data.get("accounts_scope", ACCOUNT_TYPE_ACCOUNT)
            items = [a for a in all_accounts if getattr(a, "type", ACCOUNT_TYPE_ACCOUNT) == scope]
            choice = text.strip()
            add_label = "Adicionar Conta" if scope == ACCOUNT_TYPE_ACCOUNT else "Adicionar Cartão"

            if choice.lower() == add_label.lower():
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

            # mostrar ações para a conta/cartão escolhida
            context.user_data["editing_account_id"] = acc.id
            if acc.type == ACCOUNT_TYPE_ACCOUNT:
                options = [["Adicionar Valor"], ["Remover Valor"], ["Renomear"], ["Remover"], ["Voltar"]]
            else:  # cartão
                options = [["Renomear"], ["Remover"], ["Voltar"]]
            context.user_data["mydata_step"] = "account_action"
            await update.message.reply_text(f"O que deseja fazer com '{acc.name}'?", reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            return

        if context.user_data.get("mydata_step") == "create_account_name":
            acc_name = text.strip()
            scope = context.user_data.get("accounts_scope", ACCOUNT_TYPE_ACCOUNT)
            # prevent reserved names only for real accounts
            if scope == ACCOUNT_TYPE_ACCOUNT and acc_name.lower() in DEFAULT_ACCOUNTS:
                await update.message.reply_text("Nome reservado. Escolha outro.")
                return
            # create account/card with given type (assumes Account model has 'type' column)
            new_acc = Account(profile_id=profile.id, name=acc_name, balance=0.0)
            try:
                setattr(new_acc, "type", scope)
            except Exception:
                # if model doesn't have 'type' attribute, ignore (backwards compatibility)
                pass
            session.add(new_acc)
            await session.commit()
            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(f"{'Conta' if scope == ACCOUNT_TYPE_ACCOUNT else 'Cartão'} '{new_acc.name}' criada.", reply_markup=ReplyKeyboardRemove())
            await my_data(update, context)
            return

        # ações sobre conta/cartão existente
        if context.user_data.get("mydata_step") == "account_action":
            acc_id = context.user_data.get("editing_account_id")
            acc = await session.get(Account, acc_id)
            choice = text.strip().lower()

            if choice == "adicionar valor":
                context.user_data["mydata_step"] = "add_account_value"
                await update.message.reply_text(f"Digite o valor a adicionar na conta {acc.name}:")
                return
            elif choice == "remover valor":
                context.user_data["mydata_step"] = "remove_account_value"
                await update.message.reply_text(f"Digite o valor a remover da conta {acc.name}:")
                return
            elif choice == "renomear":
                context.user_data["mydata_step"] = "rename_account"
                await update.message.reply_text(f"Digite o novo nome para {acc.name}:")
                return
            elif choice == "remover":
                # bloquear exclusão de contas padrão
                if acc.name.lower() in DEFAULT_ACCOUNTS:
                    await update.message.reply_text("Essa conta é padrão e não pode ser removida.")
                    context.user_data["mydata_step"] = "accounts_menu"
                    await my_data(update, context)
                    return
                # evitar remover conta com saldo
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
                return
            elif choice == "voltar":
                context.user_data["mydata_step"] = "accounts_menu"
                await my_data(update, context)
                return
            else:
                await update.message.reply_text("Escolha inválida. Use o menu.")
            return

        # adicionar valor
        if context.user_data.get("mydata_step") == "add_account_value":
            acc_id = context.user_data.get("editing_account_id")
            acc = await session.get(Account, acc_id)
            try:
                amount = parse_amount(text)
                if amount <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("Valor inválido.")
                return
            before = float(to_decimal(acc.balance))
            tx = Transaction(
                account_id=acc.id,
                profile_id=profile.id,
                type=TransactionType.ENTRADA,
                value=float(amount),
                date=datetime.date.today(),
                description="Entrada adicionada",
                balance_before=before,
                is_transfer=False,
            )
            acc.balance = float(Decimal(before) + amount)
            session.add(tx)
            await session.commit()
            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(f"Entrada registrada em {acc.name}.", reply_markup=ReplyKeyboardRemove())
            await my_data(update, context)
            return

        # remover valor
        if context.user_data.get("mydata_step") == "remove_account_value":
            acc_id = context.user_data.get("editing_account_id")
            acc = await session.get(Account, acc_id)
            try:
                amount = parse_amount(text)
                if amount <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("Valor inválido.")
                return
            before = float(to_decimal(acc.balance))
            if Decimal(before) < amount:
                await update.message.reply_text("Saldo insuficiente no item.")
                return
            tx = Transaction(
                account_id=acc.id,
                profile_id=profile.id,
                type=TransactionType.SAIDA,
                value=float(amount),
                date=datetime.date.today(),
                description="Retirada manual",
                balance_before=before,
                is_transfer=False,
            )
            acc.balance = float(Decimal(before) - amount)
            session.add(tx)
            await session.commit()
            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(f"Retirada registrada em {acc.name}.", reply_markup=ReplyKeyboardRemove())
            await my_data(update, context)
            return

        # renomear conta/cartão
        if context.user_data.get("mydata_step") == "rename_account":
            acc_id = context.user_data.get("editing_account_id")
            acc = await session.get(Account, acc_id)
            new_name = text.strip()
            if not new_name:
                await update.message.reply_text("Nome inválido.")
                return
            scope = getattr(acc, "type", ACCOUNT_TYPE_ACCOUNT)
            if new_name.lower() in DEFAULT_ACCOUNTS and scope == ACCOUNT_TYPE_ACCOUNT and acc.name.lower() not in DEFAULT_ACCOUNTS:
                await update.message.reply_text("Esse nome é reservado. Escolha outro.")
                return
            acc.name = new_name
            await session.commit()
            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(f"Renomeado para '{acc.name}'.", reply_markup=ReplyKeyboardRemove())
            await my_data(update, context)
            return

        # ---------- transferências (sem distinção entre contas/cartões) ----------
        if context.user_data.get("mydata_step") == "transfer_from":
            src_name = text.strip()
            # tentar resolver pelo texto enviado (usuário pode ter escolhido do teclado)
            src = next((a for a in all_accounts if a.name.lower() == src_name.lower()), None)
            if src:
                context.user_data["transfer_from_id"] = src.id
                context.user_data["mydata_step"] = "transfer_to"
                # agora mostra os destinos excluindo a origem
                dst_options = [[a.name] for a in all_accounts if a.id != src.id] + [["Voltar"]]
                await update.message.reply_text(
                    f"Origem: {src.name}. Escolha a conta de DESTINO:",
                    reply_markup=ReplyKeyboardMarkup(dst_options, one_time_keyboard=True, resize_keyboard=True),
                )
                return

            if src_name.lower() == "voltar":
                context.user_data["mydata_step"] = "show_summary"
                await my_data(update, context)
                return

            # se não encontrou, reexibe o teclado para ajudar o usuário
            options = [[a.name] for a in all_accounts] + [["Voltar"]]
            await update.message.reply_text(
                "Conta de origem não encontrada. Escolha na lista:",
                reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True),
            )
            return

        if context.user_data.get("mydata_step") == "transfer_to":
            dst_name = text.strip()
            src_id = context.user_data.get("transfer_from_id")
            src = await session.get(Account, src_id) if src_id else None

            dst = next((a for a in all_accounts if a.name.lower() == dst_name.lower()), None)
            if dst:
                if src and dst.id == src.id:
                    await update.message.reply_text("Origem e destino não podem ser a mesma conta.")
                    # reexibe destinos sem a origem
                    dst_options = [[a.name] for a in all_accounts if a.id != (src.id if src else None)] + [["Voltar"]]
                    await update.message.reply_text("Escolha outro destino:", reply_markup=ReplyKeyboardMarkup(dst_options, one_time_keyboard=True, resize_keyboard=True))
                    return
                context.user_data["transfer_to_id"] = dst.id
                context.user_data["mydata_step"] = "transfer_amount"
                await update.message.reply_text("Digite o valor da transferência:", reply_markup=ReplyKeyboardRemove())
                return

            if dst_name.lower() == "voltar":
                context.user_data["mydata_step"] = "show_summary"
                await my_data(update, context)
                return

            # se não encontrou, reexibe os destinos, excluindo a origem
            dst_options = [[a.name] for a in all_accounts if a.id != (src.id if src else None)] + [["Voltar"]]
            await update.message.reply_text(
                "Conta de destino não encontrada. Escolha na lista:",
                reply_markup=ReplyKeyboardMarkup(dst_options, one_time_keyboard=True, resize_keyboard=True),
            )
            return


        if context.user_data.get("mydata_step") == "transfer_amount":
            try:
                amount = parse_amount(text)
                if amount <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("Valor inválido.")
                return

            src_id = context.user_data.get("transfer_from_id")
            dst_id = context.user_data.get("transfer_to_id")
            src = await session.get(Account, src_id)
            dst = await session.get(Account, dst_id)

            before_src = float(to_decimal(src.balance))
            before_dst = float(to_decimal(dst.balance))

            if Decimal(before_src) < amount:
                await update.message.reply_text("Saldo insuficiente na conta de origem.")
                return

            # criar transação de saída na origem
            tx_out = Transaction(
                account_id=src.id,
                profile_id=profile.id,
                type=TransactionType.SAIDA,
                value=float(amount),
                date=datetime.date.today(),
                description=f"Pagamento/cartão -> {dst.name}",
                balance_before=before_src,
                is_transfer=True,
            )

            # criar transação de entrada no destino (representa o recebimento no cartão)
            tx_in = Transaction(
                account_id=dst.id,
                profile_id=profile.id,
                type=TransactionType.ENTRADA,
                value=float(amount),
                date=datetime.date.today(),
                description=f"Recebimento de pagamento de {src.name}",
                balance_before=before_dst,
                is_transfer=True,
            )


            session.add_all([tx_out, tx_in])
            # gera ids sem commitar
            await session.flush()

            # agora alocar o pagamento para "quitar" despesas existentes no destino (cartão)
            amount_left = Decimal(amount)
            # buscar transações do cartão que são despesas (não transfer) e sem settlement
            # construímos condições dinamicamente para evitar AttributeError caso os campos não existam
            conditions = [
                Transaction.account_id == dst.id,
                Transaction.is_transfer == False,
            ]
            if hasattr(Transaction, "type"):
                conditions.append(Transaction.type == TransactionType.SAIDA)
            # unsettled
            if hasattr(Transaction, "is_settled"):
                conditions.append(Transaction.is_settled == False)
            elif hasattr(Transaction, "settlement_id"):
                conditions.append(Transaction.settlement_id == None)
            else:
                # fallback on description not containing PAID_BY:
                conditions.append(~Transaction.description.like("%PAID_BY:%"))

            q = select(Transaction).where(*conditions).order_by(Transaction.date)
            res = await session.execute(q)
            unpaid = res.scalars().all()

            for u in unpaid:
                if amount_left <= 0:
                    break
                u_value = to_decimal(u.value)
                if amount_left >= u_value:
                    # quita a despesa totalmente
                    if hasattr(u, "settlement_id"):
                        u.settlement_id = tx_in.id
                    if hasattr(u, "is_settled"):
                        u.is_settled = True
                    if not hasattr(u, "settlement_id") and not hasattr(u, "is_settled"):
                        # fallback: anotar na description
                        u.description = (u.description or "") + f" PAID_BY:{tx_in.id}"
                    amount_left -= u_value
                else:
                    # parcial: para evitar complexidade agora, não alteramos parcialmente
                    break

            # atualizar saldos já calculados
            src.balance = float(Decimal(before_src) - amount)
            dst.balance = float(Decimal(before_dst) + amount)
            await session.commit()

            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(f"Transferência de {format_money(amount)} de {src.name} para {dst.name} realizada.", reply_markup=ReplyKeyboardRemove())
            await my_data(update, context)
            return

        # ---------- fluxo de dívidas (inalterado) ----------
        if context.user_data.get("mydata_step") == "edit_debts_menu":
            options = [[d.creditor] for d in profile.debts] + [["Adicionar Dívida"], ["Voltar"]]
            await update.message.reply_text("Escolha uma dívida para editar, ou 'Adicionar Dívida' para criar:", 
                                            reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            context.user_data["mydata_step"] = "select_debt"
            return

        if context.user_data.get("mydata_step") == "select_debt":
            choice = text.strip()
            if choice.lower() == "adicionar dívida":
                context.user_data["mydata_step"] = "add_debt_name"
                await update.message.reply_text("Digite o nome do credor da nova dívida:")
                return
            if choice.lower() == "voltar":
                context.user_data["mydata_step"] = "show_summary"
                await my_data(update, context)
                return
            # editar dívida existente
            debt = next((d for d in profile.debts if d.creditor.lower() == choice.lower()), None)
            if not debt:
                await update.message.reply_text("Dívida não encontrada. Escolha do menu.")
                return
            context.user_data["editing_debt_id"] = debt.id
            options = [["Editar Valor Mensal", "Editar Meses"], ["Remover Dívida"], ["Voltar"]]
            context.user_data["mydata_step"] = "debt_action"
            await update.message.reply_text(f"O que deseja fazer com a dívida '{debt.creditor}'?", 
                                            reply_markup=ReplyKeyboardMarkup(options, one_time_keyboard=True, resize_keyboard=True))
            return

        if context.user_data.get("mydata_step") == "debt_action":
            debt_id = context.user_data.get("editing_debt_id")
            debt = await session.get(Debt, debt_id)
            choice = text.strip().lower()
            
            if choice == "editar valor mensal":
                context.user_data["mydata_step"] = "edit_debts_monthly"
                await update.message.reply_text(f"Valor atual: {format_money(debt.monthly_payment)} Digite o novo valor mensal:")
            elif choice == "editar meses":
                context.user_data["mydata_step"] = "edit_debts_months"
                await update.message.reply_text(f"Meses atuais: {debt.months} Digite o novo número de meses:")
            elif choice == "remover dívida":
                await session.delete(debt)
                await session.commit()
                context.user_data["mydata_step"] = "show_summary"
                await update.message.reply_text(f"Dívida '{debt.creditor}' removida.", reply_markup=ReplyKeyboardRemove())
                await my_data(update, context)
            elif choice == "voltar":
                context.user_data["mydata_step"] = "show_summary"
                await my_data(update, context)
            else:
                await update.message.reply_text("Escolha inválida. Use o menu.")
            return

        # ---------- adicionar dívida ----------
        if context.user_data.get("mydata_step") == "add_debt_name":
            creditor_name = text.strip()
            # não atribuir total_amount: modelo possui total_amount como property somente leitura
            debt = Debt(profile_id=profile.id, creditor=creditor_name, monthly_payment=0.0, months=0, status=DebtStatus.OPEN)
            session.add(debt)
            await session.commit()
            await session.refresh(debt)
            context.user_data["editing_debt_id"] = debt.id
            context.user_data["mydata_step"] = "edit_debts_monthly"
            await update.message.reply_text(f"Digite o valor mensal da dívida de {debt.creditor}:")
            return


        # ---------- editar valor mensal ----------
        if context.user_data.get("mydata_step") == "edit_debts_monthly":
            debt_id = context.user_data.get("editing_debt_id")
            debt = await session.get(Debt, debt_id)
            try:
                monthly = parse_amount(text)
                if monthly <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("Valor inválido. Digite novamente.")
                return
            debt.monthly_payment = float(monthly)
            await session.commit()
            context.user_data["mydata_step"] = "edit_debts_months"
            await update.message.reply_text("Quantos meses será usado esse valor para calcular o total?")
            return


        # ---------- editar meses ----------
        if context.user_data.get("mydata_step") == "edit_debts_months":
            debt_id = context.user_data.get("editing_debt_id")
            debt = await session.get(Debt, debt_id)
            try:
                months = int(text)
                if months <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text("Número inválido. Digite novamente.")
                return

            # atualiza meses (não tocar em total_amount)
            debt.months = int(months)
            await session.commit()

            # calcula total apenas para exibição
            total_to_show = to_decimal(debt.monthly_payment) * Decimal(debt.months)
            context.user_data["mydata_step"] = "show_summary"
            await update.message.reply_text(
                f"Dívida de {debt.creditor} registrada/atualizada: {format_money(total_to_show)} ({debt.months} meses)",
                reply_markup=ReplyKeyboardRemove()
            )
            await my_data(update, context)
            return
