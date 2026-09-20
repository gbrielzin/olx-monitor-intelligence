"""Exporta o banco pra CSVs prontos pra Power BI / Excel.

    PYTHONPATH=. DB_PATH=data/olx_monitor.db python scripts/exportar_bi.py [pasta]

Sem argumento grava em data/exports/ (pasta já ignorada pelo git)."""

import sys

from common.export import exportar_para_pasta

if __name__ == "__main__":
    pasta = sys.argv[1] if len(sys.argv) > 1 else "data/exports"
    for nome, linhas in exportar_para_pasta(pasta).items():
        print(f"{nome}: {linhas} linhas")
    print(f"CSVs em {pasta}")
