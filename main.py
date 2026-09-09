# ============================================================
# ADX / DMI — V5
# ============================================================
#
# Deriv tick data -> synthetic OHLC
#
# V5:
#   - Бүх боломжтой history ашиглана
#   - 20 tick = 1 synthetic candle
#   - Богино tick noise-ийг багасгана
#   - Standard TR / +DM / -DM
#   - Wilder smoothing
#   - Standard DX / ADX
#   - Artificial 5/95, 80 clamp байхгүй
#
# ЗӨВХӨН ADX/DMI LOGIC ӨӨРЧЛӨГДСӨН.
# Бусад Feature / Structure / Score logic-д хүрээгүй.
# ============================================================

def calculate_adx_dmi(prices, period=14):

    TICKS_PER_CANDLE = 20

    if not isinstance(prices, list):
        return None

    clean_prices = []

    for price in prices:

        try:
            value = float(price)

            if math.isfinite(value):
                clean_prices.append(value)

        except Exception:
            continue

    minimum_candles = (
        period * 3 + 5
    )

    if len(clean_prices) < (
        minimum_candles
        * TICKS_PER_CANDLE
    ):
        return None

    # --------------------------------------------------------
    # 1. Бүх боломжтой tick history ашиглана
    # --------------------------------------------------------

    usable_count = (
        len(clean_prices)
        // TICKS_PER_CANDLE
    ) * TICKS_PER_CANDLE

    if usable_count < (
        minimum_candles
        * TICKS_PER_CANDLE
    ):
        return None

    usable_prices = clean_prices[
        :usable_count
    ]

    # --------------------------------------------------------
    # 2. Tick -> synthetic OHLC
    # --------------------------------------------------------

    candles = []

    for start in range(
        0,
        len(usable_prices),
        TICKS_PER_CANDLE
    ):

        chunk = usable_prices[
            start:
            start + TICKS_PER_CANDLE
        ]

        if len(chunk) != TICKS_PER_CANDLE:
            continue

        candle_open = chunk[0]
        candle_high = max(chunk)
        candle_low = min(chunk)
        candle_close = chunk[-1]

        candles.append({
            "open": candle_open,
            "high": candle_high,
            "low": candle_low,
            "close": candle_close,
        })

    if len(candles) < (
        period * 3 + 2
    ):
        return None

    # --------------------------------------------------------
    # 3. TR / +DM / -DM
    # --------------------------------------------------------

    tr_values = []
    plus_dm_values = []
    minus_dm_values = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        current_high = current["high"]
        current_low = current["low"]

        previous_high = previous["high"]
        previous_low = previous["low"]
        previous_close = previous["close"]

        tr = max(
            current_high - current_low,
            abs(
                current_high
                - previous_close
            ),
            abs(
                current_low
                - previous_close
            ),
        )

        up_move = (
            current_high
            - previous_high
        )

        down_move = (
            previous_low
            - current_low
        )

        if (
            up_move > down_move
            and up_move > 0
        ):
            plus_dm = up_move
        else:
            plus_dm = 0.0

        if (
            down_move > up_move
            and down_move > 0
        ):
            minus_dm = down_move
        else:
            minus_dm = 0.0

        tr_values.append(
            max(0.0, tr)
        )

        plus_dm_values.append(
            max(0.0, plus_dm)
        )

        minus_dm_values.append(
            max(0.0, minus_dm)
        )

    if len(tr_values) < (
        period * 2 + 1
    ):
        return None

    # --------------------------------------------------------
    # 4. Initial Wilder sums
    # --------------------------------------------------------

    smoothed_tr = sum(
        tr_values[:period]
    )

    smoothed_plus_dm = sum(
        plus_dm_values[:period]
    )

    smoothed_minus_dm = sum(
        minus_dm_values[:period]
    )

    dx_values = []

    latest_plus_di = 0.0
    latest_minus_di = 0.0

    # --------------------------------------------------------
    # 5. Initial DI / DX
    # --------------------------------------------------------

    if smoothed_tr > 0:

        latest_plus_di = (
            100.0
            * smoothed_plus_dm
            / smoothed_tr
        )

        latest_minus_di = (
            100.0
            * smoothed_minus_dm
            / smoothed_tr
        )

        di_sum = (
            latest_plus_di
            + latest_minus_di
        )

        if di_sum > 0:

            dx_values.append(
                100.0
                * abs(
                    latest_plus_di
                    - latest_minus_di
                )
                / di_sum
            )

    # --------------------------------------------------------
    # 6. Wilder recursive smoothing
    # --------------------------------------------------------

    for i in range(
        period,
        len(tr_values)
    ):

        smoothed_tr = (
            smoothed_tr
            - (
                smoothed_tr
                / period
            )
            + tr_values[i]
        )

        smoothed_plus_dm = (
            smoothed_plus_dm
            - (
                smoothed_plus_dm
                / period
            )
            + plus_dm_values[i]
        )

        smoothed_minus_dm = (
            smoothed_minus_dm
            - (
                smoothed_minus_dm
                / period
            )
            + minus_dm_values[i]
        )

        if smoothed_tr <= 0:
            continue

        latest_plus_di = (
            100.0
            * smoothed_plus_dm
            / smoothed_tr
        )

        latest_minus_di = (
            100.0
            * smoothed_minus_dm
            / smoothed_tr
        )

        di_sum = (
            latest_plus_di
            + latest_minus_di
        )

        if di_sum > 0:

            dx = (
                100.0
                * abs(
                    latest_plus_di
                    - latest_minus_di
                )
                / di_sum
            )

            if math.isfinite(dx):

                dx_values.append(
                    dx
                )

    # --------------------------------------------------------
    # 7. ADX
    # --------------------------------------------------------

    if len(dx_values) < period:
        return None

    adx = (
        sum(
            dx_values[:period]
        )
        / period
    )

    for dx in dx_values[period:]:

        adx = (
            (
                adx
                * (period - 1)
            )
            + dx
        ) / period

    # --------------------------------------------------------
    # 8. Numerical safety only
    # --------------------------------------------------------

    if not math.isfinite(adx):
        return None

    if not math.isfinite(
        latest_plus_di
    ):
        return None

    if not math.isfinite(
        latest_minus_di
    ):
        return None

    return {
        "adx": max(
            0.0,
            min(100.0, adx)
        ),

        "plus_di": max(
            0.0,
            min(100.0, latest_plus_di)
        ),

        "minus_di": max(
            0.0,
            min(100.0, latest_minus_di)
        ),
    }
