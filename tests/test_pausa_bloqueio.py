"""Depois de 403/429 a rodada pausa e não insiste nas próximas."""
from scraper import main as m
from fetcher import FetchError


def _prepara(monkeypatch, erro):
    chamadas, msgs = [], []
    def fake_coletar(url, maxp):
        chamadas.append(url)
        raise FetchError(erro)
    monkeypatch.setattr(m, "coletar_paginas", fake_coletar)
    monkeypatch.setattr(m, "enviar_telegram", lambda msg, *a, **k: msgs.append(msg))
    m._pausado_ate.clear()
    return chamadas, msgs


def _roda():
    m.rodar_coleta(nome="iPhone (ES)", search_url="u", ad_class=None, categoria="iphone", uf="ES")


def test_403_pausa_e_nao_insiste(monkeypatch):
    chamadas, msgs = _prepara(monkeypatch, "Falha ao buscar u: 403 Client Error: Forbidden")
    _roda(); _roda(); _roda()
    assert len(chamadas) == 1
    assert len(msgs) == 1 and "Pausando" in msgs[0]


def test_pausa_acaba_e_tenta_de_novo(monkeypatch):
    chamadas, _ = _prepara(monkeypatch, "403 Client Error")
    relogio = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: relogio[0])
    _roda()
    relogio[0] += m.settings.pausa_apos_bloqueio_minutos * 60 + 1
    _roda()
    assert len(chamadas) == 2


def test_erro_de_rede_comum_nao_pausa(monkeypatch):
    chamadas, msgs = _prepara(monkeypatch, "Falha ao buscar u: Read timed out")
    _roda(); _roda()
    assert len(chamadas) == 2
    assert all("Pausando" not in x for x in msgs)
