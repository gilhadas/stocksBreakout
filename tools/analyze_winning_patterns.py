"""Mine canonical 5-yr backtest trade CSVs to find winning-feature combinations.

Reads `scanner_output/backtests/trades_*_BBG15_V9-C.csv` (the live config series),
optionally enriches with Mode/RR/RSI from `backtests/validated_trades_*.csv` for
overlapping dates, and prints expectancy tables to inform SELECTIVE_MODE defaults.

Usage:  venv/bin/python3 tools/analyze_winning_patterns.py
Output: scanner_output/analysis/winning_patterns.csv (full grid)
"""
from pathlib import Path
import glob
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = sorted(glob.glob(str(ROOT / 'scanner_output/backtests/trades_2*_BBG15_V9-C.csv')))
VALIDATED = ROOT / 'backtests/validated_trades_20260308_014852.csv'
OUT = ROOT / 'scanner_output/analysis/winning_patterns.csv'


def load() -> pd.DataFrame:
    if not SRC:
        raise SystemExit(f'no canonical trade CSVs found under {ROOT}/scanner_output/backtests/')
    parts = []
    for f in SRC:
        d = pd.read_csv(f)
        d['source_year'] = Path(f).stem.split('_')[1]
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df['entry_date'] = pd.to_datetime(df['entry_date'])
    df['exit_date'] = pd.to_datetime(df['exit_date'])
    df['hold_days'] = (df['exit_date'] - df['entry_date']).dt.days.clip(lower=0)
    df['hold_bucket'] = pd.cut(
        df['hold_days'], [-1, 1, 5, 15, 30, 9999],
        labels=['≤1d', '2-5d', '6-15d', '16-30d', '>30d'],
    )
    return df


def enrich_with_validated(df: pd.DataFrame) -> pd.DataFrame:
    if not VALIDATED.exists():
        return df
    v = pd.read_csv(VALIDATED)
    v = v[v['Action'].astype(str).str.upper().isin({'BUY', 'OPEN', 'ENTER'}) | v['Action'].isna()]
    v = v.rename(columns={'Symbol': 'symbol', 'Signal_Date': 'entry_date'})
    v['entry_date'] = pd.to_datetime(v['entry_date'], errors='coerce')
    v = v[['symbol', 'entry_date', 'Mode', 'RR', 'Score_Pct', 'RSI']].dropna(subset=['entry_date'])
    v = v.drop_duplicates(['symbol', 'entry_date'], keep='first')
    return df.merge(v, on=['symbol', 'entry_date'], how='left')


def agg(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    g = (
        df.groupby(group_cols, dropna=False, observed=True)
          .agg(
              n=('pnl_pct', 'count'),
              sum_pnl_pct=('pnl_pct', 'sum'),
              avg_pnl_pct=('pnl_pct', 'mean'),
              med_pnl_pct=('pnl_pct', 'median'),
              std_pnl_pct=('pnl_pct', 'std'),
              winrate=('win', 'mean'),
          )
          .reset_index()
    )
    g['expectancy'] = g['n'] * g['avg_pnl_pct']
    g['sharpe_proxy'] = g['avg_pnl_pct'] / g['std_pnl_pct'].replace(0, pd.NA)
    g['winrate'] = (g['winrate'] * 100).round(1)
    for c in ['sum_pnl_pct', 'avg_pnl_pct', 'med_pnl_pct', 'std_pnl_pct', 'expectancy', 'sharpe_proxy']:
        g[c] = g[c].astype(float).round(2)
    return g.sort_values('expectancy', ascending=False)


def fmt_table(df: pd.DataFrame, title: str, head: int | None = None) -> str:
    print()
    print('=' * 100)
    print(title)
    print('=' * 100)
    out = df.head(head) if head else df
    return out.to_string(index=False)


def main() -> None:
    df = load()
    df = enrich_with_validated(df)
    print(f'Loaded {len(df):,} trades across {df["source_year"].nunique()} years '
          f'({df["entry_date"].min().date()} → {df["entry_date"].max().date()})')
    print(f'Quality: {df["quality"].value_counts().to_dict()}')
    print(f'Type:    {df["signal_type"].value_counts().to_dict()}')
    print(f'Regime:  {df["regime"].value_counts().to_dict()}')
    enriched = df['Mode'].notna().sum() if 'Mode' in df.columns else 0
    print(f'Mode-enriched rows (validated_trades overlap): {enriched:,} / {len(df):,}')

    print(fmt_table(agg(df, ['hold_bucket']), 'BY HOLD BUCKET'))
    print(fmt_table(agg(df, ['regime']), 'BY REGIME'))
    print(fmt_table(agg(df, ['signal_type']), 'BY SIGNAL_TYPE'))
    print(fmt_table(agg(df, ['hold_bucket', 'regime']), 'BY HOLD × REGIME'))

    if 'RR' in df.columns and df['RR'].notna().any():
        df['RR_bucket'] = pd.cut(
            pd.to_numeric(df['RR'], errors='coerce'),
            [0, 1.5, 2.0, 2.5, 3.0, 5.0, 99],
            labels=['<1.5', '1.5-2', '2-2.5', '2.5-3', '3-5', '>5'],
        )
        print(fmt_table(agg(df.dropna(subset=['RR']), ['RR_bucket']), 'BY R:R BUCKET (validated_trades subset only)'))
        print(fmt_table(agg(df.dropna(subset=['RR']), ['hold_bucket', 'RR_bucket']), 'BY HOLD × R:R'))

    full = agg(df, ['quality', 'signal_type', 'regime', 'hold_bucket'])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(OUT, index=False)
    print(f'\nFull grid written to {OUT.relative_to(ROOT)} ({len(full)} rows)')


if __name__ == '__main__':
    main()
