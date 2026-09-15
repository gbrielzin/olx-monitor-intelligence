import pandas as pd
import streamlit as st

from charts import grafico_dispersao_hz, grafico_distribuicao_preco, grafico_tendencia_quedas
from common.config import settings
from common.stats import avaliar_preco, margem_e_confiavel, medianas_todos_grupos
from queries import (
    carregar_ativos,
    carregar_coletas,
    carregar_novidades,
    carregar_quedas_precos,
    carregar_tempo_no_ar,
)
from vendas import carregar_vendas, marcar_como_vendido, registrar_compra

st.set_page_config(page_title="iPhone — OLX multi-estado", layout="wide")
st.title("Inteligência de mercado (OLX)")


def _com_avaliacao(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona oportunidade/preço pós-negociação/margem a um df de anúncios
    ativos -- usado tanto pelos KPIs do topo quanto pela aba de alertas, pra
    calcular a mediana de cada grupo 1x (não 1x por linha). Agrupa por
    (categoria, grupo), não (marca, tipo) -- ver common/stats.py."""
    df = df.copy()
    medianas = medianas_todos_grupos()
    avaliacoes = [
        avaliar_preco(preco, medianas[(categoria, grupo)], categoria)
        if pd.notna(preco) and (categoria, grupo) in medianas
           and margem_e_confiavel(condicao, titulo, preco, categoria)
        else None
        for preco, categoria, grupo, condicao, titulo in zip(
            df["preco"], df["categoria"], df["grupo"], df["condicao"], df["titulo"]
        )
    ]
    df["oportunidade"] = [a.eh_oportunidade if a else False for a in avaliacoes]
    df["apos_negociar"] = [a.custo_apos_negociacao if a else None for a in avaliacoes]
    df["margem_rs"] = [a.margem_rs if a else None for a in avaliacoes]
    df["margem_pct"] = [a.margem_pct * 100 if a else None for a in avaliacoes]
    return df


def _frescor_da_coleta(categoria: str | None, uf: str | None) -> tuple[str, str | None]:
    """(texto da última coleta, uptime dos últimos 7 dias em % ou None).
    Uptime só é calculado com uma categoria escolhida -- cada categoria tem
    sua própria agenda (monitor/computador pararam de rodar em 12/09/2026,
    ver CASE_DATA_ANALYTICS.md), então misturar todas sob 'Todas' não tem
    uma frequência esperada única pra comparar contra."""
    coletas = carregar_coletas(dias=7, categoria=categoria, uf=uf)
    if coletas.empty:
        return "—", None
    ultima = pd.to_datetime(coletas["coletado_em"]).max()
    delta_min = (pd.Timestamp.now(tz="UTC") - ultima).total_seconds() / 60
    texto = f"há {delta_min:.0f} min" if delta_min < 60 else f"há {delta_min / 60:.1f}h"
    if categoria is None:
        return texto, None
    esperadas = 7 * 24 * 60 / settings.scrape_interval_minutes
    return texto, len(coletas) / esperadas * 100


@st.fragment(run_every=60)
def secao_kpis(categoria: str | None, uf: str | None = None) -> None:
    df = carregar_ativos(categoria, uf)
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return
    df = _com_avaliacao(df)
    oportunidades = df[df["oportunidade"]]
    quedas_7d = carregar_quedas_precos(dias=7, categoria=categoria, uf=uf)
    ultima_coleta, uptime = _frescor_da_coleta(categoria, uf)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Anúncios ativos", len(df))
    col2.metric("Oportunidades agora", len(oportunidades))
    col3.metric(
        "Margem mediana (oportunidades)",
        f"R$ {oportunidades['margem_rs'].median():.0f}" if not oportunidades.empty else "—",
    )
    col4.metric("Quedas de preço (7 dias)", len(quedas_7d))
    col5.metric("Última coleta", ultima_coleta)
    if uptime is not None:
        st.caption(
            f"Uptime da coleta (7 dias, {categoria}): {uptime:.0f}% das rodadas "
            f"esperadas a cada {settings.scrape_interval_minutes} min — medido "
            "direto na tabela `coletas`, não é estimativa. Coleta roda numa "
            "máquina pessoal, não um servidor sempre ligado (ver "
            "OLX_DEEP_DIVE.md, seção 7.5)."
        )


@st.fragment(run_every=60)
def secao_alertas(categoria: str | None, uf: str | None = None) -> None:
    df = carregar_ativos(categoria, uf)
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return

    df = _com_avaliacao(df)
    oportunidades = df[df["oportunidade"]]
    if oportunidades.empty:
        st.info(
            f"Nenhuma oportunidade agora pelo critério do Telegram "
            f"(preço ≤ {settings.oportunidade_limiar:.0%} da mediana do grupo, dentro do "
            f"orçamento da categoria, com histórico suficiente)."
        )
        return

    if categoria:
        teto = getattr(settings, f"orcamento_maximo_{categoria}", None)
        nota_orcamento = (
            f" Acima de R$ {teto:.0f} não entra aqui, mesmo com margem boa — ajuste em "
            f"orcamento_maximo_{categoria} (common/config.py)." if teto else ""
        )
    else:
        nota_orcamento = " Cada categoria tem seu próprio teto de orçamento — acima dele não entra aqui, mesmo com margem boa."

    st.caption(
        f"Ordenado por margem estimada — preço anunciado já descontado em "
        f"{settings.desconto_negociacao_esperado:.0%} (negociação esperada) contra a "
        f"mediana do grupo. Não é o preço final, é ponto de partida pra negociar."
        f"{nota_orcamento}"
    )
    st.dataframe(
        oportunidades[
            ["categoria", "uf", "titulo", "preco", "apos_negociar", "margem_rs", "margem_pct",
             "condicao", "saude_bateria", "inclui_monitor", "marca", "municipio", "url"]
        ].sort_values("margem_pct", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "categoria": "Categoria",
            "uf": "Estado",
            "titulo": "Anúncio",
            "preco": st.column_config.NumberColumn("Anunciado", format="R$ %.0f"),
            "apos_negociar": st.column_config.NumberColumn("Após negociar", format="R$ %.0f"),
            "margem_rs": st.column_config.NumberColumn("Margem", format="R$ %.0f"),
            "margem_pct": st.column_config.NumberColumn("Margem %", format="%.0f%%"),
            "condicao": "Condição",
            "saude_bateria": "Bateria",
            "inclui_monitor": "Inclui monitor?",
            "marca": "Marca",
            "municipio": "Município",
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_tendencia(categoria: str | None, uf: str | None = None) -> None:
    quedas = carregar_quedas_precos(dias=30, categoria=categoria, uf=uf)
    if quedas.empty:
        st.info(
            "Sem quedas de preço registradas ainda nos últimos 30 dias. "
            "Isso é esperado no começo — só passa a existir linha aqui quando um "
            "anúncio já visto baixa de preço de verdade. Cresce sozinho com o tempo."
        )
        return
    st.caption(f"{len(quedas)} queda(s) de preço real registrada(s) nos últimos 30 dias.")
    fig = grafico_tendencia_quedas(quedas)
    if fig:
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        quedas[["registrado_em", "categoria", "titulo", "marca", "condicao", "preco_anterior", "preco", "url"]]
        .sort_values("registrado_em", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "registrado_em": "Quando",
            "categoria": "Categoria",
            "titulo": "Anúncio",
            "marca": "Marca",
            "condicao": "Condição",
            "preco_anterior": st.column_config.NumberColumn("Preço antes", format="R$ %.0f"),
            "preco": st.column_config.NumberColumn("Preço depois", format="R$ %.0f"),
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_mercado(categoria: str | None, uf: str | None = None) -> None:
    df = carregar_ativos(categoria, uf)
    if df.empty:
        st.info("Sem anúncios ativos ainda.")
        return

    st.subheader("Distribuição de preço")
    sufixo = f" — {categoria}" if categoria else ""
    fig_dist = grafico_distribuicao_preco(df, sufixo)
    if fig_dist:
        st.plotly_chart(fig_dist, use_container_width=True)
        st.caption(
            "Mediana (verde) é a régua usada por `common/stats.py` pra decidir "
            "oportunidade -- não a média (vermelho), que qualquer anúncio muito "
            "fora da curva puxa pra um lado."
        )

    tempo_no_ar = carregar_tempo_no_ar(categoria, uf)
    if not tempo_no_ar.empty:
        dias = (
            pd.to_datetime(tempo_no_ar["removido_em"]) - pd.to_datetime(tempo_no_ar["primeiro_visto_em"])
        ).dt.total_seconds() / 86400
        dias = dias[dias >= 0]
        if not dias.empty:
            st.subheader("Tempo médio no ar")
            c1, c2 = st.columns(2)
            c1.metric("Mediana", f"{dias.median():.1f} dias")
            c2.metric("Amostra", len(dias))
            st.caption(
                "Quanto tempo, em média, um anúncio fica no catálogo antes de "
                "sumir da busca (não diferencia venda de desanúncio -- o "
                "scraper só sabe que parou de aparecer)."
            )

    # só existe pra monitor (precisa de hz_exato/tipo_monitor) -- pra
    # iPhone a lista de tipos vem vazia e o loop não roda, sem quebrar.
    tipos = df["tipo_monitor"].dropna().unique()
    if len(tipos):
        st.subheader("Preço x taxa de atualização (Hz)")
        for tipo in tipos:
            fig = grafico_dispersao_hz(df, tipo)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

    st.subheader("Distribuição por região")
    st.bar_chart(df["municipio"].value_counts())

    st.subheader("Marcas/modelos mais frequentes")
    coluna_agrupamento = "marca" if categoria == "monitor" else "grupo"
    st.bar_chart(df[coluna_agrupamento].value_counts().head(10))


def secao_auditoria(categoria: str | None, uf: str | None = None) -> None:
    coletas = carregar_coletas(dias=7, categoria=categoria, uf=uf)
    if coletas.empty:
        st.info("Nenhuma rodada de coleta registrada ainda.")
    else:
        coletas = coletas.copy()
        coletas["com_preco_pct"] = (coletas["com_preco"] / coletas["total_anuncios"] * 100).round(0)
        st.caption(
            "Últimas rodadas de coleta (7 dias) — mesma régua que o checkpoint de "
            f"sanidade usa pra decidir se descarta uma rodada (mín. "
            f"{settings.min_price_field_ratio:.0%} com preço, mín. "
            f"{settings.min_ads_ratio:.0%} da média recente de anúncios)."
        )
        st.dataframe(
            coletas[["coletado_em", "categoria", "total_anuncios", "novos", "quedas_preco", "com_preco_pct"]]
            .sort_values("coletado_em", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "coletado_em": "Rodada",
                "categoria": "Categoria",
                "total_anuncios": "Total",
                "novos": "Novos",
                "quedas_preco": "Quedas de preço",
                "com_preco_pct": st.column_config.NumberColumn("% com preço", format="%.0f%%"),
            },
        )

    st.subheader("Anúncios novos (últimas 24h)")
    if not settings.ia_auditoria_ativa:
        st.caption(
            "Auditoria por IA desativada (`ia_auditoria_ativa=False` em "
            "`common/config.py`) — mostrando só os campos extraídos, sem "
            "sinal de inconsistência."
        )
    novidades = carregar_novidades(dias=1, categoria=categoria, uf=uf)
    if novidades.empty:
        st.info("Nenhum anúncio novo capturado nas últimas 24h.")
        return

    novidades = novidades.copy()
    novidades["sinal_ia"] = novidades["inconsistente"].map({1: "⚠️ inconsistente", 0: "ok"}).fillna("—")
    st.dataframe(
        novidades[
            ["primeiro_visto_em", "categoria", "uf", "titulo", "preco", "grupo",
             "condicao", "municipio", "sinal_ia", "motivo", "url"]
        ].sort_values("primeiro_visto_em", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "primeiro_visto_em": "Visto em",
            "categoria": "Categoria",
            "uf": "Estado",
            "titulo": "Anúncio",
            "preco": st.column_config.NumberColumn("Preço", format="R$ %.0f"),
            "grupo": "Grupo",
            "condicao": "Condição",
            "municipio": "Município",
            "sinal_ia": "IA",
            "motivo": "Motivo (IA)",
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_sobre() -> None:
    st.markdown(
        rf"""
Sistema de arbitragem informacional: monitora anúncios de **iPhone** na
OLX, em múltiplos estados, a cada {settings.scrape_interval_minutes} min,
calcula a mediana de mercado de cada grupo (modelo+armazenamento — separada
por estado, um iPhone de SP não compete pela mesma mediana que um do ES),
e avisa quando um anúncio aparece — ou baixa de preço — a
**{settings.oportunidade_limiar:.0%} da mediana do grupo ou menos**. Cada
estado pode ter seu próprio grupo do Telegram recebendo os alertas — a
região pessoal do administrador fica só no chat pessoal.

**A tese:** parte do mercado de usados é ineficiente — vendedor urgente ou
desinformado anuncia abaixo do preço justo. Achar isso manualmente, na hora
certa, não escala; um scraper de baixa frequência sim.

**O que o sistema NÃO faz:** não decide comprar por você, não avalia o item
fisicamente, não substitui negociar. O alerta já desconta
{settings.desconto_negociacao_esperado:.0%} de negociação esperada na
margem estimada, mas o número real só se confirma na conversa com o vendedor.

**Histórico:** monitor gamer e computador completo também foram
monitorados — o mercado se mostrou ineficiente pra continuar, mas os dados
coletados ficam salvos e visíveis nas outras abas (filtro "Monitor"/
"Computador") como referência histórica, sem coleta nova. A análise que
sustentou a faixa recomendada de monitor na época (R\$300–600, Novo/
Usado-Excelente) continua disponível:
[Raio-X do Monitor Gamer](https://claude.ai/code/artifact/333187dc-24c9-4d68-8718-eed4fb54de7b)
        """
    )


def secao_vendas() -> None:
    st.subheader("📥 Registrar compra")
    with st.form("nova_compra", clear_on_submit=True):
        titulo = st.text_input("O que comprou")
        c1, c2, c3 = st.columns(3)
        preco_pago = c1.number_input("Preço pago (R$)", min_value=0.0, step=10.0)
        categoria_compra = c2.selectbox("Categoria", ["monitor", "iphone", "computador", "outro"])
        url = c3.text_input("Link do anúncio (opcional)")
        if st.form_submit_button("Registrar") and titulo and preco_pago >= 1:
            registrar_compra(titulo, preco_pago, categoria_compra, url or None)
            st.success(f"Compra registrada: {titulo}")
            st.rerun()

    df = carregar_vendas()
    em_aberto = df[df["preco_revenda"].isna()]

    st.subheader(f"📤 Marcar como vendido ({len(em_aberto)} em aberto)")
    if em_aberto.empty:
        st.info("Nada em aberto ainda.")
    else:
        opcoes = {
            f"#{r.id} — {r.titulo} (pago R$ {r.preco_pago:.0f})": r.id
            for r in em_aberto.itertuples()
        }
        escolha = st.selectbox("Qual item", list(opcoes.keys()))
        c1, c2, c3 = st.columns(3)
        preco_revenda = c1.number_input("Vendido por (R$)", min_value=0.0, step=10.0)
        comprador = c2.text_input("Comprador (opcional)")
        if c3.button("Confirmar venda") and preco_revenda >= 1:
            marcar_como_vendido(opcoes[escolha], preco_revenda, comprador or None)
            st.success("Venda registrada!")
            st.rerun()

    st.subheader("📊 Histórico")
    concluidas = df[df["preco_revenda"].notna()].copy()
    if concluidas.empty:
        st.info(
            "Nenhuma venda concluída ainda — vai aparecer aqui, com a margem real "
            "comparada à estimada pelo sistema. É a base pra calibrar os parâmetros "
            "com resultado de verdade, não achismo."
        )
        return

    concluidas["margem_real"] = concluidas["preco_revenda"] - concluidas["preco_pago"]
    concluidas["margem_pct"] = concluidas["margem_real"] / concluidas["preco_pago"] * 100
    c1, c2, c3 = st.columns(3)
    c1.metric("Vendas concluídas", len(concluidas))
    c2.metric("Margem total real", f"R$ {concluidas['margem_real'].sum():.0f}")
    c3.metric("Margem média", f"{concluidas['margem_pct'].mean():.0f}%")
    st.dataframe(
        concluidas[
            ["titulo", "categoria", "preco_pago", "preco_revenda", "margem_real",
             "margem_pct", "comprador", "data_compra", "data_venda"]
        ].sort_values("data_venda", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "titulo": "O que",
            "categoria": "Categoria",
            "preco_pago": st.column_config.NumberColumn("Pago", format="R$ %.0f"),
            "preco_revenda": st.column_config.NumberColumn("Vendido", format="R$ %.0f"),
            "margem_real": st.column_config.NumberColumn("Margem", format="R$ %.0f"),
            "margem_pct": st.column_config.NumberColumn("Margem %", format="%.0f%%"),
            "comprador": "Comprador",
            "data_compra": "Comprou em",
            "data_venda": "Vendeu em",
        },
    )


categoria_label = st.radio(
    "Categoria", ["iPhone", "Monitor", "Computador", "Todas"], horizontal=True,
    help="iPhone é a única categoria ainda coletada -- Monitor e Computador "
    "pararam de ser agendados em 12/09/2026 (mercado se mostrou ineficaz, "
    "ver CASE_DATA_ANALYTICS.md) e ficam com dado congelado: os anúncios "
    "continuam marcados como ativos mesmo sem confirmação recente de que "
    "ainda estão no ar, então oportunidade/margem ali não são confiáveis.",
)
categoria = {"iPhone": "iphone", "Monitor": "monitor", "Computador": "computador", "Todas": None}[categoria_label]
if categoria in ("monitor", "computador") or categoria is None:
    st.warning(
        "⚠️ Monitor e Computador pararam de ser coletados em 12/09/2026 — "
        "os anúncios dessas categorias ficam com o último estado visto antes "
        "disso (não são re-checados), então margem/oportunidade aqui refletem "
        "dado congelado, não o mercado agora.",
        icon="⚠️",
    )

# Seletor de Estado só aparece com mais de 1 região de iPhone configurada
# -- sem isso (hoje: só ES), nada muda visualmente no dashboard.
uf = None
ufs_iphone = sorted({r.uf for r in settings.iphone_regioes})
if categoria == "iphone" and len(ufs_iphone) > 1:
    uf_label = st.radio("Estado", ["Todos"] + ufs_iphone, horizontal=True)
    uf = None if uf_label == "Todos" else uf_label

secao_kpis(categoria, uf)

tab_oportunidades, tab_tendencia, tab_mercado, tab_vendas, tab_auditoria, tab_sobre = st.tabs(
    ["🔔 Oportunidades", "📈 Tendência de preço", "🗺️ Mercado", "💵 Vendas", "🩺 Auditoria", "ℹ️ Sobre o negócio"]
)
with tab_oportunidades:
    secao_alertas(categoria, uf)
with tab_tendencia:
    secao_tendencia(categoria, uf)
with tab_mercado:
    secao_mercado(categoria, uf)
with tab_vendas:
    secao_vendas()
with tab_auditoria:
    secao_auditoria(categoria, uf)
with tab_sobre:
    secao_sobre()
