SMA_PERIODS = (50, 120, 150, 200)
EMA_PERIODS = (5, 12, 20, 26, 40)
MACD_PAIRS = ((12, 26, ""), (5, 20, "_5_20"), (5, 40, "_5_40"), (20, 40, "_20_40"))
FEATURE_COLUMNS = ("stage", *(f"sma_{n}" for n in SMA_PERIODS),
                   *(f"ema_{n}" for n in EMA_PERIODS),
                   *("macd" + suffix for _, _, suffix in MACD_PAIRS),
                   *("signal" + suffix for _, _, suffix in MACD_PAIRS))
