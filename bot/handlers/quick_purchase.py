# quick_purchase.py
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
from sqlalchemy import select

from db.session import get_session
from db.models import (
    Category, CategoryType,
    Account, CurrencyEnum
)
from db.auth import auth
from  handlers.transactions import save_transaction


async def add_quick_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    context.user_data.setdefault("profile_id", profile.id)
    raw = (update.message.text or "").strip()
    text = raw.lower().strip()

    if "step_quick_purchase" not in context.user_data:
        context.user_data["step_quick_purchase"] = "qp_value"
        await update.message.reply_text(
            "Registrar saída rápida — qual o valor?",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if context.user_data["step_quick_purchase"] == "qp_value":
        try:
            value = float(text.replace(",", "."))
            if value <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Valor inválido. Digite um número maior que zero.")
            return

        context.user_data["value"] = value
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        async with get_session() as session:
            result = await session.execute(
                select(Category)
                .where(Category.profile_id == profile.id)
                .where(Category.type == CategoryType.VARIAVEL)
                .order_by(Category.name)
                .limit(8)
            )
            cats = result.scalars().all()

        if not cats:
            context.user_data["step_quick_purchase"] = "qp_category_new"
            await update.message.reply_text(
                "Qual a categoria da saída? Digite o NOME da nova categoria:",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        keyboard = [[c.name] for c in cats] + [["Criar nova categoria"]]
        await update.message.reply_text(
            "Escolha uma categoria existente ou digite uma nova:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        context.user_data["step_quick_purchase"] = "qp_category"
        return

    if context.user_data["step_quick_purchase"] == "qp_category":
        chosen = raw.strip()
        if chosen.lower() == "criar nova categoria":
            context.user_data["step_quick_purchase"] = "qp_category_new"
            await update.message.reply_text("Digite o NOME da nova categoria:", reply_markup=ReplyKeyboardRemove())
            return

        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            result = await session.execute(
                select(Category)
                .where(Category.profile_id == profile.id)
                .where(Category.type == CategoryType.VARIAVEL)
                .where(Category.name.ilike(chosen))
            )
            category = result.scalar_one_or_none()

        if category:
            context.user_data["category"] = category.name
            context.user_data["category_id"] = category.id
            context.user_data["step_quick_purchase"] = "qp_used_card"
            await update.message.reply_text(
                "A compra foi feita no cartão de crédito? (sim/não)",
                reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True)
            )
            return

        context.user_data["step_quick_purchase"] = "qp_category_new"
        context.user_data["pending_category_input"] = chosen

    if context.user_data["step_quick_purchase"] == "qp_category_new":
        provided = context.user_data.pop("pending_category_input", None)
        new_name = (provided or raw).strip()
        if not new_name:
            await update.message.reply_text("Nome inválido. Digite o nome da nova categoria:")
            return

        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            result = await session.execute(
                select(Category)
                .where(Category.profile_id == profile.id)
                .where(Category.type == CategoryType.VARIAVEL)
                .where(Category.name.ilike(new_name))
            )
            existing_var = result.scalar_one_or_none()
            if existing_var:
                category = existing_var
            else:
                category = Category(profile_id=profile.id, name=new_name, type=CategoryType.VARIAVEL)
                session.add(category)
                await session.flush()
                await session.commit()
                await session.refresh(category)

        context.user_data["category"] = category.name
        context.user_data["category_id"] = category.id
        context.user_data["step_quick_purchase"] = "qp_used_card"
        await update.message.reply_text(
            f"Categoria '{category.name}' criada.\nA compra foi feita no cartão de crédito? (sim/não)",
            reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True)
        )
        return

    if context.user_data["step_quick_purchase"] == "qp_used_card":
        if text not in ("sim", "não", "nao"):
            await update.message.reply_text("Responda apenas com 'sim' ou 'não'.")
            return

        if text in ("sim",):
            context.user_data["step_quick_purchase"] = "qp_card"
            await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            async with get_session() as session:
                result = await session.execute(
                    select(Account).where(Account.profile_id == profile.id).where(Account.type == "credit_card").order_by(Account.name)
                )
                cards = result.scalars().all()

            if not cards:
                context.user_data["step_quick_purchase"] = "qp_create_card_direct"
                await update.message.reply_text("Nenhum cartão cadastrado. Digite o NOME do cartão (será criado):", reply_markup=ReplyKeyboardRemove())
                return

            keyboard = [[c.name] for c in cards] + [["Criar novo cartão"]]
            await update.message.reply_text(
                "Escolha o cartão ou digite um novo:",
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            )
            return

        context.user_data["step_quick_purchase"] = "qp_account"
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            result = await session.execute(
                select(Account).where(Account.profile_id == profile.id).where(Account.type == "bank").order_by(Account.name)
            )
            accounts = result.scalars().all()

        if not accounts:
            context.user_data["step_quick_purchase"] = "qp_create_bank_direct"
            await update.message.reply_text("Nenhuma conta bancária cadastrada. Digite o NOME da conta (será criada):", reply_markup=ReplyKeyboardRemove())
            return

        keyboard = [[a.name] for a in accounts] + [["Criar nova conta"]]
        await update.message.reply_text(
            "Escolha a conta ou digite um novo nome:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return

    if context.user_data["step_quick_purchase"] == "qp_create_card_direct":
        name = raw.strip()
        if not name:
            await update.message.reply_text("Nome inválido. Digite o nome do cartão:")
            return
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            card = Account(profile_id=profile.id, name=name, type="credit_card", balance=0.0, currency=CurrencyEnum.BRL)
            session.add(card)
            await session.flush()
            await session.commit()
            await session.refresh(card)

        context.user_data["card_account_id"] = card.id
        context.user_data["step_quick_purchase"] = "qp_installments"
        await update.message.reply_text("Foi parcelado? Digite o número de parcelas:", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data["step_quick_purchase"] == "qp_card":
        if raw.strip().lower() == "criar novo cartão":
            context.user_data["step_quick_purchase"] = "qp_create_card_direct"
            await update.message.reply_text("Digite o NOME do novo cartão:", reply_markup=ReplyKeyboardRemove())
            return

        chosen = raw.strip()
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            result = await session.execute(
                select(Account).where(Account.profile_id == profile.id).where(Account.type == "credit_card").where(Account.name.ilike(chosen))
            )
            card = result.scalar_one_or_none()
            if not card:
                card = Account(profile_id=profile.id, name=chosen, type="credit_card", balance=0.0, currency=CurrencyEnum.BRL)
                session.add(card)
                await session.flush()
                await session.commit()
                await session.refresh(card)

        context.user_data["card_account_id"] = card.id
        context.user_data["step_quick_purchase"] = "qp_installments"
        await update.message.reply_text("Foi parcelado? Digite o número de parcela:", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data["step_quick_purchase"] == "qp_create_bank_direct":
        name = raw.strip()
        if not name:
            await update.message.reply_text("Nome inválido. Digite o nome da conta:")
            return
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            acc = Account(profile_id=profile.id, name=name, type="bank", balance=0.0, currency=CurrencyEnum.BRL)
            session.add(acc)
            await session.flush()
            await session.commit()
            await session.refresh(acc)

        context.user_data["account_id"] = acc.id
        context.user_data["step_quick_purchase"] = "qp_description"
        await update.message.reply_text("Adicione uma descrição opcional para a compra:", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data["step_quick_purchase"] == "qp_account":
        if raw.strip().lower() == "criar nova conta":
            context.user_data["step_quick_purchase"] = "qp_create_bank_direct"
            await update.message.reply_text("Digite o NOME da nova conta:", reply_markup=ReplyKeyboardRemove())
            return

        chosen = raw.strip()
        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        async with get_session() as session:
            result = await session.execute(
                select(Account).where(Account.profile_id == profile.id).where(Account.type == "bank").where(Account.name.ilike(chosen))
            )
            acc = result.scalar_one_or_none()
            if not acc:
                acc = Account(profile_id=profile.id, name=chosen, type="bank", balance=0.0, currency=CurrencyEnum.BRL)
                session.add(acc)
                await session.flush()
                await session.commit()
                await session.refresh(acc)

        context.user_data["account_id"] = acc.id
        context.user_data["step_quick_purchase"] = "qp_description"
        await update.message.reply_text("Adicione uma descrição opcional para a compra:", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data["step_quick_purchase"] == "qp_installments":
        try:
            installments = int(text)
            if installments <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Número inválido. Digite um inteiro (ex: 3).")
            return

        context.user_data["card_installments"] = installments
        context.user_data["step_quick_purchase"] = "qp_description"
        await update.message.reply_text("Adicione uma descrição opcional para a compra:", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data["step_quick_purchase"] == "qp_description":
        desc = raw.strip()
        if desc == "-" or desc == "":
            desc = None
        context.user_data["description"] = desc

        context.user_data["type"] = "saida"
        if context.user_data.get("card_account_id"):
            context.user_data["account_id"] = context.user_data.get("card_account_id")
        context.user_data["card_installments"] = int(context.user_data.get("card_installments", 1))

        await context. send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        await save_transaction(update, context)

        await update.message.reply_text("✅ Saída registrada com sucesso!", reply_markup=ReplyKeyboardRemove())
        context.user_data.clear()
        return

    await update.message.reply_text("Não entendi. Para iniciar uma saída rápida, digite /compra_rapida.")
