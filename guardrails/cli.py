"""Command-line entry point: check a trade JSON, or run the self-tests.

    echo '{"side":"YES","market_price":0.5,"model_prob":0.7,"balance":250}' \
        | python -m guardrails.cli check
    python -m guardrails.cli check --file trade.json
    python -m guardrails.cli selftest
"""
from __future__ import annotations

import json
import sys

from .engine import check_trade


def _cmd_check(argv) -> int:
    if len(argv) >= 2 and argv[0] == "--file":
        with open(argv[1]) as f:
            payload = json.load(f)
    else:
        data = sys.stdin.read().strip()
        if not data:
            print("ERROR: expected trade JSON on stdin or --file <path>", file=sys.stderr)
            return 2
        payload = json.loads(data)
    verdict = check_trade(payload)
    print(json.dumps(verdict.to_dict(), indent=2))
    return 0 if verdict.passed else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        return _cmd_check(rest)
    if cmd == "selftest":
        from .selftest import run
        return run()
    print(f"unknown command: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
