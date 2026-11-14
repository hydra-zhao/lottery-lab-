"""Validated lottery datasets, statistics, and strictly walk-forward backtesting.

All historical information used to pick a ticket comes strictly before the draw.
The engine does NOT claim historical patterns predict future draws.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import comb, sqrt
from pathlib import Path
from typing import Any
import hashlib
import json
import random
import statistics
import urllib.error
import urllib.request


@dataclass(frozen=True)
class Game:
    code: str
    title: str
    front_total: int
    front_pick: int
    back_total: int
    back_pick: int

    @property
    def jackpot_probability(self) -> float:
        return 1 / (comb(self.front_total, self.front_pick) * comb(self.back_total, self.back_pick))


GAMES = {
    'ssq': Game('ssq', '双色球', 33, 6, 16, 1),
    'dlt': Game('dlt', '大乐透', 35, 5, 12, 2),
}

# Both are public community mirrors. Always record which source and when it was read.
SOURCES = {
    'primary': 'https://raw.githubusercontent.com/gudaoxuri/lottery_history/main/data/{game}.json',
    'backup': 'https://raw.githubusercontent.com/Justdoitfor/my-lottery-data/main/data/{game}.json',
}


@dataclass(frozen=True)
class Draw:
    issue: str
    date: str
    front: tuple[int, ...]
    back: tuple[int, ...]

    def payload(self) -> dict[str, Any]:
        return dict(issue=self.issue, date=self.date, front=list(self.front), back=list(self.back))


def parse_draw(row: dict[str, Any], game: Game) -> Draw:
    if not isinstance(row, dict):
        raise ValueError('draw must be a JSON object')
    issue = str(row.get('issueNumber', row.get('issue', ''))).strip()
    draw_date = str(row.get('drawDate', row.get('date', ''))).strip()
    if not issue.isdigit() or len(issue) not in (5, 7):
        raise ValueError(f'invalid issue number: {issue!r}')
    try:
        iso = date.fromisoformat(draw_date).isoformat()
    except ValueError as exc:
        raise ValueError(f'invalid draw date: {draw_date}') from exc
    if iso > date.today().isoformat():
        raise ValueError(f'future draw: {issue}, {iso}')
    front_raw = row.get('redBalls', row.get('red' if game.code == 'ssq' else 'frontBalls', row.get('front')))
    back_raw = row.get('blueBall', row.get('blue' if game.code == 'ssq' else 'backBalls', row.get('back')))
    if isinstance(back_raw, (str, int)):
        back_raw = [back_raw]
    if not isinstance(front_raw, (tuple, list)) or not isinstance(back_raw, (tuple, list)):
        raise ValueError(f'missing front/back values in {issue}')
    try:
        front = tuple(sorted(int(x) for x in front_raw))
        back = tuple(sorted(int(x) for x in back_raw))
    except (ValueError, TypeError) as exc:
        raise ValueError(f'non-numeric number in {issue}') from exc
    if len(front) != game.front_pick or len(set(front)) != len(front) or any(x < 1 or x > game.front_total for x in front):
        raise ValueError(f'invalid {game.title} front balls in {issue}: {front}')
    if len(back) != game.back_pick or len(set(back)) != len(back) or any(x < 1 or x > game.back_total for x in back):
        raise ValueError(f'invalid {game.title} back balls in {issue}: {back}')
    return Draw(issue, iso, front, back)


def parse_history(raw: Any, game: Game) -> list[Draw]:
    if not isinstance(raw, list):
        raise ValueError('expected JSON array of draws')
    by_issue: dict[str, Draw] = {}
    by_date: dict[str, Draw] = {}
    for row in raw:
        draw = parse_draw(row, game)
        if draw.issue in by_issue and draw != by_issue[draw.issue]:
            raise ValueError(f'conflicting duplicate issue: {draw.issue}')
        if draw.date in by_date and draw != by_date[draw.date]:
            raise ValueError(f'conflicting duplicate date: {draw.date}')
        by_issue[draw.issue] = draw
        by_date[draw.date] = draw
    draws = sorted(by_issue.values(), key=lambda d: (d.date, int(d.issue)))
    if not draws:
        raise ValueError('dataset is empty')
    return draws


def fetch_source(game: Game, name: str, *, timeout: int = 18) -> tuple[list[Draw], dict]:
    url = SOURCES[name].format(game=game.code)
    request = urllib.request.Request(url, headers={'User-Agent': 'LotteryLab/0.1 (+research; no betting service)', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(8_000_001)
    if len(body) > 8_000_000:
        raise ValueError('remote dataset exceeds 8 MB safeguard')
    draws = parse_history(json.loads(body.decode('utf-8-sig')), game)
    meta = dict(source=name, url=url, retrieved_at=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(body).hexdigest(), count=len(draws), latest=draws[-1].date)
    return draws, meta


def sync(game: Game, output: Path, *, cross_check: bool = True) -> dict:
    errors: dict[str, str] = {}
    valid: dict[str, tuple[list[Draw], dict]] = {}
    for name in SOURCES:
        if not cross_check and valid:
            break
        try:
            valid[name] = fetch_source(game, name)
        except (ValueError, OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            errors[name] = str(exc)
    if not valid:
        raise RuntimeError(f'all data sources failed: {errors}')
    name = 'primary' if 'primary' in valid else 'backup'
    draws, meta = valid[name]
    comparison = {'status': 'unchecked', 'matched': 0, 'conflicts': 0}
    if cross_check and len(valid) == 2:
        other_name = 'backup' if name == 'primary' else 'primary'
        other_draws = valid[other_name][0]
        other_by_date = {d.date: d for d in other_draws}
        conflicts = [d.date for d in draws if d.date in other_by_date and (d.front, d.back) != (other_by_date[d.date].front, other_by_date[d.date].back)]
        matched = sum(1 for d in draws if d.date in other_by_date)
        comparison = {'status': 'pass' if not conflicts else 'conflict', 'matched': matched, 'conflicts': len(conflicts), 'examples': conflicts[:10]}
        if conflicts:
            raise ValueError(f'cross-source draw conflicts: {comparison}')
        # Merge unique dates to retain the longest history and the freshest draw.
        combined_by_date = {d.date: d for d in other_draws}
        combined_by_date.update({d.date: d for d in draws})
        draws = parse_history([d.payload() for d in combined_by_date.values()], game)
        meta = {
            'source': 'cross_checked_merge',
            'sources': {key: data[1] for key, data in valid.items()},
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'count': len(draws), 'latest': draws[-1].date,
        }
    elif cross_check:
        comparison = {'status': 'unavailable', 'matched': 0, 'conflicts': 0, 'errors': errors}
    result = {
        'game': game.code,
        'meta': {**meta, 'cross_check': comparison, 'source_errors': errors},
        'draws': [draw.payload() for draw in draws],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_file():
        try:
            previous = json.loads(output.read_text(encoding='utf-8'))
            if previous.get('game') == game.code and previous.get('draws') == result['draws']:
                return {**result['meta'], 'unchanged': True}
        except (OSError, ValueError, AttributeError):
            pass  # Overwrite invalid local cache with a fully validated fresh dataset.
    tmp = output.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(output)
    return {**result['meta'], 'unchanged': False}


def read_history(game: Game, path: Path) -> list[Draw]:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('game') != game.code:
        raise ValueError(f'expected {game.code} dataset at {path}')
    return parse_history(data['draws'], game)


def frequencies(draws: list[Draw], game: Game) -> dict:
    out = {}
    for zone, total, pick in [('front', game.front_total, game.front_pick), ('back', game.back_total, game.back_pick)]:
        counts = Counter(x for d in draws for x in getattr(d, zone))
        missing = {}
        for number in range(1, total + 1):
            missing[number] = next((i for i, d in enumerate(reversed(draws)) if number in getattr(d, zone)), len(draws))
        out[zone] = {
            'frequency': {f'{n:02}': counts[n] for n in range(1, total + 1)},
            'missing_draws': {f'{n:02}': missing[n] for n in range(1, total + 1)},
            'expected_count_each': round(len(draws) * pick / total, 3),
        }
    return out


def chi2_uniform(counts: list[int]) -> float:
    expected = sum(counts) / len(counts)
    return sum((c - expected) ** 2 / expected for c in counts) if expected else 0.0


def randomization_test(draws: list[Draw], game: Game, *, simulations: int = 500, seed: int = 42) -> dict:
    """Monte Carlo null draws WITHOUT replacement, to respect within-draw dependence."""
    if simulations < 20 or simulations > 50_000:
        raise ValueError('simulations must be between 20 and 50000')
    rng = random.Random(seed)
    out: dict = {}
    for zone, total, pick in [('front', game.front_total, game.front_pick), ('back', game.back_total, game.back_pick)]:
        observed_counts = Counter(x for d in draws for x in getattr(d, zone))
        observed = chi2_uniform([observed_counts[n] for n in range(1, total + 1)])
        tail = 0
        for _ in range(simulations):
            simulated = [0] * total
            for _draw in draws:
                for number in rng.sample(range(total), pick):
                    simulated[number] += 1
            tail += chi2_uniform(simulated) >= observed
        out[zone] = {
            'chi_square': round(observed, 5),
            'monte_carlo_p': round((tail + 1) / (simulations + 1), 6),
            'simulations': simulations,
        }
    return out


def weighted_without_replacement(rng: random.Random, candidates: range, counts: Counter, k: int, mode: str) -> tuple[int, ...]:
    pool = list(candidates)
    chosen = []
    while len(chosen) < k:
        # The 1-unit pseudocount prevents any number being ruled out.
        weights = [counts[n] + 1 if mode == 'hot' else 1 / (counts[n] + 1) for n in pool]
        item = rng.choices(pool, weights=weights, k=1)[0]
        pool.remove(item)
        chosen.append(item)
    return tuple(sorted(chosen))


def pick(game: Game, history: list[Draw], strategy: str, rng: random.Random, *, window: int = 50) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if strategy not in ('random', 'hot', 'cold'):
        raise ValueError('invalid strategy')
    if window < 1:
        raise ValueError('window must be positive')
    result = []
    for zone, total, k in [('front', game.front_total, game.front_pick), ('back', game.back_total, game.back_pick)]:
        if strategy == 'random':
            result.append(tuple(sorted(rng.sample(range(1, total + 1), k))))
        else:
            counts = Counter(n for d in history[-window:] for n in getattr(d, zone))
            result.append(weighted_without_replacement(rng, range(1, total + 1), counts, k, strategy))
    return result[0], result[1]


def backtest(draws: list[Draw], game: Game, *, min_train: int = 100, window: int = 50, seed: int = 42, max_test: int | None = None) -> dict:
    if min_train < 1 or window < 1 or len(draws) <= min_train:
        raise ValueError('need more historical draws than min_train')
    indices = list(range(min_train, len(draws)))
    if max_test is not None:
        if max_test < 1:
            raise ValueError('max_test must be positive')
        indices = indices[-max_test:]
    strategies = ('random', 'hot', 'cold')
    rngs = {key: random.Random(seed + 1009 * i) for i, key in enumerate(strategies)}
    results: dict[str, list[tuple[int, int]]] = {k: [] for k in strategies}
    for idx in indices:
        actual = draws[idx]
        train = draws[:idx]
        for strategy in strategies:
            front, back = pick(game, train, strategy, rngs[strategy], window=window)
            results[strategy].append((len(set(front) & set(actual.front)), len(set(back) & set(actual.back))))
    by_strategy = {}
    for name in strategies:
        values = results[name]
        by_strategy[name] = {
            'mean_front_hits': round(statistics.mean(x for x, _ in values), 5),
            'mean_back_hits': round(statistics.mean(y for _, y in values), 5),
            'front_hits_at_least_4': sum(x >= 4 for x, y in values),
            'exact_jackpot_hits': sum(x == game.front_pick and y == game.back_pick for x, y in values),
            'tickets': len(values),
            'cost_cny': len(values) * 2,
            'front_hit_histogram': {str(k): sum(x == k for x, y in values) for k in range(game.front_pick + 1)},
        }
    # Paired difference confidence intervals are exploratory; multiple-testing correction not included.
    comparisons = {}
    for name in ('hot', 'cold'):
        diffs = [(x - a) for ((x, _), (a, _)) in zip(results[name], results['random'])]
        mean = statistics.mean(diffs)
        se = statistics.stdev(diffs) / sqrt(len(diffs)) if len(diffs) > 1 else 0
        comparisons[name] = {
            'mean_front_hit_advantage_over_random': round(mean, 5),
            'approx_95pct_ci': [round(mean - 1.96 * se, 5), round(mean + 1.96 * se, 5)],
        }
    return {
        'game': game.code,
        'tested_draws': len(indices),
        'date_range': [draws[indices[0]].date, draws[indices[-1]].date],
        'min_train': min_train, 'window': window, 'seed': seed,
        'exact_jackpot_probability_per_ticket': game.jackpot_probability,
        'null_expected_front_hits_per_ticket': game.front_pick**2 / game.front_total,
        'strategies': by_strategy,
        'paired_exploratory_comparisons': comparisons,
        'warning': 'Past results do not establish predictive power; this is not a betting return simulation.',
    }
