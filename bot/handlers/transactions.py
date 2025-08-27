from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from sqlalchemy import select
import datetime


from db.session import get_session
from db.models import (
Transaction, Category, TransactionType, CategoryType,
Account, Profile, Debt, DebtStatus, DebtType, CurrencyEnum
)
from db.auth import auth

async def add_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    context.user_data.setdefault("profile_id", profile.id)
    raw = update.message.text.strip()
    text = raw.lower()

    if "step" not in context.user_data:
        context.user_data["step"] = "type"
        await update.message.reply_text(
            "Você quer registrar uma *entrada* ou *saída*?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["entrada", "saída"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return

    # 1) tipo
    if context.user_data["step"] == "type":
        if text not in ("entrada", "saída", "saida"):
            await update.message.reply_text("Por favor, responda apenas com 'entrada' ou 'saída'.")
            return

        context.user_data["type"] = "saida" if text in ("saída", "saida") else "entrada"

        if context.user_data["type"] == "saida":
            context.user_data["step"] = "is_debt_payment"
            await update.message.reply_text(
                "Essa saída é pagamento de *dívida*? (sim/não)",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True)
            )
            return

        # entrada -> pede valor
        context.user_data["step"] = "value"
        await update.message.reply_text("Qual o valor da entrada?", reply_markup=ReplyKeyboardRemove())
        return

    # 2) is debt payment?
    if context.user_data["step"] == "is_debt_payment":
        if text not in ("sim", "não", "nao"):
            await update.message.reply_text("Responda 'sim' ou 'não'.")
            return

        if text in ("sim",):
            # pergunta o tipo de dívida
            context.user_data["step"] = "debt_type"
            await update.message.reply_text(
                "Qual tipo de dívida é?\n- cartão\n- dívida comum",
                reply_markup=ReplyKeyboardMarkup([["cartão", "dívida comum"]], one_time_keyboard=True, resize_keyboard=True)
            )
            return
        else:
            # não é dívida, vai direto para valor da saída
            context.user_data["step"] = "value"
            await update.message.reply_text("Qual o valor da saída?", reply_markup=ReplyKeyboardRemove())
            return
    
    # 3) tipo de dívida
    if context.user_data["step"] == "debt_type":
        if text not in ("cartão", "dívida comum"):
            await update.message.reply_text("Responda 'cartão' ou 'dívida comum'.")
            return

        context.user_data["debt_kind"] = text

        if text == "cartão":
            # fluxo do cartão
            async with get_session() as session:
                result = await session.execute(
                    select(Account).where(Account.profile_id == profile.id, Account.type == "credit_card").order_by(Account.name)
                )
                cards = result.scalars().all()

                if not cards:
                    await update.message.reply_text("Nenhum cartão encontrado. Por favor, cadastre um cartão antes.")
                    context.user_data.clear()
                    return

                display_lines = []   # linhas para mostrar ao usuário
                keyboard = []        # botões com índices (texto enviado será apenas o número)
                available = {}       # map id -> (account_id, "card", invoice_total)
                today = datetime.date.today()

                idx = 1
                # IMPORTANT: agora não filtramos por mês — usamos todas as transações não liquidadas
                for c in cards:
                    result = await session.execute(
                        select(Transaction).where(
                            Transaction.account_id == c.id,
                            Transaction.is_settled == False    # só transações ainda não liquidadas
                        )
                    )
                    txs = result.scalars().all()
                    invoice_total = 0.0

                    for tx in txs:
                        if tx.value is None or tx.value >= 0:
                            continue
                        desc = (tx.description or "").lower()

                        # se é parcelado, somamos o monthly_payment dos Debts vinculados
                        if "parcelado" in desc:
                            debt_result = await session.execute(
                                select(Debt)
                                .where(Debt.profile_id == c.profile_id)
                                .where(Debt.creditor.ilike(f"%#{tx.id}"))
                                .where(Debt.type == DebtType.PARCELADO)
                                .where(Debt.months > 0)
                            )
                            linked = debt_result.scalars().all()
                            if linked:
                                for ln in linked:
                                    # soma apenas a parcela mensal (não o total da transação)
                                    if ln.monthly_payment is not None:
                                        invoice_total += float(ln.monthly_payment)
                                    else:
                                        invoice_total += -tx.value
                            else:
                                # se não houver Debt vinculado, trata como compra normal
                                invoice_total += -tx.value
                        else:
                            # compra não parcelada — soma o valor inteiro
                            invoice_total += -tx.value

                    invoice_total = round(invoice_total, 2)
                    if invoice_total > 0:
                        label = f"{idx} — Cartão: {c.name} — R$ {invoice_total:.2f}"
                        display_lines.append(label)
                        keyboard.append([str(idx)])               # botão: apenas o índice
                        available[str(idx)] = (c.id, "card", invoice_total)
                        idx += 1

            # fora do 'with': display_lines & available populados
            if not display_lines:
                await update.message.reply_text(
                    "Nenhuma fatura de cartão encontrada. Vamos tratar como despesa normal.",
                    reply_markup=ReplyKeyboardRemove()
                )
                context.user_data["step"] = "value"
                await update.message.reply_text("Qual o valor da saída?", reply_markup=ReplyKeyboardRemove())
                return

            context.user_data["available_debts"] = available
            context.user_data["step"] = "choose_debt"
            msg = "Selecione a dívida/cartão que está pagando:\n" + "\n".join(display_lines)
            await update.message.reply_text(msg,
                                            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
            return

        else:
            # fluxo dívida comum (apenas REAL)
            async with get_session() as session:
                result = await session.execute(
                    select(Debt).where(Debt.profile_id == profile.id, Debt.months > 0, Debt.type == DebtType.REAL).order_by(Debt.creditor)
                )
                debts = result.scalars().all()

            if not debts:
                await update.message.reply_text(
                    "Nenhuma dívida comum encontrada. Vamos tratar como despesa normal.",
                    reply_markup=ReplyKeyboardRemove()
                )
                context.user_data["step"] = "value"
                await update.message.reply_text("Qual o valor da saída?", reply_markup=ReplyKeyboardRemove())
                return

            display_lines = []
            keyboard = []
            available = {}
            idx = 1
            for d in debts:
                label = f

    # 4) escolha da dívida/cartão
    if context.user_data["step"] == "choose_debt":
        chosen = raw.strip()
        debt_map = context.user_data.get("available_debts", {})
        mapped = None

        # aceita número (mapa por índice) ou a label completa (fallback)
        if chosen.isdigit():
            mapped = debt_map.get(chosen)
        else:
            mapped = debt_map.get(chosen.lower())

        if not mapped:
            await update.message.reply_text("Dívida/cartão não encontrado. Digite o número mostrado (ex: '1').")
            return

        id_or_card, kind = mapped[0], mapped[1]
        # se houver invoice_total para cartões, guardamos também
        if kind == "card":
            context.user_data["debt_card_invoice_total"] = mapped[2]

        context.user_data["debt_selected_id"] = id_or_card
        context.user_data["debt_kind"] = kind

        if kind == "card":
            context.user_data["step"] = "confirm_card_payment"
            await update.message.reply_text("Deseja pagar o valor da fatura do mês inteiro? (sim/não)",
                                            reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True,
                                                                            resize_keyboard=True))
            return
        else:
            context.user_data["step"] = "debt_pay_choice"
            await update.message.reply_text("Você quer pagar apenas a parcela atual ou adiantar parcelas?",
                                            reply_markup=ReplyKeyboardMarkup([["parcela atual", "adiantar parcelas"]],
                                                                            one_time_keyboard=True, resize_keyboard=True))
            return
        
    # 4) confirma pagamento cartão
    if context.user_data.get("step") == "confirm_card_payment":
        if text not in ("sim", "não", "nao"):
            await update.message.reply_text("Responda 'sim' ou 'não'.")
            return
        if text in ("sim",):
            context.user_data["is_debt_payment"] = True
            context.user_data["debt_is_card"] = True
            context.user_data["debt_card_account_id"] = context.user_data.get("debt_selected_id")
            context.user_data["debt_advance_total"] = context.user_data.get("debt_card_invoice_total")
            context.user_data["value"] = float(context.user_data.get("debt_card_invoice_total") or 0)
            context.user_data["step"] = "description"
            await update.message.reply_text(
                f"✅ Valor definido: R$ {context.user_data.get('debt_card_invoice_total'):.2f}. Por favor, descreva a saída (opcional):",
                reply_markup=ReplyKeyboardRemove()
            )
            return
        else:
            # usuário optou por não pagar a fatura agora
            await update.message.reply_text("Operação cancelada. Se quiser, tente novamente mais tarde.")
            context.user_data.clear()
            return

    # 5) debt advance months
    if context.user_data["step"] == "debt_advance_months":
        try:
            months_to_pay = int(text)
            if months_to_pay <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Por favor, insira um número inteiro maior que zero.")
            return
        context.user_data["debt_advance_months_num"] = months_to_pay
        context.user_data["step"] = "debt_advance_total"
        await update.message.reply_text("Qual o VALOR TOTAL que será pago nesse adiantamento? (Ex: 1234.56)", reply_markup=ReplyKeyboardRemove())
        return

    # 6) debt advance total (usado para dívidas e pagamentos de cartão)
    if context.user_data["step"] == "debt_advance_total":
        try:
            total_value = float(text.replace(",", "."))
            if total_value <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Por favor, insira um valor válido maior que zero (ex: 1234.56).")
            return

        # Se veio de cartão manual (fluxos antigos), tratar de forma simples: setar valor e ir para descrição
        if context.user_data.get("debt_kind") == "card":
            context.user_data["is_debt_payment"] = True
            context.user_data["debt_is_card"] = True
            context.user_data["debt_card_account_id"] = context.user_data.get("debt_selected_id")
            context.user_data["debt_paid_months"] = 1
            context.user_data["debt_advance_total"] = total_value
            context.user_data["value"] = float(total_value)
            context.user_data["step"] = "description"

            await update.message.reply_text(
                f"✅ Registrado pagamento de fatura do cartão, total: R$ {total_value:.2f}. Por favor, descreva a saída (opcional):",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        # Flows para dívidas tradicionais
        months_to_pay = context.user_data.get("debt_paid_months") or context.user_data.get("debt_advance_months_num")
        if not months_to_pay:
            await update.message.reply_text("Dados da dívida ausentes. Recomece a operação.")
            context.user_data.clear()
            return

        context.user_data["debt_paid_months"] = months_to_pay
        context.user_data["debt_advance_total"] = total_value
        context.user_data["is_debt_payment"] = True
        context.user_data["value"] = float(total_value)
        context.user_data["step"] = "description"

        await update.message.reply_text(
            f"✅ Registrado pagamento adiantado de {months_to_pay} parcela(s), total: R$ {total_value:.2f}. Por favor, descreva a saída (opcional):",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # 7) value manual (entrada ou saída normal)
    if context.user_data["step"] == "value":
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Por favor, insira um número válido.")
            return

        context.user_data["value"] = value

        if context.user_data["type"] == "entrada":
            context.user_data["category"] = None
            context.user_data["step"] = "description"
            await update.message.reply_text("Por favor, descreva a entrada (opcional):", reply_markup=ReplyKeyboardRemove())
            return

        # SAIDA normal -> perguntar se foi no cartão
        context.user_data["step"] = "used_card"
        await update.message.reply_text(
            "Essa saída foi feita no *cartão de crédito*? (sim/não)",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return

    # 8) used_card flow: escolher/ criar cartão ou seguir para categoria
    if context.user_data["step"] == "used_card":
        if text not in ("sim", "não", "nao"):
            await update.message.reply_text("Responda 'sim' ou 'não'.")
            return

        if text in ("sim",):
            # listar cartões existentes
            async with get_session() as session:
                result = await session.execute(
                    select(Account).where(Account.profile_id == profile.id).where(Account.type == "credit_card").order_by(Account.name)
                )
                cards = result.scalars().all()

            if not cards:
                context.user_data["step"] = "create_card_name"
                await update.message.reply_text("Nenhum cartão cadastrado. Digite o NOME do novo cartão para criar:", reply_markup=ReplyKeyboardRemove())
                return

            keyboard = [[c.name] for c in cards] + [["Criar novo cartão"]]
            context.user_data["step"] = "choose_card"
            await update.message.reply_text(
                "Escolha um cartão existente ou crie um novo:\n" + "\n".join(f"- {c.name}" for c in cards),
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return

        # não foi no cartão -> seguir para seleção/criação de categoria
        context.user_data["step"] = "category"
        # re-use category listing logic by fetching categories now
        async with get_session() as session:
            result = await session.execute(select(Category).where(Category.profile_id == profile.id).order_by(Category.name))
            categories = result.scalars().all()

        if not categories:
            await update.message.reply_text("Nenhuma categoria cadastrada ainda. Digite o nome da nova categoria para a saída:")
            context.user_data["step"] = "new_category"
            return

        list_txt = "Escolha uma categoria existente ou digite uma nova:\n" + "\n".join(f"- {c.name}" for c in categories)
        keyboard = [[c.name] for c in categories]
        context.user_data["step"] = "category"
        await update.message.reply_text(
            list_txt,
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return

    # 8b) criar novo cartão: receber nome
    if context.user_data["step"] == "create_card_name":
        name = raw.strip()
        if not name:
            await update.message.reply_text("Nome inválido. Digite o nome do cartão:")
            return
        # cria cartão
        async with get_session() as session:
            card = Account(profile_id=profile.id, name=name, type="credit_card", balance=0.0, currency=CurrencyEnum.BRL)
            session.add(card)
            await session.flush()
            await session.commit()
            await session.refresh(card)

        context.user_data["card_account_id"] = card.id
        # Depois de criar o cartão, perguntar se a compra será parcelada
        context.user_data["step"] = "card_installments_query"
        await update.message.reply_text("Essa compra será *parcelada*? (sim/não)", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True))
        return

    # 8c) choose existing card
    if context.user_data["step"] == "choose_card":
        if raw.strip().lower() == "criar novo cartão":
            context.user_data["step"] = "create_card_name"
            await update.message.reply_text("Digite o NOME do novo cartão:", reply_markup=ReplyKeyboardRemove())
            return

        chosen = raw.strip()
        async with get_session() as session:
            result = await session.execute(select(Account).where(Account.profile_id == profile.id).where(Account.type == "credit_card").where(Account.name.ilike(chosen)))
            card = result.scalar_one_or_none()
        if not card:
            await update.message.reply_text("Cartão não encontrado. Digite o nome exatamente como mostrado ou escolha 'Criar novo cartão'.")
            return

        context.user_data["card_account_id"] = card.id
        # Depois de escolher o cartão, perguntar se a compra será parcelada
        context.user_data["step"] = "card_installments_query"
        await update.message.reply_text("Essa compra será *parcelada*? (sim/não)", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True))
        return

    # 8d) flow para parcelamento do cartão
    if context.user_data["step"] == "card_installments_query":
        if text not in ("sim", "não", "nao"):
            await update.message.reply_text("Responda 'sim' ou 'não'.")
            return
        if text in ("sim",):
            context.user_data["step"] = "card_installments_number"
            await update.message.reply_text("Em quantas parcelas? (digite um número inteiro)", reply_markup=ReplyKeyboardRemove())
            return
        else:
            # não parcelado
            context.user_data["card_installments"] = 1
            context.user_data["step"] = "category"
            # pedir categoria em seguida
            async with get_session() as session:
                result = await session.execute(select(Category).where(Category.profile_id == profile.id).order_by(Category.name))
                categories = result.scalars().all()

            if not categories:
                await update.message.reply_text("Nenhuma categoria cadastrada ainda. Digite o nome da nova categoria para a saída:")
                context.user_data["step"] = "new_category"
                return

            list_txt = "Escolha uma categoria existente ou digite uma nova:\n" + "\n".join(f"- {c.name}" for c in categories)
            keyboard = [[c.name] for c in categories]
            await update.message.reply_text(
                list_txt,
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return

    if context.user_data["step"] == "card_installments_number":
        try:
            n = int(text)
            if n <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Por favor, insira um número inteiro maior que zero.")
            return
        context.user_data["card_installments"] = n
        context.user_data["step"] = "category"
        # pedir categoria em seguida
        async with get_session() as session:
            result = await session.execute(select(Category).where(Category.profile_id == profile.id).order_by(Category.name))
            categories = result.scalars().all()

        if not categories:
            await update.message.reply_text("Nenhuma categoria cadastrada ainda. Digite o nome da nova categoria para a saída:")
            context.user_data["step"] = "new_category"
            return

        list_txt = "Escolha uma categoria existente ou digite uma nova:\n" + "\n".join(f"- {c.name}" for c in categories)
        keyboard = [[c.name] for c in categories]
        await update.message.reply_text(
            list_txt,
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return

    # 8e) category flow: seleção/criação de categorias para SAÍDA (mantido como antes)
    if context.user_data["step"] in ("category", "new_category", "category_type"):
        # Só deve ocorrer para saídas
        if context.user_data.get("type") != "saida":
            await update.message.reply_text("Categorias só são usadas para saídas. Reinicie a operação se necessário.")
            context.user_data.clear()
            return

        # 8a) Usuário escolheu uma categoria existente ou digitou uma nova (etapa "category")
        if context.user_data["step"] == "category":
            chosen = raw.strip()
            async with get_session() as session:
                result = await session.execute(
                    select(Category).where(Category.profile_id == profile.id).where(Category.name.ilike(chosen))
                )
                category = result.scalar_one_or_none()

            if category:
                # categoria existente selecionada
                context.user_data["category"] = category.name
                context.user_data["category_id"] = category.id
                context.user_data["step"] = "description"
                await update.message.reply_text("Categoria selecionada. Por favor, descreva a saída (opcional):", reply_markup=ReplyKeyboardRemove())
                return

            # não encontrou: tratar como criação de nova categoria
            context.user_data["new_category_name"] = chosen
            context.user_data["step"] = "category_type"
            await update.message.reply_text(
                f"Criar nova categoria '{chosen}'. Ela é *fixa* ou *variável*?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["fixa", "variavel"]], one_time_keyboard=True, resize_keyboard=True)
            )
            return

        # 8b) Etapa quando não havia categorias cadastradas ("new_category")
        if context.user_data["step"] == "new_category":
            new_name = raw.strip()
            if not new_name:
                await update.message.reply_text("Nome de categoria inválido. Digite um nome válido para a nova categoria:")
                return
            context.user_data["new_category_name"] = new_name
            context.user_data["step"] = "category_type"
            await update.message.reply_text(
                f"Criar nova categoria '{new_name}'. Ela é *fixa* ou *variável*?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["fixa", "variavel"]], one_time_keyboard=True, resize_keyboard=True)
            )
            return

        # 8c) Escolha do tipo da nova categoria ("category_type")
        if context.user_data["step"] == "category_type":
            # aceitar 'variavel' e 'variável'
            valid = {"fixa", "variavel", "variável"}
            if text not in valid:
                await update.message.reply_text("Responda 'fixa' ou 'variavel'.")
                return

            new_name = context.user_data.get("new_category_name")
            if not new_name:
                await update.message.reply_text("Nome da nova categoria ausente. Recomece a operação.")
                context.user_data.clear()
                return

            chosen_type = CategoryType.FIXA if text == "fixa" else CategoryType.VARIAVEL

            # Cria a categoria se não existir (case-insensitive)
            async with get_session() as session:
                result = await session.execute(
                    select(Category).where(Category.profile_id == profile.id).where(Category.name.ilike(new_name))
                )
                existing = result.scalar_one_or_none()
                if existing:
                    category = existing
                else:
                    category = Category(profile_id=profile.id, name=new_name, type=chosen_type)
                    session.add(category)
                    await session.flush()
                # garante persistência antes de abrir nova sessão no save
                await session.commit()

            context.user_data["category"] = category.name
            context.user_data["category_id"] = category.id
            context.user_data["step"] = "description"
            await update.message.reply_text(f"Categoria '{category.name}' criada/selecionada. Por favor, descreva a saída (opcional):", reply_markup=ReplyKeyboardRemove())
            return

    # 9) description -> choose account (ou pular se foi no cartão)
    if context.user_data["step"] == "description":
        context.user_data["description"] = raw

        # Se a compra foi no cartão e já temos o card_account_id, pular escolha de conta
        if context.user_data.get("card_account_id"):
            context.user_data["account_id"] = context.user_data.get("card_account_id")
            await save_transaction(update, context)
            context.user_data.clear()
            return

        # Senão, pedir conta normalmente
        context.user_data["step"] = "choose_account"
        async with get_session() as session:
            result = await session.execute(select(Account).where(Account.profile_id == profile.id).where(Account.type == 'bank').order_by(Account.name))
            accounts = result.scalars().all()
        if not accounts:
            await update.message.reply_text("Nenhuma conta cadastrada ainda. Por favor, crie uma conta antes.")
            return
        keyboard = [[a.name] for a in accounts]
        list_txt = "Em qual conta foi feita a movimentação?\n" + "\n".join(f"- {a.name}: {a.currency.value} {a.balance:.2f}" for a in accounts)
        await update.message.reply_text(list_txt, reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
        return

    # 10) choose account -> save
    if context.user_data["step"] == "choose_account":
        account_name = text
        async with get_session() as session:
            result = await session.execute(select(Account).where(Account.name.ilike(account_name)).where(Account.profile_id == profile.id))
            account = result.scalar_one_or_none()
        if not account:
            await update.message.reply_text("Conta não encontrada. Por favor, escolha uma conta válida.")
            return
        context.user_data["account_id"] = account.id
        await save_transaction(update, context)
        context.user_data.clear()
        return



async def save_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_type = context.user_data.get("type")
    value = context.user_data.get("value")
    category_name = context.user_data.get("category")
    account_category_id = context.user_data.get("category_id")
    description = context.user_data.get("description", "")
    account_id = context.user_data.get("account_id")
    profile_id = context.user_data.get("profile_id")
    today = datetime.date.today()

    await update.message.reply_text("⌛ Salvando...")

    async with get_session() as session:
        profile = await session.get(Profile, profile_id)
        if not profile:
            await update.message.reply_text("⚠️ Perfil não encontrado. Operação cancelada.")
            return

        # Inicializa variáveis
        debt_info_line = ""
        category = None
        value_to_use = value

        # Tratamento de dívida (inclui pagamentos de fatura de cartão)
        if context.user_data.get("is_debt_payment"):
            # Se pagamento de cartão
            if context.user_data.get("debt_is_card"):
                card_account_id = context.user_data.get("debt_card_account_id")
                paid_total = context.user_data.get("debt_advance_total") or value

                # Não alteramos 'Debt' aqui; apenas guardamos o total que será usado
                value_to_use = paid_total
                debt_info_line = f"🔁 Pagamento de fatura do cartão: R$ {paid_total:.2f}"

            else:
                # pagamento de dívida comum
                debt_id = context.user_data.get("debt_selected_id")
                months_to_pay = context.user_data.get("debt_paid_months") or context.user_data.get("debt_advance_months_num")
                paid_total = context.user_data.get("debt_advance_total") or value

                debt = await session.get(Debt, debt_id)
                if not debt or debt.profile_id != profile.id:
                    await update.message.reply_text("⚠️ Dívida inválida. Operação cancelada.")
                    return

                remaining_before = debt.months
                debt.months = max(0, debt.months - months_to_pay)
                # apagar se zerou (você pediu que a dívida seja removida quando finalizada)
                if debt.months == 0:
                    await session.delete(debt)
                else:
                    session.add(debt)

                # Define categoria automaticamente
                category_name_default = "Pagamento de Dívida"
                category_name_advance = "Pagamento Antecipado de Dívida"

                if context.user_data.get("debt_advance_total"):
                    chosen_category_name = category_name_advance
                    chosen_category_type = CategoryType.VARIAVEL
                else:
                    chosen_category_name = category_name_default
                    chosen_category_type = CategoryType.FIXA

                # Busca ou cria categoria
                result = await session.execute(
                    select(Category).where(Category.profile_id == profile.id).where(Category.name == chosen_category_name)
                )
                category = result.scalar_one_or_none()
                if not category:
                    category = Category(profile_id=profile.id, name=chosen_category_name, type=chosen_category_type)
                    session.add(category)
                    await session.flush()

                value_to_use = paid_total

                # Calcula desconto/acréscimo
                expected_total = float(debt.monthly_payment) * months_to_pay if getattr(debt, "monthly_payment", None) is not None else 0
                diff = expected_total - float(paid_total)
                pct = (abs(diff) / expected_total * 100) if expected_total > 0 else 0.0
                if diff > 0.0001:
                    diff_line = f"💸 Desconto: R$ {diff:.2f} ({pct:.1f}% do esperado)"
                elif diff < -0.0001:
                    diff_line = f"⚠️ Acréscimo: R$ {abs(diff):.2f} ({pct:.1f}% acima do esperado)"
                else:
                    diff_line = "✅ Sem desconto nem acréscimo (valor igual ao esperado)."

                debt_info_line = (
                    f"🔁 Pagamento de dívida: {months_to_pay} mês(es) abatidos. "
                    f"Meses antes: {remaining_before}, depois: {debt.months if 'debt' in locals() and debt is not None else 0}. {diff_line}"
                )

        # Para SAÍDAS normais com categoria definida
        elif t_type == "saida":
            # Preferir category_id salvo durante o fluxo de seleção/criação
            if account_category_id:
                result = await session.execute(
                    select(Category).where(Category.id == account_category_id).where(Category.profile_id == profile.id)
                )
                category = result.scalar_one_or_none()
            if not category and category_name:
                result = await session.execute(
                    select(Category)
                    .where(Category.name.ilike(category_name))
                    .where(Category.profile_id == profile.id)
                )
                category = result.scalar_one_or_none()
            if not category:
                await update.message.reply_text("⚠️ Categoria não encontrada. Por favor, crie a categoria antes.")
                return

        # Busca conta (conta onde será registrada a transação fornecida pelo usuário)
        result = await session.execute(
            select(Account)
            .where(Account.id == account_id)
            .where(Account.profile_id == profile.id)
        )
        account = result.scalar_one_or_none()
        if not account:
            await update.message.reply_text("⚠️ Conta não encontrada. Operação cancelada.")
            return

        # tx_value: entradas positivas, saídas negativas
        tx_value = value_to_use if t_type == "entrada" else -value_to_use

        # Calcula balance_before para a conta escolhida
        result = await session.execute(
            select(Transaction)
            .where(Transaction.account_id == account.id)
            .where(Transaction.date == today)
            .order_by(Transaction.id.desc())
        )
        last_tx_today = result.scalars().first()
        balance_before = (last_tx_today.balance_before + last_tx_today.value) if last_tx_today else (account.balance or 0)

        # Caso especial: pagamento de fatura de cartão (debt_is_card True)
                # Caso especial: pagamento de fatura de cartão (debt_is_card True)
        if context.user_data.get("is_debt_payment") and context.user_data.get("debt_is_card"):
            bank_account = account  # conta origem do pagamento (onde sai o dinheiro)
            card_account_id = context.user_data.get("debt_card_account_id")
            card_account = await session.get(Account, card_account_id)
            if not card_account:
                await update.message.reply_text("⚠️ Conta do cartão não encontrada. Operação cancelada.")
                return

            paid_total = float(value_to_use)

            # Busca TODAS as transações NÃO liquidadas do cartão (sem filtro por mês)
            result = await session.execute(
                select(Transaction)
                .where(Transaction.account_id == card_account.id)
                .where(Transaction.is_settled == False)
            )
            card_txs = result.scalars().all()

            invoice_total = 0.0
            # coleções auxiliares:
            debt_links_to_reduce = []   # lista de Debt ORM objects que serão abatidos (1 parcela cada)
            nonparcel_txs = []          # lista de Transaction ORM objects que devem ser marcadas como settled

            # Primeiro: calcular total a pagar baseado em:
            # - compras não-parceladas (somam o valor inteiro)
            # - parcelamentos: somam apenas monthly_payment dos Debt vinculados
            for tx in card_txs:
                if tx.value is None or tx.value >= 0:
                    continue
                desc = (tx.description or "").lower()

                # tenta encontrar Debts PARCELADO vinculados a essa tx
                debt_result = await session.execute(
                    select(Debt)
                    .where(Debt.profile_id == card_account.profile_id)
                    .where(Debt.creditor.ilike(f"%#{tx.id}"))
                    .where(Debt.type == DebtType.PARCELADO)
                    .where(Debt.months > 0)
                )
                linked_debts = debt_result.scalars().all()

                if linked_debts:
                    # soma a parcela mensal de cada parcelamento vinculado
                    for linked in linked_debts:
                        try:
                            monthly = float(linked.monthly_payment)
                            invoice_total += monthly
                            # registramos que esse Debt precisa ser abatido (não marcamos a tx como settled)
                            debt_links_to_reduce.append(linked)
                        except Exception:
                            # fallback: se monthly inválido, soma valor da transação
                            invoice_total += -tx.value
                            nonparcel_txs.append(tx)
                else:
                    # não há parcelamento vinculado -> compra à vista/única: soma valor inteiro e marcar para settled
                    invoice_total += -tx.value
                    nonparcel_txs.append(tx)

            invoice_total = round(invoice_total, 2)

            # Se user confirmou paid_total <-> invoice_total em outro lugar, segue. Aqui usamos paid_total recebido.
            # Atualiza saldos: saída na conta bancária (reduz) e \"liquidação\" no cartão (reduz fatura)
            bank_account.balance = (bank_account.balance or 0) - paid_total
            card_account.balance = (card_account.balance or 0) + paid_total

            # Criar transações de saída (bancária) e entrada (no cartão)
            bank_tx = Transaction(
                type=TransactionType("saida"),
                value=-paid_total,
                category_id=(category.id if category else None),
                account_id=bank_account.id,
                profile_id=profile.id,
                description=(description or "") + (f" {debt_info_line}" if debt_info_line else ""),
                date=today,
                balance_before=balance_before
            )

            # balance_before para o cartão
            result = await session.execute(
                select(Transaction)
                .where(Transaction.account_id == card_account.id)
                .where(Transaction.date == today)
                .order_by(Transaction.id.desc())
            )
            last_tx_card_today = result.scalars().first()
            card_balance_before = (last_tx_card_today.balance_before + last_tx_card_today.value) if last_tx_card_today else (card_account.balance - paid_total)

            card_tx = Transaction(
                type=TransactionType("entrada"),
                value=paid_total,
                category_id=None,
                account_id=card_account.id,
                profile_id=profile.id,
                description=(f"Pagamento do cartão via {bank_account.name}."),
                date=today,
                balance_before=card_balance_before
            )

            session.add(bank_tx)
            session.add(card_tx)
            session.add(bank_account)
            session.add(card_account)

            # Agora: aplicar abatimentos nos debts PARCELADO (reduz months em 1) — NÃO marcamos as transações parceladas como settled
            debt_updates_info = []
            # Usamos um set para evitar abater o mesmo Debt múltiplas vezes caso apareça duplicado
            seen_debts = set()
            for linked in debt_links_to_reduce:
                # linked é um ORM Debt
                if linked.id in seen_debts:
                    continue
                seen_debts.add(linked.id)

                remaining_before = linked.months
                linked.months = max(0, linked.months - 1)
                if linked.months == 0:
                    await session.delete(linked)
                else:
                    session.add(linked)
                try:
                    monthly = float(linked.monthly_payment)
                except Exception:
                    monthly = 0.0
                debt_updates_info.append((linked.creditor, 1, remaining_before, linked.months, monthly))

            # Marcar COMO LIQUIDADAS apenas as transações que não eram parceladas (nonparcel_txs)
            for tx in nonparcel_txs:
                try:
                    tx.is_settled = True
                except Exception:
                    # fallback: se campo não existir, tentar setar status/paid ou atualizar descrição
                    if hasattr(tx, 'status'):
                        setattr(tx, 'status', 'paid')
                    elif hasattr(tx, 'paid'):
                        setattr(tx, 'paid', True)
                    else:
                        tx.description = (tx.description or "") + " [FATURA PAGA]"
                session.add(tx)

            # Commit das alterações (transações + debts)
            await session.commit()
            await session.refresh(bank_tx)
            await session.refresh(bank_account)
            await session.refresh(card_tx)
            await session.refresh(card_account)

            # Mensagem final com detalhes
            msg = (
                f"✅ Pagamento registrado:\n"
                f"Cartão: {card_account.name}\n"
                f"Valor pago: {card_account.currency.value} {paid_total:.2f}\n"
            )
            if debt_updates_info:
                msg += "\n📌 Abatimentos realizados:\n"
                for du in debt_updates_info:
                    creditor, reduced_months, before_m, after_m, amt = du
                    msg += f"- {reduced_months}x de '{creditor}': R$ {amt:.2f} (de {before_m} -> {after_m})\n"

            await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
            return


        # Atualiza saldo da conta normalmente (fluxo não-fatura)
        account.balance = (account.balance or 0) + tx_value
        currency = account.currency.value

        # Cria transação normal
        tx = Transaction(
            type=TransactionType(t_type),
            value=tx_value,
            category_id=(category.id if category else None),
            account_id=account.id,
            profile_id=profile.id,
            description=(description or "") + (f"{debt_info_line}" if debt_info_line else ""),
            date=today,
            balance_before=balance_before
        )
        session.add(tx)
        session.add(account)

        # Se a transação foi uma compra no cartão e foi marcada como parcelada, cria registro de 'Debt' para acompanhar as parcelas
        try:
            installments = int(context.user_data.get("card_installments", 1))
        except Exception:
            installments = 1

        # Verifica se a conta é cartão e se é uma saída
        if account.type == "credit_card" and t_type == "saida" and installments > 1:
            installment_value = round(float(value_to_use) / installments, 2)
             # Determina o próximo número de parcela sequencial
            async with get_session() as session:
                result = await session.execute(
                    select(Debt)
                    .where(Debt.profile_id == profile.id)
                    .where(Debt.type == DebtType.PARCELADO)
                    .where(Debt.creditor.ilike(f"{account.name} - Parcelado #%"))
                )
                existing = result.scalars().all()
                # Extrai números existentes
                existing_numbers = []
                for d in existing:
                    try:
                        num = int(d.creditor.split("#")[-1])
                        existing_numbers.append(num)
                    except Exception:
                        continue
                next_number = max(existing_numbers) + 1 if existing_numbers else 1

            # Cria o debt com o número sequencial correto
            creditor = f"{account.name} - Parcelado #{next_number}"
            debt = Debt(profile_id=profile.id, creditor=creditor, months=installments, monthly_payment=installment_value, type=DebtType.PARCELADO)
            session.add(debt)
            await session.flush()
            # Acrescenta uma linha informativa à descrição
            tx.description = (tx.description or "") + f"📦 Parcelado em {installments}x de R$ {installment_value:.2f} (total R$ {value_to_use:.2f})"

        # Commit único (inclui dívida se aplicável)
        await session.commit()
        await session.refresh(tx)
        await session.refresh(account)

        # Mensagem final para transação normal
        category_line = f"Categoria: {category.name}" if category else ""
        await update.message.reply_text(
            f"✅ Transação registrada:\n"
            f"Tipo: {t_type}\n"
            f"Valor: {currency} {tx_value:.2f}\n"
            f"{category_line}\n"
            f"{debt_info_line}\n"
            f"Conta: {account.name}\n"
            f"Saldo atual da conta: {currency} {account.balance:.2f}\n"
            f"Data: {today.strftime('%d/%m/%Y')}\n",
            reply_markup=ReplyKeyboardRemove()
        )
