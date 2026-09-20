"""Command-line entry point for the evaluation.

    python -m evals.run            # 500 cases, offline, writes results + chart, opens chart
    python -m evals.run --full     # 2000 cases
    python -m evals.run --no-open  # do not try to open the chart (CI)

Fully offline: zero API keys, no network. Uses the stub LLM and the frozen
deterministic customer simulator.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from evals.harness import optimal_retry_budget, run_eval
from evals.report import write_cost_curve_png, write_results_md

EVALS_DIR = Path(__file__).resolve().parent


def _try_open(path: Path) -> None:
    if os.environ.get("CI"):
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the payment-recovery evaluation.")
    parser.add_argument("--full", action="store_true", help="2000 cases instead of 500")
    parser.add_argument("--n", type=int, default=None, help="explicit case count")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--retry-budget", type=int, default=3)
    parser.add_argument("--out", type=str, default=str(EVALS_DIR))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)

    n = args.n if args.n is not None else (2000 if args.full else 500)
    result = run_eval(n=n, seed=args.seed, retry_budget=args.retry_budget)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = write_results_md(result, out_dir / "results.md")
    png_path = write_cost_curve_png(result, out_dir / "cost_curve.png")

    s = result.summary
    print(f"cases={s['n_total']} treated={s['n_treated']} control={s['n_control']}")
    print(
        f"raw recovery={s['raw_recovery'].rate:.1%} "
        f"control={s['control_recovery'].rate:.1%} "
        f"incremental={s['incremental_recovery']:.1%}"
    )
    print(f"LLM share={s['llm_share'].rate:.1%}  wrong-tool={s['wrong_tool_rate'].rate:.1%}")
    print(f"modelled cost/1000=${s['modelled_cost_per_1000_usd']:.4f}")
    print(f"optimal retry budget={optimal_retry_budget(result.retry_sweep)}")
    print(f"wrote {md_path} and {png_path}")

    if not args.no_open:
        _try_open(png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
