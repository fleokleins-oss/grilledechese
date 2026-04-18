# CLAUDE_CODE_PROMPT.md — handoff para continuar o Reef

Este arquivo é o briefing para um Claude Code (ou outra instância) que pegue
o projeto daqui e continue. Ler antes de tocar em qualquer código.

## Estado atual (baseline validado)

- **23 módulos Python**, sintaxe clean (AST parse verde).
- **11/11 testes passam** em <2s: `python -m unittest encruzilhada3d.tests.test_reef_mvp -v`.
- **CLI funciona**: `python -m encruzilhada3d --synthetic --render-html --pop 30 --ticks 800 --gens 3` produz `reef.html` (~800KB) + state files.
- **Smoke runtime**: 3 gens × 30 creatures × 800 ticks em ~0.6s total (synthetic).
- **Fitness selection pressure real**: champion_fitness sobe entre gerações na bateria de testes.

## O que NÃO está feito (prioridade decrescente)

### 1. Rodar com dados reais L2

O código tenta ler `$ENC3D_DATA_ROOT/recorder_active/SYMBOL/depth_*.parquet`
e cai para synthetic se não achar. Teste com:

```bash
export ENC3D_DATA_ROOT=~/apex_data
python -m encruzilhada3d --symbol ADAUSDT --pop 80 --ticks 5000 --gens 5 --days 2.0
```

Se os parquets têm colunas diferentes de `bid_px, ask_px, bid_qty, ask_qty`,
abrir `market/book.py::BookStream.from_dataframe` e mapear corretamente.

### 2. Calibrar `regime_factor`

Atualmente é flat (0.85 para specialistas, 1.0 para "any"). Deveria ser
weighted-average de P&L por regime. Cada criatura já tem `trades` com tick de
entrada/saída — basta cruzar com `classify_regime(features_at_tick)` e calcular
retorno-ponderado por tempo-em-regime.

Arquivo: `creatures/fitness.py::regime_factor`.

### 3. Expor P&L por regime no `Creature`

Hoje o `Creature` conta só `ticks_in_vol_spike` + `return_in_vol_spike`. Estender
para `ticks_per_regime: dict[str, int]` e `return_per_regime: dict[str, float]`,
atualizado no `record_step` com o regime classificado. Isso destrava o item 2.

### 4. Short-side + pyramiding (v2)

Long-only é assumido em 3 lugares:
- `actions.decide()` filtra `s <= 0` → hold.
- `creatures/creature.py::apply_buy` não lida com posição negativa.
- `slippage.slippage_decimal` tem parâmetro `side` mas o impacto é simétrico.

Para adicionar short, generalizar os 3 e revisar convexity_bonus (shorts ganham
em spikes negativos).

### 5. Latency model

`FillResult.delay_ticks` existe mas está sempre 0. Um modelo simples:
delay = round(Z × 5). Then `simulator.step_execution` fila a ordem e só aplica
no tick `t + delay`. Requer uma fila pendente por creature.

### 6. Converter o `reef.html` em dashboard live

Hoje é snapshot estático. Para live:
- `chart3d.py` ganha um modo WebSocket que empurra pontos do último tick.
- `world3d.py` publica em `/tmp/reef_live.jsonl` cada tick (append-only).
- Widget HTML consome com fetch polling.

Baixa prioridade — só faz sentido quando rodar 24/7.

## Regras de estilo desse projeto

1. **Fees só em `execution/fees.py`.** Test `test_no_other_module_references_fee_rates`
   vai falhar se você subtrair fee em outro lugar.
2. **Retornos sempre decimal.** Nunca bps em variáveis de fitness/pnl. Bps só em
   constantes nominadas `*_BPS`.
3. **Paths via env.** `paths.py` é a única fonte. Não hardcode `/mnt/data` ou caminhos absolutos.
4. **Validar sintaxe antes de commitar**: `python -c "import ast; [ast.parse(open(p).read()) for p in __import__('pathlib').Path('encruzilhada3d').rglob('*.py')]"`.
5. **Sem replay bias.** `decide()` só recebe o que pode ver em `t`. Se precisar de
   features computadas de uma janela, essa janela já está em `snapshot['recent_returns']`.

## Arquitetura de decisão (mental model)

```
BookStream.at(t) → dict snapshot
    ↓
compute_features(snapshot) → dict com spread_decimal, ret_sigma, OBI, ...
    ↓
classify_regime(features) → "trending" | "mean_rev" | "volatile"
    ↓
para cada creature viva:
    execution_pressure_z(features, size=creature.capital × kelly_cap) → Z
    decide(creature, t, features, regime, z) → (action, size_fraction)
    step_execution(creature, t, action, size_fraction, features) → mutate creature
    creature.record_step(t, mid, z_pressure, is_vol_spike, tick_return)
    if creature.check_ruin(mid): kill + tail_bank.record_death
```

## Perguntas que provavelmente vão aparecer

**"Por que 0 mortes no synthetic stream?"**
Porque o synthetic é calmo (σ=0.001 com spikes 8× a cada 250 ticks). Criaturas com
`ruin_threshold_pct ~ 0.3-0.7` precisam perder 30-70% do capital pra morrer. Em 800
ticks calmos isso não acontece. Rode com dados reais L2 ou aumente `vol_spike_sigma_mult`
no synthetic para estressar.

**"O champion_fitness foi 0.895 em gen 1 e 2, depois 0.965 em gen 3. Por quê?"**
Seleção com elite=20% + breed=70% + fresh=10%. Gen 2 nasce de crossover do elite
da gen 1, então muitas vezes re-encontra o mesmo ótimo local. A fresh_frac injeta
diversidade — é por isso que gen 3 achou algo marginalmente melhor.

**"Como eu rodo em produção sem matar o live bot?"**
systemd user service em `systemd/encruzilhada3d.service` tem `CPUQuota=80%`, `MemoryMax=2G`, `Nice=15`.
O kernel vai matar o Reef antes de encostar no executor.

## Sanity commands

```bash
# Ver top 10 causas de morte no tail bank
jq 'select(.kind=="death_event") | .reason' $ENC3D_STATE_ROOT/tail_bank.jsonl | sort | uniq -c | sort -rn

# Ver a distribuição de fitness da última geração
jq '.final_equity' $ENC3D_STATE_ROOT/creatures.jsonl | sort -n | awk '{a[NR]=$1} END {print "min:",a[1],"median:",a[int(NR/2)],"max:",a[NR]}'

# Rodar testes
python -m unittest encruzilhada3d.tests.test_reef_mvp -v

# Validar sintaxe do pacote todo
python -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('encruzilhada3d').rglob('*.py')]"
```
