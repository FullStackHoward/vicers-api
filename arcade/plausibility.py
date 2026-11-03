"""
Per-game limits that reject impossible runs.

Stated plainly, as the spec does: the game runs on the player's device, so a
determined cheater can fake inputs. These checks raise the effort required and
nothing more. Replaying a flagged run server side from its seed and input log
would be the next step up and is out of scope for v1.

Limits live on `ArcadeGame.plausibility` so they can be tuned in admin without a
deploy. Every key is optional, and a key that is absent is not checked, which
means an empty dict accepts anything. That is the right default for a cabinet
whose real numbers are not known yet, and the reason Vetrix's limits are seeded
from a real playtest rather than guessed here.
"""

LIMIT_KEYS = (
    'max_score',
    'max_score_per_piece',
    'min_ms_per_piece',
    'max_chain',
    'max_level',
    'max_tiles_per_piece',
)


def _number(stats, key):
    value = stats.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return value


def check(game, score, run_stats):
    """
    Every reason this run looks impossible, or an empty list if it passes.

    Returning all of them rather than the first makes a rejection legible in the
    log instead of a guessing game about which limit bit.
    """
    limits = game.plausibility or {}
    stats = run_stats or {}
    problems = []

    pieces = _number(stats, 'pieces_placed')
    duration_ms = _number(stats, 'duration_ms')

    ceiling = limits.get('max_score')
    if ceiling is not None and score > ceiling:
        problems.append(f'score {score} is above the ceiling of {ceiling}')

    per_piece = limits.get('max_score_per_piece')
    if per_piece is not None:
        if pieces is None:
            problems.append('pieces_placed is missing, so the score cannot be checked')
        elif score > pieces * per_piece:
            problems.append(
                f'score {score} is more than {per_piece} per piece over {pieces} pieces'
            )

    min_ms = limits.get('min_ms_per_piece')
    if min_ms is not None:
        if pieces is None or duration_ms is None:
            problems.append('pieces_placed or duration_ms is missing, so the pace cannot be checked')
        elif duration_ms < pieces * min_ms:
            problems.append(
                f'{pieces} pieces in {duration_ms} ms is faster than {min_ms} ms per piece'
            )

    max_chain = limits.get('max_chain')
    if max_chain is not None:
        chain = _number(stats, 'max_chain')
        if chain is not None and chain > max_chain:
            problems.append(f'max chain {chain} is above the ceiling of {max_chain}')

    max_level = limits.get('max_level')
    if max_level is not None:
        level = _number(stats, 'final_level')
        if level is not None and level > max_level:
            problems.append(f'final level {level} is above the ceiling of {max_level}')

    max_tiles = limits.get('max_tiles_per_piece')
    if max_tiles is not None:
        tiles = _number(stats, 'tiles_cleared')
        if tiles is not None and pieces is not None and tiles > pieces * max_tiles:
            problems.append(
                f'{tiles} tiles cleared is more than {max_tiles} per piece over {pieces} pieces'
            )

    return problems
