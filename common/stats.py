"""Camada estatística e de economia da oportunidade.

Decide se um anúncio é oportunidade comparando o preço com a MEDIANA
de mercado do mesmo agrupamento (marca + tipo de monitor) — mediana, não
média, porque é mais robusta a um anúncio de brincadeira por R$1 ou um
outlier de loja profissional inflando o grupo.

A mediana é calculada sobre `anuncios` (1 linha por anúncio *ainda
ativo*), não sobre um histórico bruto — cada anúncio pesa exatamente 1
vez, não importa há quantas rodadas está no ar. Ver `common/storage.py`.

Anúncio de peça/sucata é excluído dos dois lados da conta — da mediana do
grupo (não é "mercado de unidade funcionando", contaminaria o preço justo)
e da própria avaliação (não dá pra comparar o preço de uma peça quebrada
com a mediana de uma unidade boa e chamar a diferença de "margem": R$40
por um monitor quebrado não vira R$350 de revenda). `margem_e_confiavel()`
usa DOIS sinais pra decidir isso, não um: o campo estruturado `condicao`
("Com defeito ou avarias") E um regex sobre o título. Um sozinho não
bastava — visto ao vivo, testando o dashboard: título "COM DEFEITO NÃO
LIGA" com `condicao` estruturada = "Usado - Excelente" (o vendedor não
marcou certo no formulário da OLX). Sem o segundo sinal, esse anúncio
continuava aparecendo como a "melhor oportunidade" do catálogo.

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

import re
import statistics
from dataclasses import dataclass

from common.config import settings
from common.storage import get_connection

_CONDICAO_SUCATA = "Com defeito ou avarias"

# Frases comuns em título de anúncio de monitor quebrado/pra peça na OLX --
# pega o caso em que o vendedor não marcou "com defeito" no campo
# estruturado mas descreveu o problema no título de qualquer jeito.
_TITULO_DEFEITO_PATTERN = re.compile(
    r"n[ãa]o\s+liga|n[ãa]o\s+funciona|com\s+defeito|quebrad[oa]|trincad[oa]|"
    r"pra\s+pe[çc]a|para\s+pe[çc]as?|\bsucata\b|sem\s+imagem|avariad[oa]",
    re.IGNORECASE,
)


def margem_e_confiavel(condicao: str | None, titulo: str = "") -> bool:
    """False quando o preço não é comparável ao de um anúncio funcionando.
    Mesma regra vale pra decidir quem entra na mediana do grupo e pra
    decidir se UM anúncio específico pode ser avaliado contra ela — os
    dois lados têm que usar o mesmo critério, senão a margem "explode" ao
    comparar preço de sucata com mediana de unidade funcionando."""
    if condicao == _CONDICAO_SUCATA:
        return False
    if titulo and _TITULO_DEFEITO_PATTERN.search(titulo):
        return False
    return True


def preco_mediano_grupo(marca: str | None, tipo_monitor: str | None) -> float | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT preco, condicao, titulo FROM anuncios
            WHERE marca IS ? AND tipo_monitor IS ? AND preco IS NOT NULL AND ativo = 1
            """,
            (marca, tipo_monitor),
        )
        linhas = cursor.fetchall()
    precos = [preco for preco, condicao, titulo in linhas if margem_e_confiavel(condicao, titulo)]
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
            "SELECT marca, tipo_monitor, preco, condicao, titulo FROM anuncios "
            "WHERE ativo = 1 AND preco IS NOT NULL"
        )
        linhas = cursor.fetchall()
    grupos: dict[tuple[str | None, str | None], list[float]] = {}
    for marca, tipo, preco, condicao, titulo in linhas:
        if not margem_e_confiavel(condicao, titulo):
            continue
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
    e não precisa de uma consulta nova por anúncio. Não checa
    condição/título — quem chama direto (dashboard) já filtrou com
    `margem_e_confiavel` antes."""
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


def avaliar(
    preco: float | None,
    marca: str | None,
    tipo_monitor: str | None,
    condicao: str | None = None,
    titulo: str = "",
) -> Avaliacao | None:
    """Ponto de entrada pra avaliar UM anúncio (usado pelo scraper, que
    processa poucos anúncios por rodada — uma consulta por anúncio aqui não
    é o gargalo que era no dashboard). None quando falta preço, o anúncio
    parece peça/sucata (`margem_e_confiavel`), ou a amostra do grupo ainda
    é pequena demais pra confiar na mediana."""
    if preco is None or not margem_e_confiavel(condicao, titulo):
        return None
    mediana = preco_mediano_grupo(marca, tipo_monitor)
    if mediana is None:
        return None
    return avaliar_preco(preco, mediana)
