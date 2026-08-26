import pandas as pd
import streamlit as st

from charts import grafico_dispersao_hz, grafico_tendencia_quedas
from common.config import settings
from common.stats import avaliar_preco, margem_e_confiavel, medianas_todos_grupos
from queries import carregar_ativos, carregar_quedas_precos

st.set_page_config(page_title="Monitor Gamer + iPhone — Grande Vitória", layout="wide")
st.title("Inteligência de mercado (OLX-ES)")


def _com_avaliacao(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona oportunidade/preço pós-negociação/margem a um df de anúncios
    ativos -- usado tanto pelos KPIs do topo quanto pela aba de alertas, pra
    calcular a mediana de cada grupo 1x (não 1x por linha). Agrupa por
    (categoria, grupo), não (marca, tipo) -- ver common/stats.py."""
    df = df.copy()
    medianas = medianas_todos_grupos()
    avaliacoes = [
        avaliar_preco(preco, medianas[(categoria, grupo)])
        if pd.notna(preco) and (categoria, grupo) in medianas and margem_e_confiavel(condicao, titulo)
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


@st.fragment(run_every=60)
def secao_kpis(categoria: str | None) -> None:
    df = carregar_ativos(categoria)
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return
    df = _com_avaliacao(df)
    oportunidades = df[df["oportunidade"]]
    quedas_7d = carregar_quedas_precos(dias=7, categoria=categoria)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Anúncios ativos", len(df))
    col2.metric("Oportunidades agora", len(oportunidades))
    col3.metric(
        "Margem mediana (oportunidades)",
        f"R$ {oportunidades['margem_rs'].median():.0f}" if not oportunidades.empty else "—",
    )
    col4.metric("Quedas de preço (7 dias)", len(quedas_7d))


@st.fragment(run_every=60)
def secao_alertas(categoria: str | None) -> None:
    df = carregar_ativos(categoria)
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
            ["categoria", "titulo", "preco", "apos_negociar", "margem_rs", "margem_pct",
             "condicao", "marca", "municipio", "url"]
        ].sort_values("margem_pct", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "categoria": "Categoria",
            "titulo": "Anúncio",
            "preco": st.column_config.NumberColumn("Anunciado", format="R$ %.0f"),
            "apos_negociar": st.column_config.NumberColumn("Após negociar", format="R$ %.0f"),
            "margem_rs": st.column_config.NumberColumn("Margem", format="R$ %.0f"),
            "margem_pct": st.column_config.NumberColumn("Margem %", format="%.0f%%"),
            "condicao": "Condição",
            "marca": "Marca",
            "municipio": "Município",
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_tendencia(categoria: str | None) -> None:
    quedas = carregar_quedas_precos(dias=30, categoria=categoria)
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


def secao_mercado(categoria: str | None) -> None:
    df = carregar_ativos(categoria)
    if df.empty:
        st.info("Sem anúncios ativos ainda.")
        return

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
    coluna_agrupamento = "grupo" if categoria == "iphone" else "marca"
    st.bar_chart(df[coluna_agrupamento].value_counts().head(10))


def secao_sobre() -> None:
    st.markdown(
        rf"""
Sistema de arbitragem informacional: monitora anúncios (monitor gamer e
iPhone, por enquanto) na OLX (Grande Vitória/ES) a cada
{settings.scrape_interval_minutes} min, calcula a mediana de mercado de
cada grupo (marca+tipo pra monitor, modelo+armazenamento pra iPhone), e
avisa quando um anúncio aparece — ou baixa de preço — a
**{settings.oportunidade_limiar:.0%} da mediana do grupo ou menos**.

**A tese:** parte do mercado de usados é ineficiente — vendedor urgente ou
desinformado anuncia abaixo do preço justo. Achar isso manualmente, na hora
certa, não escala; um scraper de baixa frequência sim.

**O que o sistema NÃO faz:** não decide comprar por você, não avalia o item
fisicamente, não substitui negociar. O alerta já desconta
{settings.desconto_negociacao_esperado:.0%} de negociação esperada na
margem estimada, mas o número real só se confirma na conversa com o vendedor.

**Onde focar em monitor**, pelos dados reais do próprio catálogo: entre
R\$300 e R\$600, em estado Novo ou Usado-Excelente — maior volume de
anúncios e melhor relação margem/risco do que ticket único mais caro ou
faixa abaixo de R\$300 (mais risco de defeito).

Análise completa, com os números que sustentam essa recomendação:
[Raio-X do Monitor Gamer](https://claude.ai/code/artifact/333187dc-24c9-4d68-8718-eed4fb54de7b)
        """
    )


categoria_label = st.radio("Categoria", ["Monitor", "iPhone", "Todas"], horizontal=True)
categoria = {"Monitor": "monitor", "iPhone": "iphone", "Todas": None}[categoria_label]

secao_kpis(categoria)

tab_oportunidades, tab_tendencia, tab_mercado, tab_sobre = st.tabs(
    ["🔔 Oportunidades", "📈 Tendência de preço", "🗺️ Mercado", "ℹ️ Sobre o negócio"]
)
with tab_oportunidades:
    secao_alertas(categoria)
with tab_tendencia:
    secao_tendencia(categoria)
with tab_mercado:
    secao_mercado(categoria)
with tab_sobre:
    secao_sobre()
