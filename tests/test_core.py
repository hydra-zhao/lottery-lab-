import json
import random
import tempfile
import unittest
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from lottery_lab.core import (
    GAMES, Draw, backtest, chi2_uniform, fetch_source, frequencies,
    parse_draw, parse_history, pick, randomization_test, read_history, sync,
)


def sample_history(game, count=180, seed=99):
    rng = random.Random(seed)
    start = date(2019, 1, 1)
    return [Draw(f'2019{i+1:03}', (start + timedelta(days=i * 2)).isoformat(),
                 tuple(sorted(rng.sample(range(1, game.front_total + 1), game.front_pick))),
                 tuple(sorted(rng.sample(range(1, game.back_total + 1), game.back_pick))))
            for i in range(count)]


class TestValidation(unittest.TestCase):
    def test_primary_mirror_samples(self):
        ssq = {'issueNumber': '26130', 'redBalls': [1, 2, 6, 16, 20, 28], 'blueBall': 2, 'drawDate': '2026-11-12'}
        dlt = {'issueNumber': '26129', 'frontBalls': [2, 12, 14, 17, 33], 'backBalls': [7, 8], 'drawDate': '2026-11-11'}
        self.assertEqual(parse_draw(ssq, GAMES['ssq']).front, (1, 2, 6, 16, 20, 28))
        self.assertEqual(parse_draw(dlt, GAMES['dlt']).back, (7, 8))

    def test_backup_mirror_samples(self):
        ssq = {'issue': '26060', 'date': '2026-05-28', 'red': ['07', '09', '10', '16', '22', '27'], 'blue': ['11']}
        dlt = {'issue': '26062', 'date': '2026-05-28', 'front': ['05', '11', '18', '20', '33'], 'back': ['04', '09']}
        self.assertEqual(parse_draw(ssq, GAMES['ssq']).back, (11,))
        self.assertEqual(parse_draw(dlt, GAMES['dlt']).front, (5, 11, 18, 20, 33))

    def test_reject_duplicate_invalid_numbers(self):
        row = {'issue': '26060', 'date': '2026-05-28', 'red': ['07', '07', '10', '16', '22', '27'], 'blue': ['11']}
        with self.assertRaisesRegex(ValueError, 'invalid'):
            parse_draw(row, GAMES['ssq'])

    def test_reject_future(self):
        row = {'issue': '26160', 'date': '2099-01-01', 'front': [1, 2, 3, 4, 5], 'back': [1, 2]}
        with self.assertRaisesRegex(ValueError, 'future'):
            parse_draw(row, GAMES['dlt'])

    def test_reject_conflicting_issue(self):
        row = {'issue': '26060', 'date': '2026-05-28', 'red': ['01','02','03','04','05','06'], 'blue': '01'}
        other = {**row, 'blue': '02'}
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            parse_history([row, other], GAMES['ssq'])

    def test_file_roundtrip(self):
        g = GAMES['ssq']
        hist = sample_history(g, 3)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ssq.json'
            path.write_text(json.dumps({'game': g.code, 'draws': [x.payload() for x in hist]}))
            self.assertEqual(read_history(g, path), hist)

    @patch('lottery_lab.core.fetch_source')
    def test_cross_check_fails_closed(self, fetch):
        game = GAMES['ssq']
        first = sample_history(game, 5)
        second = first.copy()
        second[0] = Draw(second[0].issue, second[0].date, second[0].front, (2 if second[0].back != (2,) else 3,))
        fetch.side_effect = [(first, {'source': 'primary'}), (second, {'source': 'backup'})]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ssq.json'
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                sync(game, path)
            self.assertFalse(path.exists())

    @patch('lottery_lab.core.fetch_source')
    def test_cross_checked_merge_retains_older_and_latest(self, fetch):
        game = GAMES['dlt']
        history = sample_history(game, 8)
        # Two mirrors overlap, but have different coverage at the ends.
        fetch.side_effect = [(history[:6], {'source': 'primary'}), (history[2:], {'source': 'backup'})]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dlt.json'
            meta = sync(game, path)
            self.assertEqual(meta['source'], 'cross_checked_merge')
            self.assertEqual(meta['cross_check']['matched'], 4)
            self.assertEqual(read_history(game, path), history)
            self.assertFalse(meta['unchanged'])

    @patch('lottery_lab.core.fetch_source')
    def test_sync_backup(self, fetch):
        game = GAMES['dlt']
        hist = sample_history(game, 5)
        def impl(g, name):
            if name == 'primary':
                raise OSError('network down')
            return (hist, {'source': 'backup', 'latest': hist[-1].date})
        fetch.side_effect = impl
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dlt.json'
            meta = sync(game, path)
            self.assertEqual(meta['cross_check']['status'], 'unavailable')
            self.assertEqual(read_history(game, path), hist)


class TestResearch(unittest.TestCase):
    def test_jackpot_probabilities(self):
        self.assertAlmostEqual(GAMES['ssq'].jackpot_probability, 1 / 17_721_088)
        self.assertAlmostEqual(GAMES['dlt'].jackpot_probability, 1 / 21_425_712)

    def test_pick_uniqueness_and_bounds(self):
        for game in GAMES.values():
            hist = sample_history(game)
            for strategy in ('random', 'hot', 'cold'):
                for i in range(30):
                    a, b = pick(game, hist, strategy, random.Random(i))
                    self.assertEqual(len(set(a)), game.front_pick)
                    self.assertEqual(len(set(b)), game.back_pick)
                    self.assertTrue(all(1 <= x <= game.front_total for x in a))
                    self.assertTrue(all(1 <= x <= game.back_total for x in b))

    def test_backtest_is_deterministic(self):
        for game in GAMES.values():
            hist = sample_history(game)
            first = backtest(hist, game, min_train=50, max_test=75, seed=11)
            second = backtest(hist, game, min_train=50, max_test=75, seed=11)
            self.assertEqual(first, second)
            self.assertEqual(first['tested_draws'], 75)
            self.assertEqual(first['strategies']['random']['tickets'], 75)
            self.assertEqual(first['strategies']['random']['cost_cny'], 150)

    def test_no_future_leakage(self):
        game = GAMES['dlt']
        original = sample_history(game, 105)
        modified = original[:]
        last = modified[-1]
        modified[-1] = Draw(last.issue, last.date, (1, 2, 3, 4, 5), (1, 2))
        # Last outcome changes scores, but never changes tickets picked before it.
        r1 = random.Random(42)
        r2 = random.Random(42)
        self.assertEqual(pick(game, original[:-1], 'hot', r1), pick(game, modified[:-1], 'hot', r2))

    def test_randomness_diagnostics(self):
        game = GAMES['ssq']
        hist = sample_history(game, 100)
        report = randomization_test(hist, game, simulations=25, seed=9)
        self.assertTrue(0 < report['front']['monte_carlo_p'] <= 1)
        self.assertTrue(0 < report['back']['monte_carlo_p'] <= 1)
        self.assertEqual(len(frequencies(hist, game)['front']['frequency']), 33)
        self.assertEqual(chi2_uniform([10, 10, 10]), 0)


if __name__ == '__main__':
    unittest.main()
