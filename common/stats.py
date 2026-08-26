"""Camada estatística e de economia da oportunidade.

Decide se um anúncio é oportunidade comparando o preço com a MEDIANA
de mercado do mesmo agrupamento (marca + tipo de monitor) — mediana, não
média, porque é mais robusta a um anúncio de brincadeira por R$1 ou um
outlier de loja profissional inflando o grupo.

A mediana é calculada sobre `anuncios` (1 linha por anúncio *ainda
ativo*), não sobre um histórico bruto — cada anúncio pesa exatamente 1
vez, não importa há quantas rodadas está no ar. Ver `common/storage.py`.

O preço anunciado não é o preço final — na OLX, negociar (\"chorar\"
por desconto) faz parte do fluxo normal de compra. `avaliar()` por isso
não devolve só um booleano: devolve o preço anunciado, uma estimativa
de custo já descontando a negociação esperada (`desconto_negociacao_esperado`
em config.py) e a margem daí até a mediana do grupo — que é a economia
real por trás do alerta, não só "abaixo da mediana ou não". A decisão de
"é oportunidade" continua comparando o preço ANUNCIADO (o único número
verificável antes de negociar) com a mediana; o desconto de negociação
entra só na estimativa de margem que acompanha o alerta.

Limitação real, documentada em vez de escondida: nos primeiros dias, com
menos de `oportunidade_amostra_minima` anúncios *distintos* ativos num
grupo, nenhum alerta de oportunidade sai — não dá pra confiar numa
mediana com 2 ou 3 pontos. O README explica isso.
"""

import statistics
from dataclasses import dataclass

from common.config import settings
from common.storage import get_connection


def preco_mediano_grupo(marca: str | None, tipo_monitor: str | None) -> float | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT preco FROM anuncios
            WHERE marca IS ? AND tipo_monitor IS ? AND preco IS NOT NULL AND ativo = 1
            """,
            (marca, tipo_monitor),
        )
        precos = [r[0] for r in cursor.fetchall()]
    if len(precos) < settings.oportunidade_amostra_minima:
        return None
    return statistics.median(precos)


def medianas_todos_grupos() -> dict[tuple[str | None, str | None], float]:
    """Mediana de preço de TODOS os grupos (marca, tipo_monitor) de uma vez
    só, numa única consulta — usado pelo dashboard pra avaliar ~250+ linhas
    sem abrir uma conexão SQLite nova por linha a cada refresh de 60s
    (era exatamente isso que `eh_oportunidade` fazia antes, chamada dentro
    de um `df.apply`)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT marca, tipo_monitor, preco FROM anuncios WHERE ativo = 1 AND preco IS NOT NULL"
        )
        linhas = cursor.fetchall()
    grupos: dict[tuple[str | None, str | None], list[float]] = {}
    for marca, tipo, preco in linhas:
        grupos.setdefault((marca, tipo), []).append(preco)
    return {
        chave: statistics.median(precos)
        for chave, precos in grupos.items()
        if len(precos) >= settings.oportunidade_amostra_minima
    }


@dataclass
class Avaliacao:
    preco: float
    mediana: float
    custo_apos_negociacao: float
    margem_rs: float
    margem_pct: float
    eh_oportunidade: bool


def avaliar_preco(preco: float, mediana: float) -> Avaliacao:
    """A matemática de `avaliar()`, isolada pra quem já tem a mediana em
    mãos (o dashboard, que busca todas de uma vez com `medianas_todos_grupos`)
    e não precisa de uma consulta nova por anúncio."""
    custo = preco * (1 - settings.desconto_negociacao_esperado)
    margem_rs = mediana - custo
    margem_pct = margem_rs / custo if custo > 0 else 0.0
    return Avaliacao(
        preco=preco,
        mediana=mediana,
        custo_apos_negociacao=custo,
        margem_rs=margem_rs,
        margem_pct=margem_pct,
        eh_oportunidade=preco <= mediana * settings.oportunidade_limiar,
    )


def avaliar(preco: float | None, marca: str | None, tipo_monitor: str | None) -> Avaliacao | None:
    """Ponto de entrada pra avaliar UM anúncio (usado pelo scraper, que
    processa poucos anúncios por rodada — uma consulta por anúncio aqui não
    é o gargalo que era no dashboard). None quando falta preço ou a amostra
    do grupo ainda é pequena demais pra confiar na mediana."""
    if preco is None:
        return None
    mediana = preco_mediano_grupo(marca, tipo_monitor)
    if mediana is None:
        return None
    return avaliar_preco(preco, mediana)
