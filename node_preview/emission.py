"""MarsChain's block reward and reference emission model.

The integer reward follows ``PowerContractUpgradeable.getBlockReward``.  Future
emission is a reference projection at three seconds per block, not a claim about
blocks that have actually been produced.  Its first slot at the anchor timestamp
uses ``anchor_block + 1``, matching the contract's future-emission calculation.
"""

from __future__ import annotations

from decimal import Decimal, localcontext


MODEL_VERSION = "marschain-block-reward-v1"
BLOCK_SECONDS = 3
BLOCKS_PER_DAY = 28_800
HALVING_BLOCKS = 12_902_400
HALVING_OFFSET = 79_192
WEI_PER_TOKEN = 10**18
BASE_REWARD_WEI = 7_750_496_031_750_000_000_000
UINT256_MAX = 2**256 - 1
# Solidity performs the addition before division and reverts on overflow.
MAX_REWARD_BLOCK = UINT256_MAX - HALVING_OFFSET
TOTAL_SUPPLY_CAP_TOKENS = 200_000_000_000


def _uint256(value: int, name: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT256_MAX:
        raise ValueError(f"{name} must be a nonnegative uint256 integer")
    return value


def _block(value: int, name: str = "block") -> int:
    _uint256(value, name)
    if value > MAX_REWARD_BLOCK:
        raise ValueError(f"{name} overflows the contract's halving offset")
    return value


def block_reward_wei(block: int) -> int:
    """Return the exact integer reward for a valid Solidity block number."""
    block = _block(block)
    halvings = (block + HALVING_OFFSET) // HALVING_BLOCKS
    # Avoid passing an arbitrarily large shift count to Python, while retaining
    # Solidity's zero result once every bit of the base reward has shifted out.
    return 0 if halvings >= BASE_REWARD_WEI.bit_length() else BASE_REWARD_WEI >> halvings


def next_halving_block(block: int) -> int:
    """Return the next cycle boundary, strictly after ``block``.

    At the far uint256 boundary a following valid reward block may not exist;
    report that explicitly rather than returning a block the contract rejects.
    """
    block = _block(block)
    boundary = ((block + HALVING_OFFSET) // HALVING_BLOCKS + 1) * HALVING_BLOCKS - HALVING_OFFSET
    if boundary > MAX_REWARD_BLOCK:
        raise ValueError("the next halving is outside the valid reward block range")
    return boundary


def sum_block_rewards_wei(start_block: int, block_count: int) -> int:
    """Sum ``block_count`` rewards starting at ``start_block`` inclusively.

    This visits halving boundaries, never individual blocks.  Once the reward is
    zero, even an enormous valid interval is handled immediately.
    """
    cursor = _block(start_block, "start_block")
    count = _uint256(block_count, "block_count")
    end = cursor + count
    if end > MAX_REWARD_BLOCK + 1:
        raise ValueError("reward interval exceeds the valid reward block range")
    total = 0
    while cursor < end:
        reward = block_reward_wei(cursor)
        if reward == 0:
            break
        boundary = next_halving_block(cursor)
        stop = min(end, boundary)
        total += (stop - cursor) * reward
        cursor = stop
    return total


def _prefix_reward_seconds(position: int) -> int:
    """Integer wei-seconds before a position in the fixed model block clock."""
    blocks, remainder = divmod(position, BLOCK_SECONDS)
    # A position just beyond the last valid block is allowed as an exclusive
    # endpoint; it cannot carry a fractional part or request one more reward.
    total = sum_block_rewards_wei(0, blocks) * BLOCK_SECONDS
    if remainder:
        total += block_reward_wei(blocks) * remainder
    return total


def projected_emission_wei(start_ts: int, end_ts: int, anchor_block: int,
                           anchor_timestamp: int) -> Decimal:
    """Project wei over the half-open interval ``[start_ts, end_ts)``.

    Integer timestamps can split a three-second model slot.  The resulting
    fractional model blocks are retained as Decimal reference amounts.  The
    same anchor also supports past intervals down to model block zero; it must
    never be interpreted as a reconstruction of actual past block production.
    All integration precedes Decimal conversion, so adjacent segments share
    the exact same integer area before the final division by three.
    """
    start = _uint256(start_ts, "start_ts")
    end = _uint256(end_ts, "end_ts")
    block = _block(anchor_block, "anchor_block")
    anchor = _uint256(anchor_timestamp, "anchor_timestamp")
    if end < start:
        raise ValueError("end_ts must not precede start_ts")
    origin = (block + 1) * BLOCK_SECONDS
    start_position = origin + start - anchor
    end_position = origin + end - anchor
    maximum = (MAX_REWARD_BLOCK + 1) * BLOCK_SECONDS
    if start_position < 0 or end_position > maximum:
        raise ValueError("projection exceeds the valid model block clock")
    numerator = (_prefix_reward_seconds(end_position)
                 - _prefix_reward_seconds(start_position))
    with localcontext() as context:
        context.prec = 120
        return Decimal(numerator) / BLOCK_SECONDS


def _amount(number: Decimal) -> str:
    for threshold, unit in ((Decimal(10)**12, "万亿"),
                            (Decimal(10)**8, "亿"),
                            (Decimal(10)**4, "万")):
        if abs(number) >= threshold:
            return f"{number / threshold:.3f}{unit}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def build_emission_meta(block: int, reference_timestamp: int,
                        network_total_power: int | None, *,
                        observed_reward_wei: int | None = None) -> dict:
    """Build JSON-ready display fields from one verified block anchor.

    ``observed_reward_wei``, when supplied, must be the integer result of the
    on-chain reward getter for this exact block.  A mismatch fails closed.  The
    current daily rate uses this block's reward; future-interval projections
    deliberately use the next block, as the contract does.
    """
    block = _block(block)
    reference = _uint256(reference_timestamp, "reference_timestamp")
    power = (None if network_total_power is None else
             _uint256(network_total_power, "network_total_power"))
    reward = block_reward_wei(block)
    if observed_reward_wei is not None:
        observed = _uint256(observed_reward_wei, "observed_reward_wei")
        if observed != reward:
            raise ValueError("observed block reward does not match the official model")
    halvings, elapsed_blocks = divmod(block + HALVING_OFFSET, HALVING_BLOCKS)
    try:
        next_block = next_halving_block(block)
    except ValueError:
        next_block = None
    remaining = next_block - block if next_block is not None else None
    # Estimate when the boundary block arrives after the observed block.  The
    # continuous future-emission integral starts paying the B + 1 model slot
    # immediately, so its rate boundary is one model slot earlier.  Neither
    # timestamp predicts the exact wall-clock arrival of a future real block.
    next_timestamp = (reference + remaining * BLOCK_SECONDS
                      if remaining is not None else None)
    if next_timestamp is not None and next_timestamp > UINT256_MAX:
        next_timestamp = None
    with localcontext() as context:
        context.prec = 120
        daily = Decimal(reward * BLOCKS_PER_DAY) / WEI_PER_TOKEN
        miner = daily * Decimal("0.75")
        node = daily * Decimal("0.25")
        required = Decimal(power) / miner if power is not None and miner > 0 else None
        cycle_output = Decimal(reward * HALVING_BLOCKS) / WEI_PER_TOKEN
        return {
            "emission_model_version": MODEL_VERSION,
            "emission_reference_block": block,
            "emission_reference_timestamp": reference,
            "emission_block_reward_wei": str(reward),
            "emission_block_time_seconds": BLOCK_SECONDS,
            "emission_blocks_per_day": BLOCKS_PER_DAY,
            "emission_halving_interval_blocks": HALVING_BLOCKS,
            "emission_halving_offset": HALVING_OFFSET,
            "emission_next_halving_block": next_block,
            "emission_blocks_until_halving": remaining,
            "emission_next_halving_estimated_timestamp": next_timestamp,
            "emission_current_cycle": halvings + 1,
            "emission_cycle_elapsed_days": float(Decimal(elapsed_blocks) / BLOCKS_PER_DAY),
            "emission_cycle_output_tokens": float(cycle_output),
            "emission_daily_total_tokens": float(daily),
            "emission_daily_total_display": f"{daily / 10**8:.3f}亿/日",
            "emission_daily_miner_tokens": float(miner),
            "emission_daily_miner_display": f"{miner / 10**8:.3f}亿/日",
            "emission_daily_node_tokens": float(node),
            "emission_daily_node_display": f"{node / 10**8:.3f}亿/日",
            "power_required_per_mars_daily": float(required) if required is not None else None,
            "power_required_per_mars_daily_display": _amount(required) if required is not None else None,
            "emission_total_supply_cap_tokens": TOTAL_SUPPLY_CAP_TOKENS,
            "emission_total_supply_cap_display": "2000亿",
            "emission_miner_share": 0.75,
            "emission_node_share": 0.25,
        }
