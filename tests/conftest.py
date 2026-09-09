import sys
from pathlib import Path

# scraper/*.py usa imports "achatados" entre si (ex: `from notifier import
# enviar_telegram` em resumo_diario.py/main.py) porque em produção (Docker)
# os arquivos de scraper/ são copiados soltos pra dentro de /app com
# PYTHONPATH=/app -- não existe pacote `scraper.` ali. Nos testes, rodando a
# partir da raiz do repo, scraper/ é um pacote de verdade; sem isso no
# sys.path, um módulo de scraper/ que importa outro módulo irmão (em vez de
# só common/) quebra na coleta dos testes.
sys.path.insert(0, str(Path(__file__).parent.parent / "scraper"))
