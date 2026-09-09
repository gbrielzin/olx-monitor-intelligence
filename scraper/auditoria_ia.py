"""Auditoria de anúncios novos via LLM -- camada opcional em cima do
checkpoint de sanidade (sanity.py).

Diferença de sanity.py: sanity.py julga a RODADA inteira (dá pra confiar
nela?) e pode descartar a coleta inteira; isto julga um ANÚNCIO individual
(o que foi extraído bate com o que o título diz?) e nunca descarta nada --
só grava um sinal em `auditoria_ia` pro usuário revisar na aba de auditoria
do dashboard, do jeito que ele já confere manualmente contra a OLX de
verdade (ver feedback registrado sobre validar oportunidade contra anúncio
real).

Desativado por padrão (settings.ia_auditoria_ativa=False) -- precisa de
anthropic_api_key preenchida pra ligar. Roda só nos anúncios NOVOS de cada
rodada, nunca nos que só seguem ativos: rodando em 3 categorias x até 10
páginas por rodada, auditar todo anúncio a cada ciclo custaria caro rápido
pra nenhum ganho (o que já foi auditado não muda). Chamada sempre envolvida
em try/except no scraper/main.py, mesmo espírito de
notifier.py/grava_snapshot_diario: falha de API nunca pode derrubar a
rodada nem os alertas.
"""

import logging

import anthropic
from pydantic import BaseModel

from common.config import settings
from common.storage import get_connection

logger = logging.getLogger(__name__)

# Só os campos específicos de categoria valem comparar contra o título --
# os campos comuns (município, vendedor...) não vêm do texto livre do
# vendedor, então uma IA não teria como julgar inconsistência neles.
_CAMPOS_POR_CATEGORIA = {
    "monitor": ("marca", "condicao", "polegadas", "resolucao_max", "faixa_hz", "hz_exato", "tipo_monitor", "curvo"),
    "iphone": ("marca", "condicao", "modelo", "armazenamento_gb", "cor", "saude_bateria"),
    "computador": ("marca", "condicao", "cpu_marca", "cpu_modelo", "ram_gb", "armazenamento_gb", "inclui_monitor"),
}

_SYSTEM = (
    "Você audita anúncios de revenda extraídos automaticamente da OLX. "
    "Compare o título (texto livre, escrito pelo vendedor) com os campos "
    "estruturados que um parser extraiu. Aponte só inconsistência real "
    "entre o que o título descreve e o que foi extraído (ex.: título cita "
    "Ryzen 7 mas cpu_modelo extraído é Intel i3; título cita 128GB mas "
    "armazenamento_gb extraído é 32; preço muito destoante do que o título "
    "descreve). Não invente problema: sem inconsistência clara, "
    "inconsistente=false e motivo vazio. resumo é sempre 1 frase curta "
    "descrevendo o anúncio, existindo inconsistência ou não."
)

# Schema minimalista escrito à mão (em vez de AuditoriaResultado.model_json_schema())
# pra bater exatamente com o formato que a API espera em output_config.format --
# o schema auto-gerado pelo pydantic inclui chaves extras (title, $defs) que
# não têm exemplo documentado de aceitação aqui.
_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "inconsistente": {"type": "boolean"},
        "motivo": {"type": "string"},
        "resumo": {"type": "string"},
    },
    "required": ["inconsistente", "motivo", "resumo"],
    "additionalProperties": False,
}


class AuditoriaResultado(BaseModel):
    inconsistente: bool
    motivo: str
    resumo: str


def _monta_prompt(ad) -> str:
    campos = _CAMPOS_POR_CATEGORIA.get(ad.categoria, ())
    extraido = {c: getattr(ad, c, None) for c in campos}
    preco = f"R$ {ad.preco:.0f}" if ad.preco is not None else "não informado"
    return (
        f"Categoria: {ad.categoria}\n"
        f"Título: {ad.titulo}\n"
        f"Preço: {preco}\n"
        f"Campos extraídos: {extraido}"
    )


def auditar_anuncio(ad) -> None:
    """Chama o LLM 1x pro anúncio `ad` (recém-visto) e grava o resultado em
    `auditoria_ia`. Levanta a exceção pro chamador decidir o que fazer --
    ver docstring do módulo sobre onde isso é envolvido em try/except."""
    if not settings.ia_auditoria_ativa:
        return
    if not settings.anthropic_api_key:
        logger.warning("ia_auditoria_ativa=True mas anthropic_api_key vazia -- pulando auditoria.")
        return

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.ia_modelo,
        max_tokens=1024,
        system=_SYSTEM,
        # Classificação simples e de alto volume (roda em todo anúncio novo,
        # de 3 categorias, a cada rodada) -- não é o tipo de tarefa que se
        # beneficia de effort alto.
        output_config={"format": {"type": "json_schema", "schema": _JSON_SCHEMA}, "effort": "low"},
        messages=[{"role": "user", "content": _monta_prompt(ad)}],
    )
    texto = next(b.text for b in response.content if b.type == "text")
    resultado = AuditoriaResultado.model_validate_json(texto)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO auditoria_ia
                (listing_id, plataforma, inconsistente, motivo, resumo, modelo, verificado_em)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT (listing_id, plataforma) DO UPDATE SET
                inconsistente=excluded.inconsistente,
                motivo=excluded.motivo,
                resumo=excluded.resumo,
                modelo=excluded.modelo,
                verificado_em=excluded.verificado_em
            """,
            (
                ad.listing_id, ad.plataforma, int(resultado.inconsistente),
                resultado.motivo, resultado.resumo, settings.ia_modelo,
            ),
        )
