import pandas as pd
import streamlit as st

from charts import grafico_dispersao_hz, grafico_tendencia_quedas
from common.config import settings
from common.stats import avaliar_preco, margem_e_confiavel, medianas_todos_grupos
from queries import carregar_ativos, carregar_quedas_precos

st.set_page_config(page_title="Monitor Gamer — Grande Vitória", layout="wide")
st.title("Monitor Gamer — Inteligência de mercado (OLX-ES)")


def _com_avaliacao(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona oportunidade/preço pós-negociação/margem a um df de anúncios
    ativos -- usado tanto pelos KPIs do topo quanto pela aba de alertas, pra
    calcular a mediana de cada grupo 1x (não 1x por linha). Ver common/stats.py."""
    df = df.copy()
    medianas = medianas_todos_grupos()
    avaliacoes = [
        avaliar_preco(preco, medianas[(marca, tipo)])
        if pd.notna(preco) and (marca, tipo) in medianas and margem_e_confiavel(condicao, titulo)
        else None
        for preco, marca, tipo, condicao, titulo in zip(
            df["preco"], df["marca"], df["tipo_monitor"], df["condicao"], df["titulo"]
        )
    ]
    df["oportunidade"] = [a.eh_oportunidade if a else False for a in avaliacoes]
    df["apos_negociar"] = [a.custo_apos_negociacao if a else None for a in avaliacoes]
    df["margem_rs"] = [a.margem_rs if a else None for a in avaliacoes]
    df["margem_pct"] = [a.margem_pct * 100 if a else None for a in avaliacoes]
    return df


@st.fragment(run_every=60)
def secao_kpis() -> None:
    df = carregar_ativos()
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return
    df = _com_avaliacao(df)
    oportunidades = df[df["oportunidade"]]
    quedas_7d = carregar_quedas_precos(dias=7)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Anúncios ativos", len(df))
    col2.metric("Oportunidades agora", len(oportunidades))
    col3.metric(
        "Margem mediana (oportunidades)",
        f"R$ {oportunidades['margem_rs'].median():.0f}" if not oportunidades.empty else "—",
    )
    col4.metric("Quedas de preço (7 dias)", len(quedas_7d))


@st.fragment(run_every=60)
def secao_alertas() -> None:
    df = carregar_ativos()
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return

    df = _com_avaliacao(df)
    oportunidades = df[df["oportunidade"]]
    if oportunidades.empty:
        st.info(
            "Nenhuma oportunidade agora pelo critério do Telegram "
            "(preço ≤ 75% da mediana do grupo, com histórico suficiente)."
        )
        return

    st.caption(
        f"Ordenado por margem estimada — preço anunciado já descontado em "
        f"{settings.desconto_negociacao_esperado:.0%} (negociação esperada) contra a "
        f"mediana do grupo. Não é o preço final, é ponto de partida pra negociar."
    )
    st.dataframe(
        oportunidades[
            ["titulo", "preco", "apos_negociar", "margem_rs", "margem_pct",
             "condicao", "marca", "hz_exato", "municipio", "url"]
        ].sort_values("margem_pct", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "titulo": "Anúncio",
            "preco": st.column_config.NumberColumn("Anunciado", format="R$ %.0f"),
            "apos_negociar": st.column_config.NumberColumn("Após negociar", format="R$ %.0f"),
            "margem_rs": st.column_config.NumberColumn("Margem", format="R$ %.0f"),
            "margem_pct": st.column_config.NumberColumn("Margem %", format="%.0f%%"),
            "condicao": "Condição",
            "marca": "Marca",
            "hz_exato": "Hz",
            "municipio": "Município",
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_tendencia() -> None:
    quedas = carregar_quedas_precos(dias=30)
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
        quedas[["registrado_em", "titulo", "marca", "condicao", "preco_anterior", "preco", "url"]]
        .sort_values("registrado_em", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "registrado_em": "Quando",
            "titulo": "Anúncio",
            "marca": "Marca",
            "condicao": "Condição",
            "preco_anterior": st.column_config.NumberColumn("Preço antes", format="R$ %.0f"),
            "preco": st.column_config.NumberColumn("Preço depois", format="R$ %.0f"),
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_mercado() -> None:
    df = carregar_ativos()
    if df.empty:
        st.info("Sem anúncios ativos ainda.")
        return

    st.subheader("Preço x taxa de atualização (Hz)")
    for tipo in df["tipo_monitor"].dropna().unique():
        fig = grafico_dispersao_hz(df, tipo)
        if fig:
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Distribuição por região")
    st.bar_chart(df["municipio"].value_counts())

    st.subheader("Marcas mais frequentes")
    st.bar_chart(df["marca"].value_counts().head(10))


def secao_sobre() -> None:
    st.markdown(
        rf"""
Sistema de arbitragem informacional: monitora anúncios de monitor gamer na OLX
(Grande Vitória/ES) a cada {settings.scrape_interval_minutes} min, calcula a
mediana de mercado por marca + tipo de monitor, e avisa quando um anúncio
aparece — ou baixa de preço — a **{settings.oportunidade_limiar:.0%} da
mediana do grupo ou menos**.

**A tese:** parte do mercado de usados é ineficiente — vendedor urgente ou
desinformado anuncia abaixo do preço justo. Achar isso manualmente, na hora
certa, não escala; um scraper de baixa frequência sim.

**O que o sistema NÃO faz:** não decide comprar por você, não avalia o item
fisicamente, não substitui negociar. O alerta já desconta
{settings.desconto_negociacao_esperado:.0%} de negociação esperada na
margem estimada, mas o número real só se confirma na conversa com o vendedor.

**Onde focar agora**, pelos dados reais do próprio catálogo: monitores entre
R\$300 e R\$600, em estado Novo ou Usado-Excelente — maior volume de anúncios
e melhor relação margem/risco do que ticket único mais caro ou faixa abaixo
de R\$300 (mais risco de defeito).

Análise completa, com os números que sustentam essa recomendação:
[Raio-X do Monitor Gamer](https://claude.ai/code/artifact/333187dc-24c9-4d68-8718-eed4fb54de7b)
        """
    )


secao_kpis()

tab_oportunidades, tab_tendencia, tab_mercado, tab_sobre = st.tabs(
    ["🔔 Oportunidades", "📈 Tendência de preço", "🗺️ Mercado", "ℹ️ Sobre o negócio"]
)
with tab_oportunidades:
    secao_alertas()
with tab_tendencia:
    secao_tendencia()
with tab_mercado:
    secao_mercado()
with tab_sobre:
    secao_sobre()
