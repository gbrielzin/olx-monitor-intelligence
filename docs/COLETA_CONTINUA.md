# Coleta contínua — como sair de ~65% dos dias com coleta

Hoje o scraper roda em Docker Desktop num PC pessoal. Quando o PC dorme ou
hiberna, a coleta para: em 20/09/2026 havia coleta em 19 de 29 dias corridos,
e a aba *Resumo* do dashboard lista os maiores buracos (medidos na tabela
`coletas`, não estimados).

Por que importa: mais dias contínuos = mediana mais estável, tempo no ar
menos quantizado e um backtest defensável (ver `medianas_diarias`).

## Opções, da mais simples à mais robusta

1. **Impedir a suspensão com o PC na tomada** (grátis, 1 minuto). Num
   PowerShell, `powercfg /change standby-timeout-ac 0` e
   `powercfg /change hibernate-timeout-ac 0`. Não resolve PC desligado nem
   queda de energia/internet. *Não aplicado automaticamente: muda
   configuração do sistema.*
2. **Docker com `restart: unless-stopped`** (já configurado): se o Docker
   Desktop reiniciar com o Windows, o scraper volta sozinho. Confira que o
   Docker Desktop está marcado para iniciar no login.
3. **VPS pequena** (~US$ 4–6/mês) rodando o mesmo `docker-compose up -d`. O
   banco é um arquivo SQLite (`data/`): dá pra migrar copiando o arquivo.
   Cuidado: IP de datacenter tem mais chance de bloqueio que IP residencial —
   o checkpoint de sanidade avisa se acontecer.
4. **Mini-PC/Raspberry Pi em casa**: IP residencial + sempre ligado, custo
   único.

## Depois de migrar

- Não sobrescreva `data/olx_monitor.db` no destino: copie o arquivo antes de
  subir o container novo.
- Rode `python scripts/exportar_bi.py` para conferir contagens antes/depois.
