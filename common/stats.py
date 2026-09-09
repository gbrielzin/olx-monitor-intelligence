"""Camada estatística e de economia da oportunidade.

Decide se um anúncio é oportunidade comparando o preço com a MEDIANA de
mercado do mesmo (categoria, grupo) — mediana, não média, porque é mais
robusta a um anúncio de brincadeira por R$1 ou um outlier de loja
profissional inflando o grupo. `grupo` é calculado por cada schema (ver
common/schema.py: marca+tipo pra monitor, modelo+armazenamento pra
iPhone) — esta camada não precisa saber o que compõe cada grupo, só que
dois anúncios do mesmo grupo competem pela mesma mediana.

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
continuava aparecendo como a "melhor oportunidade" do catálogo. Vale pra
qualquer categoria: iPhone usa exatamente o mesmo vocabulário de condição
da OLX que monitor usa.

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

`grava_snapshot_diario()` e `tendencia_grupo()` estendem isso pra responder
"comparado ao mercado dos últimos N dias", não só "comparado a agora" — mas
dependem de um histórico próprio (`medianas_diarias`) que só existe a partir
de quando o snapshot diário começa a rodar; mesma limitação de amostra
mínima, só que no eixo do tempo em vez do eixo da quantidade.
"""

import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.config import settings
from common.storage import get_connection

_CONDICAO_SUCATA = "Com defeito ou avarias"

# Frases comuns em título de anúncio quebrado/pra peça/de risco na OLX --
# pega o caso em que o vendedor não marcou "com defeito" no campo
# estruturado mas descreveu o problema no título de qualquer jeito.
# Vocabulário genérico o bastante (não fala em "tela") pra servir monitor
# e iPhone, mais alguns termos específicos de celular (bateria, iCloud --
# "(?<!des)bloqueado" pega "bloqueado"/"iCloud bloqueado" sem confundir
# com "desbloqueado", que é o oposto: bom sinal, não defeito).
#
# Isso NUNCA vai cobrir toda frase possível de defeito em português --
# é uma rede de segurança probabilística, não uma garantia. Ver
# margem_e_confiavel() pra reportar caso passe um exemplo nesta rede.
_TITULO_DEFEITO_PATTERN = re.compile(
    r"n[ãa]o\s+liga|n[ãa]o\s+funciona|n[ãa]o\s+carrega|n[ãa]o\s+desbloqueia|"
    r"com\s+defeito|com\s+problemas?|problemas?\s+n[ao]|quebrad[oa]|trincad[oa]|rachad[oa]|"
    r"amassad[oa]|molhad[oa]|pra\s+pe[çc]a|para\s+pe[çc]as?|\bsucata\b|sem\s+imagem|"
    r"avariad[oa]|bateria\s+(viciada|fraca|ruim)|(?<!des)bloqueado|preso\s+no\s+icloud|"
    r"icloud\s+(bloqueado|ativ[oa])",
    re.IGNORECASE,
)


def margem_e_confiavel(
    condicao: str | None,
    titulo: str = "",
    preco: float | None = None,
    categoria: str | None = None,
) -> bool:
    """False quando o preço não é comparável ao de um anúncio funcionando.
    Mesma regra vale pra decidir quem entra na mediana do grupo e pra
    decidir se UM anúncio específico pode ser avaliado contra ela — os
    dois lados têm que usar o mesmo critério, senão a margem "explode" ao
    comparar preço de sucata com mediana de unidade funcionando.

    `preco`/`categoria` são opcionais (todo call site antigo continua
    funcionando sem eles) e cobrem um caso que o texto não pega: preço bom
    demais pra ser real. "iPhone 11 64GB" por R$10, "TROCO POR PC COMPLETO"
    por R$1 -- nenhum menciona defeito, mas nenhum é dado de mercado
    confiável (golpe, erro de digitação, "a combinar"/troca com preço-
    placeholder, item errado na categoria). Ver orcamento_minimo_* em
    config.py."""
    if condicao == _CONDICAO_SUCATA:
        return False
    if titulo and _TITULO_DEFEITO_PATTERN.search(titulo):
        return False
    if preco is not None and categoria is not None:
        piso = getattr(settings, f"orcamento_minimo_{categoria}", None)
        if piso is not None and preco < piso:
            return False
    return True


def preco_mediano_grupo(categoria: str, grupo: str) -> float | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT preco, condicao, titulo FROM anuncios
            WHERE categoria = ? AND grupo = ? AND preco IS NOT NULL AND ativo = 1
            """,
            (categoria, grupo),
        )
        linhas = cursor.fetchall()
    precos = [
        preco for preco, condicao, titulo in linhas
        if margem_e_confiavel(condicao, titulo, preco, categoria)
    ]
    if len(precos) < settings.oportunidade_amostra_minima:
        return None
    return statistics.median(precos)


@dataclass
class GrupoStats:
    mediana: float
    amostra: int


def _grupos_confiaveis(categoria: str | None = None) -> dict[tuple[str, str], GrupoStats]:
    """Mediana + tamanho de amostra de TODOS os grupos de uma vez só, numa
    única consulta — usado pelo dashboard pra avaliar centenas de linhas sem
    abrir uma conexão SQLite nova por linha a cada refresh de 60s (era
    exatamente isso que a versão antiga fazia, chamada dentro de um
    `df.apply`). Chave do dict: (categoria, grupo). Sem `categoria`,
    calcula pra todas de uma vez (monitor, iPhone e computador juntos).

    Função interna — `medianas_todos_grupos()` (mediana só) e
    `grava_snapshot_diario()` (mediana + amostra, pra `medianas_diarias`)
    são as duas fachadas públicas por cima disto, pra não duplicar a mesma
    consulta e o mesmo filtro de sucata em dois lugares."""
    with get_connection() as conn:
        if categoria is None:
            cursor = conn.execute(
                "SELECT categoria, grupo, preco, condicao, titulo FROM anuncios "
                "WHERE ativo = 1 AND preco IS NOT NULL"
            )
        else:
            cursor = conn.execute(
                "SELECT categoria, grupo, preco, condicao, titulo FROM anuncios "
                "WHERE ativo = 1 AND preco IS NOT NULL AND categoria = ?",
                (categoria,),
            )
        linhas = cursor.fetchall()
    grupos: dict[tuple[str, str], list[float]] = {}
    for cat, grupo, preco, condicao, titulo in linhas:
        if not margem_e_confiavel(condicao, titulo, preco, cat):
            continue
        grupos.setdefault((cat, grupo), []).append(preco)
    return {
        chave: GrupoStats(mediana=statistics.median(precos), amostra=len(precos))
        for chave, precos in grupos.items()
        if len(precos) >= settings.oportunidade_amostra_minima
    }


def medianas_todos_grupos(categoria: str | None = None) -> dict[tuple[str, str], float]:
    """Mediana de preço de todos os grupos — ver `_grupos_confiaveis()`.
    Mantido como função separada (em vez de expor `GrupoStats` direto)
    porque é chamado de muitos lugares (dashboard, scraper) que só querem
    o número, não o objeto — trocar a assinatura quebraria todos eles."""
    return {chave: g.mediana for chave, g in _grupos_confiaveis(categoria).items()}


def grava_snapshot_diario(categoria: str | None = None) -> int:
    """Grava a mediana de HOJE (UTC) pra cada (categoria, grupo) com amostra
    confiável, se ainda não existe uma linha de hoje — idempotente por dia,
    seguro de chamar em toda rodada do scraper (não só a primeira do dia).

    Deliberadamente NÃO preso a um horário fixo (ex.: só à meia-noite): a
    coleta roda numa máquina pessoal que dorme/hiberna de forma irregular
    (uptime real medido em ~19-26%, ver OLX_DEEP_DIVE.md seção 7.5) — um
    gatilho de horário fixo perderia o dia inteiro toda vez que a máquina
    estivesse desligada naquele instante. Chamar isto no fim de toda rodada,
    de qualquer categoria, garante que a PRIMEIRA rodada que rodar num dia
    novo — seja qual for o horário — grava o snapshot daquele dia.

    Retorna quantas linhas novas foram gravadas (0 é normal e esperado na
    maioria das chamadas: só a primeira rodada do dia grava algo, as
    seguintes não têm o que fazer).

    É a base de dado que falta pra responder "esse preço é bom comparado ao
    mercado dos últimos N dias", não só "comparado a agora" — ver
    `tendencia_grupo()` — e pra um backtest sem viés de olhar o futuro: sem
    uma foto própria por dia, salva no dia em que ela era verdade, não tem
    como reconstruir com confiança qual era a mediana de mercado num dia que
    já passou (`anuncios` só guarda o estado ATUAL; `historico_precos` só
    guarda quedas de preço, não a distribuição completa do grupo)."""
    hoje = datetime.now(timezone.utc).date().isoformat()
    agora = datetime.now(timezone.utc).isoformat()
    grupos = _grupos_confiaveis(categoria)
    gravados = 0
    with get_connection() as conn:
        for (cat, grupo), g in grupos.items():
            existe = conn.execute(
                "SELECT 1 FROM medianas_diarias WHERE data = ? AND categoria = ? AND grupo = ?",
                (hoje, cat, grupo),
            ).fetchone()
            if existe:
                continue
            conn.execute(
                "INSERT INTO medianas_diarias (data, categoria, grupo, mediana, amostra, registrado_em) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (hoje, cat, grupo, g.mediana, g.amostra, agora),
            )
            gravados += 1
    return gravados


_LIMIAR_TENDENCIA = 0.05  # +-5% entre o primeiro e o último dia da janela = "estável"


@dataclass
class TendenciaGrupo:
    mediana_atual: float
    mediana_periodo: float | None  # None se dias_disponiveis < dias_pedidos -- nunca fabricado
    dias_pedidos: int
    dias_disponiveis: int
    direcao: str  # "subindo" / "caindo" / "estavel" / "indeterminado"
    amostra_periodo: int


def tendencia_grupo(categoria: str, grupo: str, dias: int = 30) -> TendenciaGrupo | None:
    """Compara a mediana ATUAL do grupo (mesma régua de `preco_mediano_grupo`)
    com o histórico gravado por `grava_snapshot_diario()` nos últimos `dias`
    dias corridos. Diferente de olhar só `anuncios` (estado de agora),
    `medianas_diarias` só existe a partir de quando o snapshot diário
    começou a rodar — não há como reconstruir retroativamente o que já
    passou antes disso (ver docstring de `grava_snapshot_diario`).

    None quando a mediana atual não é confiável (mesma regra de amostra
    mínima de sempre) — sem mediana atual, não tem com o que comparar.

    `dias_disponiveis` pode vir menor que `dias` pedido: isso é reportado
    explicitamente no resultado, e `mediana_periodo` fica None nesse caso
    em vez de devolver uma média calculada sobre menos dias do que o
    rótulo "30 dias" sugeriria — ver README/config.py pra outros exemplos
    do mesmo princípio (não fabricar confiança que o dado não sustenta)."""
    mediana_atual = preco_mediano_grupo(categoria, grupo)
    if mediana_atual is None:
        return None

    limite = (datetime.now(timezone.utc).date() - timedelta(days=dias)).isoformat()
    with get_connection() as conn:
        linhas = conn.execute(
            "SELECT data, mediana, amostra FROM medianas_diarias "
            "WHERE categoria = ? AND grupo = ? AND data >= ? ORDER BY data",
            (categoria, grupo, limite),
        ).fetchall()

    dias_disponiveis = len(linhas)
    if dias_disponiveis == 0:
        return TendenciaGrupo(
            mediana_atual=mediana_atual, mediana_periodo=None, dias_pedidos=dias,
            dias_disponiveis=0, direcao="indeterminado", amostra_periodo=0,
        )

    medianas_periodo = [m for _, m, _ in linhas]
    amostra_periodo = sum(a for _, _, a in linhas)

    if dias_disponiveis < 2:
        direcao = "indeterminado"  # 1 ponto só não define tendência nenhuma
    else:
        primeira, ultima = medianas_periodo[0], medianas_periodo[-1]
        if primeira == 0:
            direcao = "indeterminado"
        else:
            variacao = (ultima - primeira) / primeira
            if variacao > _LIMIAR_TENDENCIA:
                direcao = "subindo"
            elif variacao < -_LIMIAR_TENDENCIA:
                direcao = "caindo"
            else:
                direcao = "estavel"

    return TendenciaGrupo(
        mediana_atual=mediana_atual,
        mediana_periodo=statistics.mean(medianas_periodo) if dias_disponiveis >= dias else None,
        dias_pedidos=dias,
        dias_disponiveis=dias_disponiveis,
        direcao=direcao,
        amostra_periodo=amostra_periodo,
    )


@dataclass
class Avaliacao:
    preco: float
    mediana: float
    custo_apos_negociacao: float
    margem_rs: float
    margem_pct: float
    eh_oportunidade: bool


def _dentro_do_orcamento(preco: float, categoria: Optional[str]) -> bool:
    """Teto de preço por categoria -- margem % boa não importa se o preço
    em si está fora do que a pessoa compraria. `categoria=None` (chamador
    que não sabe/não filtra por categoria) não aplica teto nenhum."""
    if categoria is None:
        return True
    teto = getattr(settings, f"orcamento_maximo_{categoria}", None)
    return teto is None or preco <= teto


def avaliar_preco(preco: float, mediana: float, categoria: Optional[str] = None) -> Avaliacao:
    """A matemática de `avaliar()`, isolada pra quem já tem a mediana em
    mãos (o dashboard, que busca todas de uma vez com `medianas_todos_grupos`)
    e não precisa de uma consulta nova por anúncio. Não checa
    condição/título — quem chama direto (dashboard) já filtrou com
    `margem_e_confiavel` antes."""
    custo = preco * (1 - settings.desconto_negociacao_esperado)
    margem_rs = mediana - custo
    margem_pct = margem_rs / custo if custo > 0 else 0.0
    dentro_do_limiar = preco <= mediana * settings.oportunidade_limiar
    return Avaliacao(
        preco=preco,
        mediana=mediana,
        custo_apos_negociacao=custo,
        margem_rs=margem_rs,
        margem_pct=margem_pct,
        eh_oportunidade=dentro_do_limiar and _dentro_do_orcamento(preco, categoria),
    )


def avaliar(
    preco: float | None,
    categoria: str,
    grupo: str,
    condicao: str | None = None,
    titulo: str = "",
) -> Avaliacao | None:
    """Ponto de entrada pra avaliar UM anúncio (usado pelo scraper, que
    processa poucos anúncios por rodada — uma consulta por anúncio aqui não
    é o gargalo que era no dashboard). None quando falta preço, o anúncio
    parece peça/sucata (`margem_e_confiavel`), ou a amostra do grupo ainda
    é pequena demais pra confiar na mediana."""
    if preco is None or not margem_e_confiavel(condicao, titulo, preco, categoria):
        return None
    mediana = preco_mediano_grupo(categoria, grupo)
    if mediana is None:
        return None
    return avaliar_preco(preco, mediana, categoria)
