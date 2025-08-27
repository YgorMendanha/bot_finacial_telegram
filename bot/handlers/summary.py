import io
import datetime
import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from db.session import get_session
from db.models import Transaction, TransactionType, Category, CategoryType
from db.auth import auth
from dateutil.relativedelta import relativedelta

async def summary_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = await auth(update)
    if profile is None:
        return 

    # tratar argumento mm/aaaa
    args = context.args
    today = datetime.date.today()
    if args:
        try:
            month, year = map(int, args[0].split("/"))
        except Exception:
            await update.message.reply_text("Use o formato: /resumo mm/aaaa")
            return
    else:
        month, year = today.month, today.year

    start_date = datetime.date(year, month, 1)
    end_date = start_date + relativedelta(months=1)  # próximo mês

    await update.message.reply_text(f"⌛ Gerando Relatório para {month:02d}/{year}...")

    async with get_session() as session:
        # total por categoria (apenas despesas)
        result = await session.execute(
            select(Transaction.category_id, func.sum(Transaction.value))
            .where(Transaction.date >= start_date, Transaction.date < end_date)
            .where(Transaction.profile_id == profile.id)
            .where(Transaction.type == TransactionType.SAIDA)
            .group_by(Transaction.category_id)
        )
        category_totals = result.all()

        if not category_totals:
            await update.message.reply_text("ℹ️ Nenhuma despesa encontrada nesse período.")
            return

        category_names = []
        category_values = []
        fixed_total = 0
        variable_total = 0

        for cat_id, total in category_totals:
            cat = await session.get(Category, cat_id)
            name = cat.name if cat else "Sem Categoria"
            category_names.append(name)
            value_abs = abs(total)
            category_values.append(value_abs)
            if cat and cat.type == CategoryType.FIXA:
                fixed_total += value_abs
            else:
                variable_total += value_abs

        # total de despesas e receitas no mês
        result = await session.execute(
            select(Transaction.type, func.sum(Transaction.value))
            .where(Transaction.date >= start_date, Transaction.date < end_date)
            .where(Transaction.profile_id == profile.id)
            .group_by(Transaction.type)
        )
        totals_by_type = dict(result.all())
        total_entrada = totals_by_type.get(TransactionType.ENTRADA, 0) or 0
        total_saida = totals_by_type.get(TransactionType.SAIDA, 0) or 0

        saldo = total_entrada - abs(total_saida)

        # --- preparar dados de séries mensais (até o mês atual do ano selecionado) ---
        month_saldo_real = []
        month_labels = []

        # Se quisermos mostrar até o mês selecionado (não necessariamente hoje.month)
        last_plot_month = month if year == today.year else 12 if year < today.year else min(month, today.month)
        for i in range(1, last_plot_month + 1):
            start_m = datetime.date(year, i, 1)
            end_m = start_m + relativedelta(months=1)
            result = await session.execute(
                select(func.sum(Transaction.value))
                .where(Transaction.profile_id == profile.id)
                .where(Transaction.date >= start_m, Transaction.date < end_m)
            )
            total_month = result.scalar() or 0
            month_saldo_real.append(total_month)
            month_labels.append(f"{i:02d}/{year}")

        # projeção futura (média mensal passada) -> gera rótulos apenas para meses futuros no mesmo ano
        media_saldo = sum(month_saldo_real) / len(month_saldo_real) if month_saldo_real else 0
        month_saldo_proj = []
        month_labels_proj = []
        # construir projeções para meses seguintes até dezembro do mesmo ano
        for i in range(last_plot_month + 1, 13):
            saldo_proj = month_saldo_real[-1] + media_saldo if month_saldo_real else media_saldo
            month_saldo_proj.append(saldo_proj)
            date_label = datetime.date(year, i, 1).strftime("%m/%Y")
            month_labels_proj.append(date_label)
            month_saldo_real.append(saldo_proj)  # para manter série cumulativa se precisar

        # --------------------
        # Criar imagem 1: pizza + barras (lado a lado)
        # --------------------
        fig1, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
        # Pizza
        ax1.pie(category_values, labels=category_names, autopct='%1.1f%%', startangle=90)
        ax1.set_title("Proporção de Despesas por Categoria")
        # Barra fixos vs variáveis
        ax2.bar(["Fixos", "Variáveis"], [fixed_total, variable_total])
        ax2.set_ylabel("Total (R$)")
        ax2.set_title("Despesas Fixas vs Variáveis")
        plt.tight_layout()
        img1_buf = io.BytesIO()
        plt.savefig(img1_buf, format='png')
        img1_buf.seek(0)
        plt.close(fig1)

        # --------------------
        # Criar imagem 2: saldo mensal + projeção (lado a lado)
        # --------------------
        fig2, (ax3, ax4) = plt.subplots(1, 2, figsize=(12, 6))
        # Saldo Mensal
        ax3.plot(month_labels, month_saldo_real[:len(month_labels)], marker='o', linestyle='-')
        ax3.set_title("Saldo Mensal")
        ax3.set_ylabel("R$")
        ax3.set_xlabel("Mês")
        ax3.grid(True)
        # Projeção
        ax4.plot(month_labels_proj, month_saldo_proj, marker='o', linestyle='--')
        ax4.set_title("Projeção de Saldo Futuro")
        ax4.set_ylabel("R$")
        ax4.set_xlabel("Mês")
        ax4.grid(True)
        plt.tight_layout()
        img2_buf = io.BytesIO()
        plt.savefig(img2_buf, format='png')
        img2_buf.seek(0)
        plt.close(fig2)

    resumo_text = (
        f"📊 Resumo {month:02d}/{year}\n\n"
        f"💰 Receita total: R$ {total_entrada:.2f}\n"
        f"💸 Despesa total: R$ {abs(total_saida):.2f}\n"
        f"⚖️ Saldo do período: R$ {saldo:.2f}\n\n"
        f"🏷️ Fixos: R$ {fixed_total:.2f}\n"
        f"🏷️ Variáveis: R$ {variable_total:.2f}\n"
    )

    await update.message.reply_text(resumo_text)
    # enviar apenas duas imagens (cada uma com 2 plots)
    await update.message.reply_photo(photo=img1_buf)
    await update.message.reply_photo(photo=img2_buf)
