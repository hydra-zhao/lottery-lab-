"""Run: python -m lottery_lab {sync,analyze,backtest,pick}."""
import argparse
import json
from pathlib import Path
import random
import sys

from .core import GAMES, backtest, frequencies, pick, randomization_test, read_history, sync


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='双色球 / 大乐透开奖统计与样本外回测')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('sync', 'analyze', 'backtest', 'pick'):
        p = sub.add_parser(name)
        p.add_argument('--game', choices=['ssq', 'dlt', 'all'] if name == 'sync' else ['ssq', 'dlt'], required=True)
        p.add_argument('--data-dir', type=Path, default=Path('data'))
        if name == 'sync':
            p.add_argument('--no-cross-check', action='store_true')
        if name == 'analyze':
            p.add_argument('--simulations', type=int, default=500)
            p.add_argument('--seed', type=int, default=42)
        if name == 'backtest':
            p.add_argument('--min-train', type=int, default=100)
            p.add_argument('--window', type=int, default=50)
            p.add_argument('--seed', type=int, default=42)
            p.add_argument('--max-test', type=int, default=None)
        if name == 'pick':
            p.add_argument('--strategy', choices=['random', 'hot', 'cold'], default='random')
            p.add_argument('--window', type=int, default=50)
            p.add_argument('--seed', type=int, default=None)
            p.add_argument('--count', type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == 'sync':
            result = {code: sync(GAMES[code], args.data_dir / f'{code}.json', cross_check=not args.no_cross_check) for code in (GAMES if args.game == 'all' else [args.game])}
        else:
            game = GAMES[args.game]
            draws = read_history(game, args.data_dir / f'{game.code}.json')
            if args.command == 'analyze':
                result = {'game': game.code, 'count': len(draws), 'oldest': draws[0].date, 'latest': draws[-1].date,
                          'statistics': frequencies(draws, game), 'randomness_test': randomization_test(draws, game, simulations=args.simulations, seed=args.seed),
                          'jackpot_probability_per_ticket': game.jackpot_probability}
            elif args.command == 'backtest':
                result = backtest(draws, game, min_train=args.min_train, window=args.window, seed=args.seed, max_test=args.max_test)
            else:
                if args.count < 1 or args.count > 100:
                    raise ValueError('count must be 1..100')
                rng = random.Random(args.seed)
                tickets = [pick(game, draws, args.strategy, rng, window=args.window) for _ in range(args.count)]
                result = {'game': game.code, 'strategy': args.strategy,
                          'tickets': [{'front': list(a), 'back': list(b)} for a, b in tickets],
                          'note': '号码仅供研究演示；每个合法组合的头奖概率相同。'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
