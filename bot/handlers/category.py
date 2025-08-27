from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from db.session import get_session
from db.models import Category, CategoryType
from sqlalchemy import select
from db.auth import auth


async def list_and_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return

    text = update.message.text.strip().lower()

    async with get_session() as session:
        # PASSO 1: listar categorias e perguntar se quer adicionar nova
        if "step_category" not in context.user_data:
            await update.message.reply_text("⌛ Buscando categorias...")
            result = await session.execute(
                select(Category)
                .where(Category.profile_id == profile.id)
                .order_by(Category.name)
            )
            categories = result.scalars().all()

            if not categories:
                await update.message.reply_text("Nenhuma categoria cadastrada ainda.")
            else:
                # Numeração + negrito + tipo, escapando ponto e parênteses
                text_categories = "📂 *Categorias existentes:*\n\n" + "\n".join(
                    rf"{i+1}\. *{c.name}* \({c.type.value.capitalize()}\)" for i, c in enumerate(categories)
                )
                await update.message.reply_text(
                    text_categories,
                    parse_mode="MarkdownV2"
                )


            await update.message.reply_text(
                "Deseja adicionar uma nova categoria? (sim/não)",
                reply_markup=ReplyKeyboardMarkup([["sim", "não"]], one_time_keyboard=True, resize_keyboard=True)
            )
            context.user_data["step_category"] = "confirm_add_category"
            return

        # PASSO 2: usuário respondeu "sim" ou "não"
        if context.user_data["step_category"] == "confirm_add_category":
            if text == "sim":
                context.user_data["step_category"] = "type_new_category"
                await update.message.reply_text("Digite o nome da nova categoria:",
                                                reply_markup=ReplyKeyboardRemove())
            else:
                await update.message.reply_text("Ok, nenhuma categoria foi adicionada.",
                                                reply_markup=ReplyKeyboardRemove())
                context.user_data.clear()
            return

        # PASSO 3: usuário digitou o nome da nova categoria
        if context.user_data["step_category"] == "type_new_category":
            new_name = update.message.text.strip()
            result = await session.execute(
                select(Category)
                .where(Category.profile_id == profile.id)
                .where(Category.name.ilike(new_name))
            )
            existing = result.scalar_one_or_none()

            if existing:
                await update.message.reply_text(f"A categoria '{new_name}' já existe.",
                                                reply_markup=ReplyKeyboardRemove())
            else:
                context.user_data["new_category_name"] = new_name
                context.user_data["step_category"] = "category_kind"
                await update.message.reply_text(
                    "Essa categoria é Variável ou Fixa?",
                    reply_markup=ReplyKeyboardMarkup([["variável", "fixa"]], one_time_keyboard=True, resize_keyboard=True)
                )
            return

        # PASSO 4: usuário escolheu variável ou fixa
        if context.user_data["step_category"] == "category_kind":
            user_input = text  # "variável" ou "fixa"
            new_name = context.user_data.get("new_category_name")
            if not new_name:
                await update.message.reply_text("Nome da categoria não encontrado. Vamos começar de novo.",
                                                reply_markup=ReplyKeyboardRemove())
                context.user_data.clear()
                return

            if user_input in ("variavel", "variável"):
                tipo = CategoryType.VARIAVEL
            elif user_input == "fixa":
                tipo = CategoryType.FIXA
            else:
                await update.message.reply_text("Escolha inválida. Digite 'variável' ou 'fixa'.")
                return

            new_category = Category(name=new_name, type=tipo, profile_id=profile.id)
            session.add(new_category)
            await session.commit()
            await session.refresh(new_category)

            await update.message.reply_text(f"✅ Categoria '{new_name}' ({user_input}) criada!",
                                            reply_markup=ReplyKeyboardRemove())
            context.user_data.clear()
            await list_and_add_category(update, context)  # mostra resumo atualizado
            return
