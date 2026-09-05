"""
core/signals/cli.py
CLI entry point for signal validation.

Usage:
    python3 -m core.signals.cli validate --data data/validation/eurusd_m1.csv --symbol EURUSD
    python3 -m core.signals.cli report --out docs/HA_EMA100_FOREX_VALIDATION_REPORT.md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.signals.validation import load_csv, generate_ha_ema100_signals, simulate_trades, compute_stats, render_validation_report, ValidationResult


def cmd_validate(args) -> int:
    rows = load_csv(args.data)
    signals = generate_ha_ema100_signals(rows, args.symbol, args.timeframe)
    trades = simulate_trades(signals, rows)
    result = compute_stats(args.symbol, trades)
    print(f"{args.symbol}: {result.total_signals} signals, WR={result.win_rate:.1%}, PF={result.profit_factor:.2f}")
    return 0


def cmd_report(args) -> int:
    data_dir = Path(args.data_dir)
    results = []
    for csv_file in sorted(data_dir.glob("*_m5.csv")):
        symbol = csv_file.stem.split("_")[0].upper()
        rows = load_csv(str(csv_file))
        signals = generate_ha_ema100_signals(rows, symbol, "M5")
        trades = simulate_trades(signals, rows)
        results.append(compute_stats(symbol, trades))

    report = render_validation_report(results)
    print(report)
    if args.out:
        with open(args.out, "w") as f:
            f.write(report)
        print(f"\nReport saved to {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--data", required=True)
    p_validate.add_argument("--symbol", required=True)
    p_validate.add_argument("--timeframe", default="M1")

    p_report = sub.add_parser("report")
    p_report.add_argument("--data-dir", default="data/validation")
    p_report.add_argument("--out", default="")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "report":
        return cmd_report(args)
    return 1


if __name__ == "__main__":
    main()
