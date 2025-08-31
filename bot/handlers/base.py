from telegram.ext import  CommandHandler, MessageHandler, filters, ContextTypes
from  handlers.transactions import add_transaction, auth
from  handlers.category import list_and_add_category
from  handlers.summary import summary_month
from  handlers.mydata import my_data
from  handlers.start import start_handler
from  handlers.wallet import daily_budget
from  handlers.quick_purchase import add_quick_purchase

from telegram import Update, ReplyKeyboardRemove


async def exit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1️⃣ Limpar todos os dados do usuário
    context.user_data.clear()

    # 2️⃣ Remover qualquer teclado aberto
    await update.message.reply_text(
        "✅ Fluxo cancelado e reiniciado.",
        reply_markup=ReplyKeyboardRemove()
    )

async def step_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fluxo de categoria
    if "step_category" in context.user_data:
        await list_and_add_category(update, context)
        return

    # Fluxo de transação
    if "step" in context.user_data:
        await add_transaction(update, context)
        return
    
    # Fluxo de Compra rapida
    if "step_quick_purchase" in context.user_data:
        await add_quick_purchase(update, context)
        return    
    
    # Fluxo de Dados Pessoais
    if "mydata_step" in context.user_data:
        await my_data(update, context)
        return


def register_handlers(app): 
    
    app.add_handler(CommandHandler("start", start_handler))

    # add_quick_purchase
    app.add_handler(CommandHandler("comprarapida", add_quick_purchase))

    # transactions
    app.add_handler(CommandHandler("add", add_transaction))

    # wallet
    app.add_handler(CommandHandler("carteira", daily_budget))

    # category
    app.add_handler(CommandHandler("listacategorias", list_and_add_category))

    # Resumo
    app.add_handler(CommandHandler("resumo", summary_month))

    # Meu Dados
    app.add_handler(CommandHandler("meusdados", my_data))

    # Handler global para texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, step_handler))

    app.add_handler(CommandHandler("exit", exit_handler))

    
    

