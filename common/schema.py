"""Schema para anúncios extraídos da OLX — monitor e iPhone, hoje.

Baseado na estrutura real de `properties` encontrada no JSON embutido
(payload RSC do Next.js) das páginas de busca e detalhe da OLX — não em
regex sobre HTML visual. Cada categoria tem seu próprio dicionário de
`properties` (`info_monitors_*` pra monitor, `electronics_*`/`cellphone_*`
pra celular) mas o resto do envelope (listId, subject, priceValue, url,
date, locationDetails, olxPay) é idêntico entre categorias — só o
dicionário de specs muda, do jeito que o README original já esperava
("reaproveita quase o parser inteiro, só troca o dicionário de specs").

`categoria` + a property `grupo` são o que deixa `common/stats.py` e
`common/storage.py` genéricos: toda comparação de "isso é oportunidade"
agrupa por (categoria, grupo) em vez de conhecer os campos de cada
categoria — pra monitor, grupo = marca + tipo; pra iPhone, grupo =
modelo + armazenamento (marca sozinha não discrimina nada, é sempre
"Apple").
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


# Mesma lógica de _recupera_marca, mas pra CPU: quando o vendedor não
# preenche o campo estruturado (comum em loja pequena/pessoa física), o
# título quase sempre cita o processador em texto livre ("PC Gamer
# Completo i5...", "Ryzen 5 5500..."). Sem isso, todo anúncio sem essa
# property cai no mesmo grupo genérico "CPU (?)" e corrompe a mediana
# igual "Outros" corrompia a de monitor -- só que pior, porque aqui a
# faixa de preço dentro do grupo genérico é enorme (R$450 a R$2000+).
# "core\s*iN\b" cobre o caso real "Corei3" (sem espaço, sem borda de
# palavra antes do "i") que \bi3\b sozinho não pega.
_CPU_CONHECIDOS = (
    ("Intel Core i9", re.compile(r"\bi9\b|core\s*i9\b", re.IGNORECASE)),
    ("Intel Core i7", re.compile(r"\bi7\b|core\s*i7\b", re.IGNORECASE)),
    ("Intel Core i5", re.compile(r"\bi5\b|core\s*i5\b", re.IGNORECASE)),
    ("Intel Core i3", re.compile(r"\bi3\b|core\s*i3\b", re.IGNORECASE)),
    ("Intel Core 2 Duo", re.compile(r"core\s*2\s*duo", re.IGNORECASE)),
    ("Intel Xeon", re.compile(r"\bxeon\b", re.IGNORECASE)),
    ("Intel Celeron", re.compile(r"\bceleron\b", re.IGNORECASE)),
    ("Intel Pentium", re.compile(r"\bpentium\b", re.IGNORECASE)),
    ("AMD Ryzen 9", re.compile(r"ryzen\s*9\b", re.IGNORECASE)),
    ("AMD Ryzen 7", re.compile(r"ryzen\s*7\b", re.IGNORECASE)),
    ("AMD Ryzen 5", re.compile(r"ryzen\s*5\b", re.IGNORECASE)),
    ("AMD Ryzen 3", re.compile(r"ryzen\s*3\b", re.IGNORECASE)),
    ("AMD Athlon", re.compile(r"\bathlon\b", re.IGNORECASE)),
)


def _recupera_cpu(cpu_olx: Optional[str], titulo: str) -> Optional[str]:
    if cpu_olx:
        return cpu_olx
    for cpu, pattern in _CPU_CONHECIDOS:
        if pattern.search(titulo):
            return cpu
    return cpu_olx  # mantém None se nada bateu


def _limpa_preco_valor(v):
    """Converte 'R$ 1.250' -> 1250.0. Preço ausente vira None, nunca 0 — um
    0 pareceria uma pechincha impossível e contaminaria a mediana usada
    pela camada de oportunidade. Compartilhado entre MonitorAd e IphoneAd."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    digitos = re.sub(r"[^\d]", "", str(v))
    return float(digitos) if digitos else None


class MonitorAd(BaseModel):
    listing_id: int
    plataforma: str = "olx"
    categoria: str = "monitor"
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
    def _valida_preco(cls, v):
        return _limpa_preco_valor(v)

    @property
    def grupo(self) -> str:
        """Chave de comparabilidade — dois anúncios só competem pela mesma
        mediana se tiverem o mesmo grupo. Ver common/stats.py."""
        return f"{self.marca or '?'} · {self.tipo_monitor or '?'}"

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


# "IPHONE 11", "IPHONE 14 PRO MAX", "IPHONE SE 2022", "IPHONE XR"... —
# cobre a nomenclatura real da Apple sem precisar de uma lista fixa de
# modelos que ia ficar desatualizada a cada lançamento novo.
_IPHONE_MODELO_PATTERN = re.compile(
    r"iphone\s*(se\s*20\d{2}|\d{1,2}\s*(?:pro\s*max|pro|plus|mini)?|xr|xs\s*max|xs|x)\b",
    re.IGNORECASE,
)


def _normaliza_modelo_iphone(modelo_olx: Optional[str], titulo: str) -> Optional[str]:
    """`electronics_model` normalmente vem limpo ("IPHONE 13 PRO MAX"), mas
    visto ao vivo: às vezes vem lixo sem sentido ('2', '25') -- nesse caso
    cai pro mesmo truque de extrair do título que já existe pra marca/Hz
    de monitor."""
    if modelo_olx:
        limpo = modelo_olx.strip()
        if len(limpo) >= 4 and not limpo.isdigit():
            return limpo.upper()
    match = _IPHONE_MODELO_PATTERN.search(titulo)
    if match:
        return f"IPHONE {match.group(1).strip().upper()}"
    return modelo_olx


def _parse_armazenamento(valor: Optional[str]) -> Optional[int]:
    """'128GB' -> 128. '1TB' -> 1024 (normaliza pra GB, senão 1TB parece
    "menor" que 512GB numa ordenação numérica)."""
    if not valor:
        return None
    valor_lower = valor.lower()
    digitos = re.sub(r"[^\d]", "", valor_lower)
    if not digitos:
        return None
    numero = int(digitos)
    return numero * 1024 if "tb" in valor_lower else numero


class IphoneAd(BaseModel):
    listing_id: int
    plataforma: str = "olx"
    categoria: str = "iphone"
    titulo: str
    preco: Optional[float] = None
    preco_antigo: Optional[float] = None
    url: str
    data_publicacao: datetime
    municipio: Optional[str] = None
    bairro: Optional[str] = None
    marca: Optional[str] = "Apple"
    condicao: Optional[str] = None
    modelo: Optional[str] = None
    armazenamento_gb: Optional[int] = None
    cor: Optional[str] = None
    saude_bateria: Optional[str] = None
    vendedor_nome: Optional[str] = None
    vendedor_nota: Optional[float] = None
    coletado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("preco", "preco_antigo", mode="before")
    @classmethod
    def _valida_preco(cls, v):
        return _limpa_preco_valor(v)

    @property
    def grupo(self) -> str:
        modelo = self.modelo or "iPhone (modelo?)"
        if self.armazenamento_gb:
            return f"{modelo} · {self.armazenamento_gb}GB"
        return modelo

    @classmethod
    def from_olx_json(cls, raw: dict) -> "IphoneAd":
        """Baseado na estrutura real de `properties` de um anúncio de
        celular na OLX (categoria 'Celulares e Smartphones', id 3060) —
        mesmo envelope de monitor, dicionário de specs diferente."""
        props = {p["name"]: p["value"] for p in raw.get("properties", [])}
        titulo = raw.get("subject", "")
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
            marca=props.get("electronics_brand") or "Apple",
            condicao=props.get("electronics_condition"),
            modelo=_normaliza_modelo_iphone(props.get("electronics_model"), titulo),
            armazenamento_gb=_parse_armazenamento(props.get("cellphone_storage")),
            cor=props.get("electronics_color"),
            saude_bateria=props.get("electronics_battery_health"),
            vendedor_nome=olx_pay.get("transactionalSellerName"),
            vendedor_nota=olx_pay.get("transactionalSellerRating"),
        )


class ComputadorAd(BaseModel):
    listing_id: int
    plataforma: str = "olx"
    categoria: str = "computador"
    titulo: str
    preco: Optional[float] = None
    preco_antigo: Optional[float] = None
    url: str
    data_publicacao: datetime
    municipio: Optional[str] = None
    bairro: Optional[str] = None
    marca: Optional[str] = None
    condicao: Optional[str] = None
    cpu_marca: Optional[str] = None
    cpu_modelo: Optional[str] = None
    ram_gb: Optional[int] = None
    armazenamento_gb: Optional[int] = None  # reaproveita o mesmo campo de iPhone -- mesmo conceito
    # True quando a OLX marca "Inclui monitor" nas características, ou o
    # título menciona monitor -- só um SINAL de que o kit tem monitor
    # junto, não dá pra saber marca/tamanho/Hz do monitor incluso a
    # partir daqui, então não entra em nenhuma conta de valor.
    inclui_monitor: bool = False
    vendedor_nome: Optional[str] = None
    vendedor_nota: Optional[float] = None
    coletado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("preco", "preco_antigo", mode="before")
    @classmethod
    def _valida_preco(cls, v):
        return _limpa_preco_valor(v)

    @property
    def grupo(self) -> str:
        """CPU + RAM, não marca -- metade do catálogo vem marca='Outros'
        (monta avulsa/loja pequena) e o que separa um PC de R$800 de um
        de R$3000 é a config, não quem montou."""
        cpu = self.cpu_modelo or "CPU (?)"
        if self.ram_gb:
            return f"{cpu} · {self.ram_gb}GB RAM"
        return cpu

    @classmethod
    def from_olx_json(cls, raw: dict) -> "ComputadorAd":
        """Baseado na estrutura real de `properties` de um anúncio de PC
        completo na OLX (categoria 'Computadores e Desktops')."""
        props = {p["name"]: p["value"] for p in raw.get("properties", [])}
        titulo = raw.get("subject", "")
        loc = raw.get("locationDetails") or {}
        olx_pay = raw.get("olxPay") or {}
        features = (props.get("info_computer_features") or "").lower()

        return cls(
            listing_id=raw["listId"],
            titulo=titulo,
            preco=raw.get("priceValue"),
            preco_antigo=raw.get("oldPrice"),
            url=raw["url"],
            data_publicacao=datetime.fromtimestamp(raw["date"]),
            municipio=loc.get("municipality"),
            bairro=loc.get("neighbourhood"),
            marca=props.get("info_computer_brand"),
            condicao=props.get("info_computer_condition"),
            cpu_marca=props.get("info_computer_cpu_brand"),
            cpu_modelo=_recupera_cpu(props.get("info_computer_cpu_model"), titulo),
            ram_gb=_parse_armazenamento(props.get("info_computer_ram_size")),
            armazenamento_gb=_parse_armazenamento(props.get("info_computer_storage_size")),
            inclui_monitor="monitor" in features or "monitor" in titulo.lower(),
            vendedor_nome=olx_pay.get("transactionalSellerName"),
            vendedor_nota=olx_pay.get("transactionalSellerRating"),
        )
