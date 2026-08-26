"""Schema para anúncios de monitor extraídos da OLX.

Baseado na estrutura real de `properties` encontrada no JSON embutido
(payload RSC do Next.js) das páginas de busca e detalhe da OLX — não em
regex sobre HTML visual. A única regex que sobrevive aqui é para achar o
Hz exato no título, porque o campo estruturado `info_monitors_refresh_rate`
vem como faixa de filtro de busca ("144 Hz ou maior"), não valor exato.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_HZ_PATTERN = re.compile(r"(\d{2,3})\s*hz", re.IGNORECASE)

# Quando o vendedor não acha (ou não seleciona) a marca no dropdown da OLX,
# o campo estruturado vem "Outros" — mas o título quase sempre cita a marca
# real em texto livre ("Monitor Samsung 24 polegadas..."). Sem esse
# fallback, quase metade do catálogo cai no mesmo grupo genérico "Outros",
# o que dilui a mediana usada por common/stats.py pra decidir oportunidade.
# \b (borda de palavra) evita casar marca de 2-3 letras (LG, HP, TCL, MSI)
# no meio de outra palavra.
_MARCAS_CONHECIDAS = (
    "Samsung", "LG", "AOC", "Dell", "Acer", "BenQ", "Lenovo", "HP", "Asus",
    "Philips", "ViewSonic", "Gigabyte", "MSI", "Positivo", "Multilaser",
    "Xiaomi", "TCL", "Sony", "Toshiba", "Compaq",
)
_MARCA_PATTERNS = tuple(
    (marca, re.compile(rf"\b{re.escape(marca)}\b", re.IGNORECASE)) for marca in _MARCAS_CONHECIDAS
)


def _recupera_marca(marca_olx: Optional[str], titulo: str) -> Optional[str]:
    if marca_olx and marca_olx != "Outros":
        return marca_olx
    for marca, pattern in _MARCA_PATTERNS:
        if pattern.search(titulo):
            return marca
    return marca_olx  # mantém None ou "Outros" se nada bateu


class MonitorAd(BaseModel):
    listing_id: int
    plataforma: str = "olx"
    titulo: str
    preco: Optional[float] = None
    preco_antigo: Optional[float] = None
    url: str
    data_publicacao: datetime
    municipio: Optional[str] = None
    bairro: Optional[str] = None
    marca: Optional[str] = None
    condicao: Optional[str] = None
    polegadas: Optional[str] = None
    resolucao_max: Optional[str] = None
    faixa_hz: Optional[str] = None
    hz_exato: Optional[int] = None
    # Optional[str] em vez de Literal: um valor inesperado da OLX não pode
    # derrubar a validação do anúncio inteiro. Ver test_schema.py.
    tipo_tela: Optional[str] = None
    tipo_monitor: Optional[str] = None
    curvo: bool = False
    vendedor_nome: Optional[str] = None
    vendedor_nota: Optional[float] = None
    coletado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("preco", "preco_antigo", mode="before")
    @classmethod
    def _limpa_preco(cls, v):
        """Converte 'R$ 1.250' -> 1250.0. Preço ausente vira None, nunca 0
        — um 0 pareceria uma pechincha impossível e contaminaria a mediana
        usada pela camada de oportunidade."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        digitos = re.sub(r"[^\d]", "", str(v))
        return float(digitos) if digitos else None

    @classmethod
    def from_olx_json(cls, raw: dict) -> "MonitorAd":
        """Constrói o modelo a partir de um item bruto do array `ads` do
        JSON embutido na página da OLX (busca ou detalhe)."""
        props = {p["name"]: p["value"] for p in raw.get("properties", [])}
        titulo = raw.get("subject", "")

        hz_match = _HZ_PATTERN.search(titulo)
        hz_exato = int(hz_match.group(1)) if hz_match else None

        # Fallback: quando o título não menciona Hz, tenta a faixa
        # estruturada. "60 Hz" / "100 Hz" / "120 Hz" já são valores exatos
        # nessa faixa (só "144 Hz ou maior" é faixa de verdade e não
        # produz um número confiável, por isso o regex a ignora).
        if hz_exato is None:
            faixa = props.get("info_monitors_refresh_rate", "") or ""
            faixa_match = _HZ_PATTERN.search(faixa)
            if faixa_match and "maior" not in faixa.lower():
                hz_exato = int(faixa_match.group(1))

        features = props.get("info_monitors_features", "") or ""
        loc = raw.get("locationDetails") or {}
        olx_pay = raw.get("olxPay") or {}

        return cls(
            listing_id=raw["listId"],
            titulo=titulo,
            preco=raw.get("priceValue"),
            preco_antigo=raw.get("oldPrice"),
            url=raw["url"],
            data_publicacao=datetime.fromtimestamp(raw["date"]),
            municipio=loc.get("municipality"),
            bairro=loc.get("neighbourhood"),
            marca=_recupera_marca(props.get("info_monitors_brand"), titulo),
            condicao=props.get("info_monitors_condition"),
            polegadas=props.get("info_monitors_inches"),
            resolucao_max=props.get("info_monitors_max_resolution"),
            faixa_hz=props.get("info_monitors_refresh_rate"),
            hz_exato=hz_exato,
            tipo_tela=props.get("info_monitors_screen_type"),
            tipo_monitor=props.get("info_monitors_type"),
            curvo="Curvo" in features,
            vendedor_nome=olx_pay.get("transactionalSellerName"),
            vendedor_nota=olx_pay.get("transactionalSellerRating"),
        )
