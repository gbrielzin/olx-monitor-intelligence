import pytest

from common.config import Settings
from pipeline.armazenamento import ArmazenamentoLocal, armazenamento_padrao


def test_local_grava_le_lista_e_existe(tmp_path):
    arm = ArmazenamentoLocal(tmp_path / "lake")
    arm.gravar("bronze/olx/iphone/uf=ES/a.json", b"{}")
    arm.gravar("prata/olx/iphone/uf=ES/a.jsonl", b"x")

    assert arm.ler("bronze/olx/iphone/uf=ES/a.json") == b"{}"
    assert arm.existe("bronze/olx/iphone/uf=ES/a.json")
    assert not arm.existe("bronze/nao/existe.json")
    assert arm.listar("bronze/") == ["bronze/olx/iphone/uf=ES/a.json"]
    assert arm.listar() == ["bronze/olx/iphone/uf=ES/a.json", "prata/olx/iphone/uf=ES/a.jsonl"]


def test_local_lista_vazio_quando_pasta_nao_existe(tmp_path):
    assert ArmazenamentoLocal(tmp_path / "nada").listar() == []


def test_local_sobrescreve_sem_deixar_temporario(tmp_path):
    arm = ArmazenamentoLocal(tmp_path)
    arm.gravar("ouro/x.csv", b"1")
    arm.gravar("ouro/x.csv", b"2")
    assert arm.ler("ouro/x.csv") == b"2"
    assert arm.listar() == ["ouro/x.csv"]


@pytest.mark.parametrize("chave", ["../fora.json", "bronze/../../fora.json", "/abs.json", "a\\b.json", ""])
def test_chave_malformada_nao_escreve_fora_do_lake(tmp_path, chave):
    with pytest.raises(ValueError):
        ArmazenamentoLocal(tmp_path / "lake").gravar(chave, b"x")


def test_fabrica_escolhe_local_por_padrao(tmp_path):
    arm = armazenamento_padrao(Settings(_env_file=None, lake_dir=str(tmp_path)))
    assert isinstance(arm, ArmazenamentoLocal)


def test_fabrica_recusa_backend_desconhecido():
    with pytest.raises(ValueError, match="LAKE_BACKEND"):
        armazenamento_padrao(Settings(_env_file=None, lake_backend="ftp"))
