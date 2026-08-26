import pandas as pd
import streamlit as st

from charts import grafico_dispersao_hz
from common.config import settings
from common.stats import avaliar_preco, medianas_todos_grupos
from queries import carregar_ativos

st.set_page_config(page_title="Monitor Gamer — Grande Vitória", layout="wide")
st.title("Monitor Gamer — Inteligência de mercado (OLX-ES)")


@st.fragment(run_every=60)
def secao_alertas() -> None:
    st.header("🔔 Alertas de oportunidade")
    df = carregar_ativos()
    if df.empty:
        st.info("Ainda sem dados coletados.")
        return

    # mediana de cada grupo (marca, tipo_monitor) calculada 1x por refresh,
    # não 1x por anúncio -- antes isso era 1 conexão SQLite nova por LINHA
    # a cada 60s (até ~290 conexões por refresh). Ver common/stats.py.
    medianas = medianas_todos_grupos()
    avaliacoes = [
        avaliar_preco(preco, medianas[(marca, tipo)])
        if pd.notna(preco) and (marca, tipo) in medianas
        else None
        for preco, marca, tipo in zip(df["preco"], df["marca"], df["tipo_monitor"])
    ]
    df["oportunidade"] = [a.eh_oportunidade if a else False for a in avaliacoes]
    df["apos_negociar"] = [a.custo_apos_negociacao if a else None for a in avaliacoes]
    df["margem_rs"] = [a.margem_rs if a else None for a in avaliacoes]
    df["margem_pct"] = [a.margem_pct * 100 if a else None for a in avaliacoes]

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
             "marca", "hz_exato", "municipio", "url"]
        ].sort_values("margem_pct", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "titulo": "Anúncio",
            "preco": st.column_config.NumberColumn("Anunciado", format="R$ %.0f"),
            "apos_negociar": st.column_config.NumberColumn("Após negociar", format="R$ %.0f"),
            "margem_rs": st.column_config.NumberColumn("Margem", format="R$ %.0f"),
            "margem_pct": st.column_config.NumberColumn("Margem %", format="%.0f%%"),
            "marca": "Marca",
            "hz_exato": "Hz",
            "municipio": "Município",
            "url": st.column_config.LinkColumn("Link", display_text="abrir"),
        },
    )


def secao_historico() -> None:
    st.header("📈 Preço x taxa de atualização (Hz)")
    df = carregar_ativos()
    if df.empty:
        st.info("Sem anúncios ativos ainda.")
        return
    for tipo in df["tipo_monitor"].dropna().unique():
        fig = grafico_dispersao_hz(df, tipo)
        if fig:
            st.plotly_chart(fig, use_container_width=True)


def secao_mapa() -> None:
    st.header("🗺️ Distribuição por região")
    df = carregar_ativos()
    if df.empty:
        return
    st.bar_chart(df["municipio"].value_counts())


def secao_resumo() -> None:
    st.header("📋 Resumo executivo")
    df = carregar_ativos()
    if df.empty:
        return
    col1, col2, col3 = st.columns(3)
    col1.metric("Anúncios ativos", len(df))
    col2.metric("Preço mediano", f"R$ {df['preco'].median():.0f}")
    col3.metric("Marcas distintas", df["marca"].nunique())


secao_alertas()
secao_historico()
secao_mapa()
secao_resumo()
