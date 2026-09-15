"""Resumo diário agentic: 1x/dia, junta oportunidades ativas de iPhone
(todas as regiões configuradas) e tendência de preço (quedas reais nas
últimas 24h), e sugestão de por onde negociar primeiro, e pede pro LLM
escrever um resumo corrido -- manda por Telegram, só pro chat pessoal
(não replica por região, é ferramenta de operador).

Diferença de auditoria_ia.py: lá o LLM julga UM anúncio por vez, sem
síntese nenhuma. Aqui o LLM recebe o panorama do dia inteiro e decide
sozinho como organizar/priorizar o texto -- orquestra dado + prompt +
formatação sem um template fixo por trás, o mais "agente" dos dois.

Desativado por padrão (settings.resumo_diario_ativo=False) -- precisa de
anthropic_api_key preenchida. Agendado 1x/dia via APScheduler em
scraper/main.py (settings.resumo_diario_hora_utc). Nunca decide nada
sozinho (não compra, não recalcula oportunidade além do que
common/stats.py já decidiu) -- só resume o que já está no banco.
"""

import logging

import anthropic

from common.config import settings
from common.stats import avaliar_preco, margem_e_confiavel, medianas_todos_grupos
from common.storage import get_connection
from notifier import enviar_telegram

logger = logging.getLogger(__name__)

_CATEGORIAS = ("iphone",)  # monitor/computador pararam de ser coletados


def _ativos_por_categoria(categoria: str) -> list[dict]:
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT titulo, preco, grupo, condicao, municipio, url FROM anuncios "
            "WHERE categoria = ? AND ativo = 1 AND preco IS NOT NULL",
            (categoria,),
        )
        colunas = ("titulo", "preco", "grupo", "condicao", "municipio", "url")
        return [dict(zip(colunas, row)) for row in cursor.fetchall()]


def _quedas_ultimas_24h(categoria: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM historico_precos h "
            "JOIN anuncios a ON a.listing_id = h.listing_id AND a.plataforma = h.plataforma "
            "WHERE a.categoria = ? AND h.preco_anterior IS NOT NULL AND h.preco < h.preco_anterior "
            "AND h.registrado_em >= datetime('now', '-1 day')",
            (categoria,),
        ).fetchone()[0]


def _panorama() -> dict:
    """Monta o panorama do dia (hoje só iPhone, mas mantém o loop por
    categoria -- menor diff se outra categoria voltar a ser coletada) --
    oportunidades ativas de TODAS as regiões configuradas juntas, ordenadas
    por margem (o `grupo` de cada anúncio já vem prefixado com a UF, ver
    common/schema.py, então o panorama naturalmente reflete cada região
    sem precisar filtrar por ela aqui), e quantas quedas de preço reais
    bateram nas últimas 24h. Reaproveita a mesma régua de oportunidade/
    margem de common/stats.py (medianas_todos_grupos + avaliar_preco), do
    jeito que o dashboard já faz em `_com_avaliacao` -- sem depender de
    pandas aqui (scraper não tem pandas nas dependências)."""
    medianas = medianas_todos_grupos()
    panorama = {}
    for categoria in _CATEGORIAS:
        ativos = _ativos_por_categoria(categoria)
        oportunidades = []
        for ad in ativos:
            chave = (categoria, ad["grupo"])
            if chave not in medianas:
                continue
            if not margem_e_confiavel(ad["condicao"], ad["titulo"], ad["preco"], categoria):
                continue
            av = avaliar_preco(ad["preco"], medianas[chave], categoria)
            if av.eh_oportunidade:
                oportunidades.append({**ad, "margem_rs": av.margem_rs, "margem_pct": av.margem_pct})
        oportunidades.sort(key=lambda o: o["margem_pct"], reverse=True)
        panorama[categoria] = {
            "ativos": len(ativos),
            "oportunidades": oportunidades[:5],
            "total_oportunidades": len(oportunidades),
            "quedas_24h": _quedas_ultimas_24h(categoria),
        }
    return panorama


def _monta_prompt(panorama: dict) -> str:
    linhas = []
    for categoria, dados in panorama.items():
        linhas.append(
            f"\n## {categoria}\n"
            f"Anúncios ativos: {dados['ativos']}\n"
            f"Oportunidades agora: {dados['total_oportunidades']}\n"
            f"Quedas de preço reais nas últimas 24h: {dados['quedas_24h']}\n"
        )
        if dados["oportunidades"]:
            linhas.append("Top oportunidades (título | preço | margem | condição | município | link):")
            for o in dados["oportunidades"]:
                linhas.append(
                    f"\n- {o['titulo']} | R$ {o['preco']:.0f} | R$ {o['margem_rs']:.0f} "
                    f"({o['margem_pct']:.0%}) | {o['condicao'] or '?'} | {o['municipio'] or '?'} | {o['url']}"
                )
    return "".join(linhas)


_SYSTEM = (
    "Você escreve o resumo diário de um sistema de revenda que monitora iPhone na "
    "OLX em múltiplos estados. Quem lê já conhece o sistema -- não explique o que "
    "é, só resuma o dia. Texto corrido curto (poucos parágrafos), em português, "
    "sem markdown (vai direto pro Telegram sem formatação). Estrutura sugerida: "
    "quantas oportunidades no total e em quais cidades/regiões estão concentradas "
    "(cada oportunidade vem com o município do anúncio), como está a tendência de "
    "preço (quedas reais nas últimas 24h), e por qual anúncio específico começar a "
    "negociar hoje e por quê (cite título, preço, margem e município). Se não "
    "houver oportunidade nenhuma, diga isso em 1 frase, sem inventar dado. Termine "
    "com 1 frase prática do que fazer primeiro."
)


def _gera_resumo(panorama: dict) -> str:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ia_modelo,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _monta_prompt(panorama)}],
    )
    return next(b.text for b in response.content if b.type == "text")


def enviar_resumo_diario() -> None:
    """Ponto de entrada agendado (scraper/main.py). Nunca levanta pro
    scheduler -- mesmo espírito de auditoria_ia.py/grava_snapshot_diario:
    uma falha aqui não pode afetar as rodadas de coleta que rodam no
    mesmo processo."""
    if not settings.resumo_diario_ativo:
        return
    if not settings.anthropic_api_key:
        logger.warning("resumo_diario_ativo=True mas anthropic_api_key vazia -- pulando resumo diário.")
        return

    try:
        panorama = _panorama()
        texto = _gera_resumo(panorama)
        enviar_telegram(f"☀️ Resumo diário\n\n{texto}")
    except Exception as e:
        logger.error("Falha ao gerar/enviar resumo diário: %s", e)
