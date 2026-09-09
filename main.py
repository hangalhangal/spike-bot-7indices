# ============================================================
# BOS / CHoCH — CORRECTED LOGIC
# ============================================================

bos = "NONE"
choch = "NONE"


# ============================================================
# BULLISH STRUCTURE
# HH + HL
# Current price breaking latest swing low means
# bullish structure has changed character.
# ============================================================

if (
    high_is_higher
    and low_is_higher
):

    if current_price < latest_low:

        choch = "BEARISH"


# ============================================================
# BEARISH STRUCTURE
# LH + LL
# Current price breaking latest swing high means
# bearish structure has changed character.
# ============================================================

elif (
    high_is_lower
    and low_is_lower
):

    if current_price > latest_high:

        choch = "BULLISH"


# ============================================================
# MIXED STRUCTURE
# HH + LL
# or
# LH + HL
#
# Do not call BOS/CHoCH from mixed structure.
# Wait for a clearer directional structure.
# ============================================================

elif (
    (
        high_is_higher
        and low_is_lower
    )
    or
    (
        high_is_lower
        and low_is_higher
    )
):

    bos = "NONE"
    choch = "NONE"


# ============================================================
# BOS
#
# BOS is only accepted when the current structure is already
# directional and price breaks the corresponding swing.
# ============================================================

if (
    high_is_higher
    and low_is_higher
):

    # Bullish structure remains intact unless latest low breaks.
    # Therefore a move above the latest high is NOT bearish.
    if current_price > latest_high:

        bos = "BULLISH"


elif (
    high_is_lower
    and low_is_lower
):

    # Bearish structure remains intact unless latest high breaks.
    if current_price < latest_low:

        bos = "BEARISH"
