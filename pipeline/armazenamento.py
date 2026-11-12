"""Armazenamento de objetos com a mesma cara do S3: chave -> bytes.

Duas implementações com a mesma interface:

- `ArmazenamentoLocal`: grava numa pasta (a chave vira caminho de arquivo).
  É o padrão -- roda sem Docker, sem conta, sem credencial.
- `ArmazenamentoS3`: grava num bucket via boto3. Com `endpoint_url`
  apontando pro MinIO (docker-compose, perfil `lake`) roda 100% local; sem
  `endpoint_url`, fala com a AWS de verdade -- o código é o mesmo, só muda
  configuração.

O resto do pipeline (pipeline/camadas.py) só conhece a interface
(`gravar`/`ler`/`listar`/`existe`), nunca sabe onde o arquivo foi parar.
É o mesmo raciocínio de `scraper/fetcher.py`: trocar a infraestrutura mexe
num arquivo só.

boto3 é importado só dentro de `ArmazenamentoS3`/`criar_cliente_s3`: quem
usa o backend local (e o scraper antigo, que nem importa este módulo) não
precisa ter boto3 instalado.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol

from common.config import Settings, settings as settings_padrao


class Armazenamento(Protocol):
    def gravar(self, chave: str, dados: bytes) -> str: ...
    def ler(self, chave: str) -> bytes: ...
    def listar(self, prefixo: str = "") -> list[str]: ...
    def existe(self, chave: str) -> bool: ...


def _valida_chave(chave: str) -> str:
    """Chave no formato do S3 ("bronze/olx/.../x.json"). Barra `..` e caminho
    absoluto: no backend local a chave vira caminho de arquivo, e uma chave
    malformada não pode escrever fora da pasta do lake."""
    if not chave or chave.startswith("/") or "\\" in chave:
        raise ValueError(f"chave inválida: {chave!r}")
    if ".." in PurePosixPath(chave).parts:
        raise ValueError(f"chave inválida (contém '..'): {chave!r}")
    return chave


class ArmazenamentoLocal:
    def __init__(self, raiz: str | Path):
        self.raiz = Path(raiz)

    def _caminho(self, chave: str) -> Path:
        return self.raiz.joinpath(*PurePosixPath(_valida_chave(chave)).parts)

    def gravar(self, chave: str, dados: bytes) -> str:
        caminho = self._caminho(chave)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        # Grava num temporário e renomeia: se o processo cair no meio, não
        # fica um JSON pela metade que quebraria a leitura da prata depois.
        tmp = caminho.with_name(caminho.name + ".tmp")
        tmp.write_bytes(dados)
        tmp.replace(caminho)
        return chave

    def ler(self, chave: str) -> bytes:
        return self._caminho(chave).read_bytes()

    def existe(self, chave: str) -> bool:
        return self._caminho(chave).is_file()

    def listar(self, prefixo: str = "") -> list[str]:
        if not self.raiz.exists():
            return []
        chaves = []
        for arquivo in self.raiz.rglob("*"):
            if not arquivo.is_file() or arquivo.name.endswith(".tmp"):
                continue
            chave = arquivo.relative_to(self.raiz).as_posix()
            if chave.startswith(prefixo):
                chaves.append(chave)
        return sorted(chaves)


class ArmazenamentoS3:
    def __init__(self, bucket: str, cliente):
        self.bucket = bucket
        self.cliente = cliente

    def gravar(self, chave: str, dados: bytes) -> str:
        self.cliente.put_object(Bucket=self.bucket, Key=_valida_chave(chave), Body=dados)
        return chave

    def ler(self, chave: str) -> bytes:
        resp = self.cliente.get_object(Bucket=self.bucket, Key=_valida_chave(chave))
        return resp["Body"].read()

    def existe(self, chave: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.cliente.head_object(Bucket=self.bucket, Key=_valida_chave(chave))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def listar(self, prefixo: str = "") -> list[str]:
        paginador = self.cliente.get_paginator("list_objects_v2")
        chaves = []
        for pagina in paginador.paginate(Bucket=self.bucket, Prefix=prefixo):
            chaves.extend(obj["Key"] for obj in pagina.get("Contents", []))
        return sorted(chaves)

    def garantir_bucket(self, regiao: str = "us-east-1") -> None:
        """Cria o bucket se não existir -- útil no MinIO recém-subido. Na AWS
        fora de us-east-1, o create_bucket exige LocationConstraint."""
        from botocore.exceptions import ClientError

        try:
            self.cliente.head_bucket(Bucket=self.bucket)
            return
        except ClientError:
            pass
        if regiao == "us-east-1":
            self.cliente.create_bucket(Bucket=self.bucket)
        else:
            self.cliente.create_bucket(
                Bucket=self.bucket, CreateBucketConfiguration={"LocationConstraint": regiao}
            )


def criar_cliente_s3(cfg: Settings):
    import boto3

    kwargs = {"region_name": cfg.lake_s3_regiao}
    if cfg.lake_s3_endpoint_url:
        kwargs["endpoint_url"] = cfg.lake_s3_endpoint_url
    if cfg.lake_s3_access_key and cfg.lake_s3_secret_key:
        kwargs["aws_access_key_id"] = cfg.lake_s3_access_key
        kwargs["aws_secret_access_key"] = cfg.lake_s3_secret_key
    return boto3.client("s3", **kwargs)


def armazenamento_padrao(cfg: Settings | None = None) -> Armazenamento:
    """Escolhe o backend pela configuração (`LAKE_BACKEND` no .env)."""
    cfg = cfg or settings_padrao
    backend = cfg.lake_backend.strip().lower()
    if backend == "local":
        return ArmazenamentoLocal(cfg.lake_dir)
    if backend == "s3":
        arm = ArmazenamentoS3(cfg.lake_s3_bucket, criar_cliente_s3(cfg))
        arm.garantir_bucket(cfg.lake_s3_regiao)
        return arm
    raise ValueError(f"LAKE_BACKEND desconhecido: {cfg.lake_backend!r} (use 'local' ou 's3')")
