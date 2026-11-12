"""Mesmo contrato de tests/test_armazenamento.py, no backend S3 -- com moto
(S3 falso em memória: sem rede, sem conta, sem credencial real). Pulado se
boto3/moto não estiverem instalados (requirements-pipeline.txt)."""

import pytest

from common.config import Settings
from pipeline.armazenamento import ArmazenamentoS3, armazenamento_padrao

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")


@pytest.fixture
def s3_falso(monkeypatch):
    # Credencial de mentira só pro boto3 não procurar uma de verdade na máquina.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "teste")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "teste")
    with moto.mock_aws():
        yield


def test_s3_mesmo_contrato_do_local(s3_falso):
    cfg = Settings(_env_file=None, lake_backend="s3", lake_s3_bucket="olx-lake-teste")
    arm = armazenamento_padrao(cfg)  # cria o bucket se não existir
    assert isinstance(arm, ArmazenamentoS3)

    arm.gravar("bronze/olx/iphone/uf=ES/a.json", b'{"ok": 1}')
    arm.gravar("prata/olx/iphone/uf=ES/a.jsonl", b"x")

    assert arm.ler("bronze/olx/iphone/uf=ES/a.json") == b'{"ok": 1}'
    assert arm.existe("bronze/olx/iphone/uf=ES/a.json")
    assert not arm.existe("bronze/nao/existe.json")
    assert arm.listar("bronze/") == ["bronze/olx/iphone/uf=ES/a.json"]


def test_s3_garantir_bucket_e_idempotente(s3_falso):
    cliente = boto3.client("s3", region_name="us-east-1")
    arm = ArmazenamentoS3("bucket-x", cliente)
    arm.garantir_bucket()
    arm.garantir_bucket()  # segunda vez não pode falhar
    assert "bucket-x" in [b["Name"] for b in cliente.list_buckets()["Buckets"]]


def test_s3_fora_de_us_east_1_usa_location_constraint(s3_falso):
    cliente = boto3.client("s3", region_name="sa-east-1")
    ArmazenamentoS3("bucket-sp", cliente).garantir_bucket("sa-east-1")
    assert cliente.get_bucket_location(Bucket="bucket-sp")["LocationConstraint"] == "sa-east-1"
