# ============================================================
# STOCK RANKING MODEL — V1
# ============================================================
#
# PURPOSE:
#
# Track a universe of stocks.
#
# Every 10 trading days:
#
#   1. Download market data
#   2. Calculate:
#        - 10-day momentum
#        - 30-day momentum
#        - 30-day volatility
#   3. Score all eligible stocks
#   4. Rank them
#   5. Select Top 5
#   6. Allocate paper portfolio by score
#   7. Compare against equal-weight baseline
#   8. Save permanent cycle history
#   9. Repeat
#
# This program DOES NOT place real trades.
# ============================================================


import json
from pathlib import Path

import pandas as pd
import yfinance as yf


# ============================================================
# 1. SETTINGS
# ============================================================

# Stocks currently allowed to compete for portfolio selection.
#
# Later this will come from the website/database instead of
# being manually edited here.

universe = [
    "AAPL",
    "MSFT",
    "KO",
    "PEP",
    "XOM",
    "JNJ",
    "PG",
    "V",
    "DIS",
    "NVDA"
]


# Starting theoretical money
STARTING_BALANCE = 10.00


# Number of stocks selected each cycle
TOP_N = 5


# Feature windows
SHORT_WINDOW = 10
LONG_WINDOW = 30
VOLATILITY_WINDOW = 30


# One cycle = exactly 10 trading days
REBALANCE_EVERY = 10


# How much historical data yfinance downloads
DATA_PERIOD = "1y"


# ============================================================
# FILE LOCATIONS
# ============================================================
#
# Both JSON files now live in static/data/ so the dashboard
# (served from the same project's static/ folder) can fetch
# them directly with zero backend/API code.

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "static" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "current_cycle.json"
HISTORY_FILE = DATA_DIR / "cycle_history.json"
BACKUP_DIR = BASE_DIR / "backups"


# ============================================================
# 2. BASIC JSON FILE HELPERS
# ============================================================

def load_json(path, default):
    """
    Load JSON from a file.

    If the file doesn't exist yet,
    return the supplied default value.
    """

    if not path.exists():
        return default

    try:

        with path.open("r") as file:
            return json.load(file)

    except json.JSONDecodeError as error:

        raise RuntimeError(
            f"{path} exists but contains invalid JSON."
        ) from error


def save_json(path, data):
    """
    Save Python data as readable JSON.
    """

    with path.open("w") as file:

        json.dump(
            data,
            file,
            indent=4
        )


# ============================================================
# 3. LOAD CURRENT STATE / HISTORY
# ============================================================

def load_current_cycle():

    return load_json(
        STATE_FILE,
        None
    )


def load_history():

    return load_json(
        HISTORY_FILE,
        []
    )


# ============================================================
# 4. EXTRACT CLOSE PRICE SAFELY
# ============================================================

def extract_close_series(hist, ticker):
    """
    Return one clean pandas Series containing closing prices.

    Handles both ordinary and MultiIndex yfinance output.
    """

    if hist.empty:
        return None


    # --------------------------------------------------------
    # Newer yfinance versions may use MultiIndex columns.
    # --------------------------------------------------------

    if isinstance(
        hist.columns,
        pd.MultiIndex
    ):

        try:

            close = hist["Close"]

        except KeyError:

            return None


        # Could still be a DataFrame after selecting "Close"
        if isinstance(
            close,
            pd.DataFrame
        ):

            if ticker in close.columns:

                close = close[ticker]

            elif len(close.columns) == 1:

                close = close.iloc[:, 0]

            else:

                return None


    # --------------------------------------------------------
    # Normal single-level columns
    # --------------------------------------------------------

    else:

        if "Close" not in hist.columns:
            return None

        close = hist["Close"]


    close = pd.to_numeric(
        close,
        errors="coerce"
    )


    return close.dropna()


# ============================================================
# 5. DOWNLOAD PRICE DATA
# ============================================================

def fetch_prices(tickers):
    """
    Download approximately one year of adjusted
    daily closing-price history.

    Returns:

        {
            "AAPL": pandas Series,
            "MSFT": pandas Series,
            ...
        }
    """

    prices = {}


    for ticker in tickers:

        print(
            f"Downloading {ticker}..."
        )


        try:

            hist = yf.download(
                ticker,
                period=DATA_PERIOD,
                interval="1d",
                auto_adjust=True,
                progress=False,
                multi_level_index=False
            )


        # ----------------------------------------------------
        # Compatibility fallback for an older yfinance version
        # that may not understand multi_level_index.
        # ----------------------------------------------------

        except TypeError:

            hist = yf.download(
                ticker,
                period=DATA_PERIOD,
                interval="1d",
                auto_adjust=True,
                progress=False
            )


        except Exception as error:

            print(
                f"Download failed for {ticker}: "
                f"{error}"
            )

            continue


        close = extract_close_series(
            hist,
            ticker
        )


        if (
            close is None
            or close.empty
        ):

            print(
                f"No usable price data for {ticker}"
            )

            continue


        prices[ticker] = close


    return prices


# ============================================================
# 6. CHECK THAT REQUIRED TICKERS DOWNLOADED
# ============================================================

def require_prices(
    prices,
    tickers,
    context
):

    missing = [

        ticker

        for ticker in tickers

        if (
            ticker not in prices
            or prices[ticker].empty
        )
    ]


    if missing:

        raise RuntimeError(
            f"{context}: market data completely missing for: "
            + ", ".join(missing)
        )


# ============================================================
# 7. GET HISTORY ONLY UP TO A CERTAIN DATE
# ============================================================

def history_as_of(
    price_series,
    date
):
    """
    Important for preventing look-ahead bias.

    If the model is calculating a score for September 1,
    it must not see September 2+ prices.
    """

    target_date = (
        pd.Timestamp(date).date()
    )


    mask = (
        price_series.index.date
        <= target_date
    )


    return price_series[mask]


# ============================================================
# 8. GET PRICE ON EXACT DATE
# ============================================================

def price_on_date(
    price_series,
    target_date
):

    target_date = (
        pd.Timestamp(
            target_date
        ).date()
    )


    matches = price_series[
        price_series.index.date
        == target_date
    ]


    if matches.empty:
        return None


    return float(
        matches.iloc[-1]
    )


# ============================================================
# 9. FIND TRADING DATES SHARED BY STOCKS
# ============================================================

def common_trading_dates(
    prices,
    tickers
):

    if not tickers:
        return []


    require_prices(
        prices,
        tickers,
        "Cannot determine common trading dates"
    )


    date_sets = []


    for ticker in tickers:

        dates = set(
            prices[ticker]
            .index
            .date
        )

        date_sets.append(
            dates
        )


    common = set.intersection(
        *date_sets
    )


    return sorted(
        pd.Timestamp(date)
        for date in common
    )


# ============================================================
# 10. FIND LATEST COMMON MARKET DATE
# ============================================================

def latest_common_date(
    prices,
    tickers
):

    dates = common_trading_dates(
        prices,
        tickers
    )


    if not dates:

        raise RuntimeError(
            "No common market date could be found."
        )


    return dates[-1]


# ============================================================
# 11. MOMENTUM
# ============================================================

def momentum(
    price_series,
    days
):
    """
    Percentage return over N trading-day intervals.

    Example:

        $100 -> $110

        110 / 100 - 1
        = 0.10
        = +10%

    10 intervals require 11 price observations.
    """

    if len(price_series) < days + 1:
        return None


    old_price = (
        price_series.iloc[
            -(days + 1)
        ]
    )


    new_price = (
        price_series.iloc[-1]
    )


    return (
        new_price
        / old_price
        - 1
    )


# ============================================================
# 12. VOLATILITY
# ============================================================

def volatility(
    price_series,
    days
):
    """
    Standard deviation of recent daily returns.

    Larger value = less stable / more volatile.
    """

    daily_returns = (
        price_series
        .pct_change()
        .dropna()
    )


    if len(daily_returns) < days:
        return None


    recent_returns = (
        daily_returns
        .tail(days)
    )


    return float(
        recent_returns.std()
    )


# ============================================================
# 13. BUILD THE RANKING
# ============================================================

def build_ranking(
    prices,
    current_universe,
    analysis_date
):
    """
    Calculate features and scores.

    There are 3 possible stock states:

        1. Missing completely
            -> ERROR

        2. Exists but not enough history
            -> WARMING UP

        3. Enough history
            -> SCORE IT
    """

    rows = []

    failed_missing = []

    warming_up = []


    for ticker in current_universe:


        # ====================================================
        # Completely missing
        # ====================================================

        if ticker not in prices:

            failed_missing.append(
                ticker
            )

            continue


        # ====================================================
        # Only use data available as of analysis date
        # ====================================================

        history = history_as_of(
            prices[ticker],
            analysis_date
        )


        current_price = price_on_date(
            prices[ticker],
            analysis_date
        )


        # If it downloaded but lacks a price on the shared
        # analysis date, something is inconsistent.
        if current_price is None:

            failed_missing.append(
                ticker
            )

            continue


        # ====================================================
        # Features
        # ====================================================

        momentum_10 = momentum(
            history,
            SHORT_WINDOW
        )


        momentum_30 = momentum(
            history,
            LONG_WINDOW
        )


        volatility_30 = volatility(
            history,
            VOLATILITY_WINDOW
        )


        # ====================================================
        # Warming-up stock
        # ====================================================

        if (
            momentum_10 is None
            or momentum_30 is None
            or volatility_30 is None
        ):

            warming_up.append(
                ticker
            )

            continue


        # ====================================================
        # Fully usable
        # ====================================================

        rows.append(
            {
                "Ticker":
                    ticker,

                "Price":
                    current_price,

                "Momentum10":
                    float(momentum_10),

                "Momentum30":
                    float(momentum_30),

                "Volatility30":
                    float(volatility_30)
            }
        )


    # ========================================================
    # Hard failure: ticker has no usable data
    # ========================================================

    if failed_missing:

        raise RuntimeError(
            "Market data missing/inconsistent for: "
            + ", ".join(
                failed_missing
            )
        )


    # ========================================================
    # Warming-up warning
    # ========================================================

    if warming_up:

        print(
            "\nWARMING UP — not eligible this cycle:"
        )

        for ticker in warming_up:

            print(
                f"  {ticker}"
            )


    # ========================================================
    # Need at least enough stocks to create Top N
    # ========================================================

    if len(rows) < TOP_N:

        raise RuntimeError(
            f"Only {len(rows)} stocks currently have enough "
            f"history to be scored. Need at least {TOP_N}."
        )


    # ========================================================
    # Create DataFrame
    # ========================================================

    ranking = (
        pd.DataFrame(rows)
        .set_index("Ticker")
    )


    # ========================================================
    # Convert raw features into relative percentile scores
    # ========================================================

    # Higher short-term momentum = better
    ranking["Momentum10Score"] = (

        ranking["Momentum10"]
        .rank(
            pct=True
        )
    )


    # Higher long-term momentum = better
    ranking["Momentum30Score"] = (

        ranking["Momentum30"]
        .rank(
            pct=True
        )
    )


    # Lower volatility = better
    ranking["RiskScore"] = (

        ranking["Volatility30"]
        .rank(
            pct=True,
            ascending=False
        )
    )


    # ========================================================
    # V1 SCORING PHILOSOPHY
    # ========================================================
    #
    # 50% = recent momentum
    # 30% = longer momentum
    # 20% = stability
    # ========================================================

    ranking["Score"] = (

        0.50
        * ranking["Momentum10Score"]

        +

        0.30
        * ranking["Momentum30Score"]

        +

        0.20
        * ranking["RiskScore"]
    )


    ranking = ranking.sort_values(
        "Score",
        ascending=False
    )


    return ranking, warming_up


# ============================================================
# 14. CREATE TOP-N PORTFOLIO
# ============================================================

def create_portfolio(
    ranking,
    balance
):

    top = (
        ranking
        .head(TOP_N)
        .copy()
    )


    total_score = (
        top["Score"].sum()
    )


    # --------------------------------------------------------
    # Score-weighted allocation
    # --------------------------------------------------------

    top["Weight"] = (

        top["Score"]
        / total_score
    )


    top["PaperAmount"] = (

        top["Weight"]
        * balance
    )


    return top


# ============================================================
# 15. SAVE CURRENT ACTIVE CYCLE
# ============================================================

def save_current_cycle(
    cycle_number,
    ranking,
    portfolio,
    model_balance,
    baseline_balance,
    analysis_date,
    current_universe,
    warming_up
):

    selected = set(
        portfolio.index
    )


    stocks = {}


    # --------------------------------------------------------
    # Save every ELIGIBLE stock.
    #
    # That means both:
    #
    #   Top 5
    #   Rejected eligible stocks
    #
    # Warming stocks are recorded separately.
    #
    # Momentum/volatility are saved here too (not just Score)
    # so the dashboard's ranking table has real numbers to show
    # instead of just the blended score.
    # --------------------------------------------------------

    for ticker, row in (
        ranking.iterrows()
    ):

        is_selected = (
            ticker in selected
        )


        if is_selected:

            weight = float(
                portfolio.loc[
                    ticker,
                    "Weight"
                ]
            )

        else:

            weight = 0.0


        stocks[ticker] = {

            "entry_price":
                float(
                    row["Price"]
                ),

            "score":
                float(
                    row["Score"]
                ),

            "momentum_10":
                float(
                    row["Momentum10"]
                ),

            "momentum_30":
                float(
                    row["Momentum30"]
                ),

            "volatility_30":
                float(
                    row["Volatility30"]
                ),

            "selected":
                is_selected,

            "weight":
                weight
        }


    state = {

        "cycle_number":
            cycle_number,

        "analysis_date":
            str(
                pd.Timestamp(
                    analysis_date
                ).date()
            ),

        "model_balance":
            float(
                model_balance
            ),

        "baseline_balance":
            float(
                baseline_balance
            ),

        # Exact universe at cycle creation
        "universe_snapshot":
            list(
                current_universe
            ),

        # Stocks that existed but weren't eligible yet
        "warming_up":
            list(
                warming_up
            ),

        # Eligible scored stocks
        "stocks":
            stocks
    }


    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# 16. APPEND COMPLETED CYCLE TO PERMANENT HISTORY
# ============================================================

def append_history(
    record
):

    history = load_history()


    # --------------------------------------------------------
    # Prevent accidental duplicate logging
    # --------------------------------------------------------

    already_exists = any(

        old[
            "cycle_number"
        ]
        == record[
            "cycle_number"
        ]

        for old
        in history
    )


    if already_exists:

        print(
            f"Cycle {record['cycle_number']} "
            "already exists in history."
        )

        return


    history.append(
        record
    )


    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# 17. FIND EXACT 10-TRADING-DAY CYCLE END
# ============================================================

def find_cycle_end_date(
    previous_cycle,
    prices
):

    old_tickers = list(
        previous_cycle[
            "stocks"
        ].keys()
    )


    require_prices(
        prices,
        old_tickers,
        "Cannot evaluate previous cycle"
    )


    previous_date = pd.Timestamp(
        previous_cycle[
            "analysis_date"
        ]
    )


    dates = common_trading_dates(
        prices,
        old_tickers
    )


    # Only dates AFTER cycle began
    new_dates = [

        date

        for date in dates

        if date > previous_date
    ]


    # --------------------------------------------------------
    # Cycle not finished
    # --------------------------------------------------------

    if len(new_dates) < REBALANCE_EVERY:

        return (
            None,
            len(new_dates),
            0
        )


    # --------------------------------------------------------
    # Exact Day 10
    # --------------------------------------------------------

    cycle_end_date = (
        new_dates[
            REBALANCE_EVERY - 1
        ]
    )


    # Did user run the script late?
    late_days = (

        len(new_dates)
        - REBALANCE_EVERY
    )


    return (
        cycle_end_date,
        len(new_dates),
        late_days
    )


# ============================================================
# 18. EVALUATE PREVIOUS CYCLE
# ============================================================

def evaluate_previous_cycle(
    previous_cycle,
    prices,
    cycle_end_date
):
    """
    Evaluate:

        Model portfolio
        Equal-weight baseline
        Top stocks vs rejected stocks
        Balances
    """

    selected_returns = []

    rejected_returns = []

    all_returns = []

    weighted_model_return = 0

    details = []


    # ========================================================
    # Calculate return of every eligible stock
    # ========================================================

    for ticker, old_data in (
        previous_cycle[
            "stocks"
        ].items()
    ):


        if ticker not in prices:

            raise RuntimeError(
                f"Cannot evaluate {ticker}: "
                "price history missing."
            )


        exit_price = price_on_date(
            prices[ticker],
            cycle_end_date
        )


        if exit_price is None:

            raise RuntimeError(
                f"No {ticker} price exists on "
                f"{cycle_end_date.date()}."
            )


        entry_price = float(
            old_data[
                "entry_price"
            ]
        )


        stock_return = (

            exit_price
            / entry_price
            - 1
        )


        all_returns.append(
            stock_return
        )


        details.append(
            {
                "ticker":
                    ticker,

                "selected":
                    old_data[
                        "selected"
                    ],

                "weight":
                    old_data[
                        "weight"
                    ],

                "entry_price":
                    entry_price,

                "exit_price":
                    exit_price,

                "return":
                    stock_return
            }
        )


        # ====================================================
        # Was it selected?
        # ====================================================

        if old_data["selected"]:

            selected_returns.append(
                stock_return
            )


            weighted_model_return += (

                old_data[
                    "weight"
                ]

                * stock_return
            )


        else:

            rejected_returns.append(
                stock_return
            )


    if not all_returns:

        raise RuntimeError(
            "Previous cycle contains no evaluable stocks."
        )


    # ========================================================
    # TOP-N AVERAGE
    # ========================================================

    selected_average = (

        sum(selected_returns)
        / len(selected_returns)

        if selected_returns
        else None
    )


    # ========================================================
    # REJECTED AVERAGE
    # ========================================================

    rejected_average = (

        sum(rejected_returns)
        / len(rejected_returns)

        if rejected_returns
        else None
    )


    # ========================================================
    # SELECTION ADVANTAGE
    # ========================================================

    if (
        selected_average is not None
        and rejected_average is not None
    ):

        selection_advantage = (

            selected_average
            - rejected_average
        )

    else:

        selection_advantage = None


    # ========================================================
    # BASELINE
    #
    # Equal weight across every ELIGIBLE stock from that cycle.
    # ========================================================

    baseline_return = (

        sum(all_returns)
        / len(all_returns)
    )


    # ========================================================
    # MODEL EDGE
    # ========================================================

    model_edge = (

        weighted_model_return
        - baseline_return
    )


    # ========================================================
    # MODEL BALANCE
    # ========================================================

    starting_model_balance = float(

        previous_cycle[
            "model_balance"
        ]
    )


    ending_model_balance = (

        starting_model_balance

        * (
            1
            + weighted_model_return
        )
    )


    # ========================================================
    # BASELINE BALANCE
    # ========================================================

    starting_baseline_balance = float(

        previous_cycle[
            "baseline_balance"
        ]
    )


    ending_baseline_balance = (

        starting_baseline_balance

        * (
            1
            + baseline_return
        )
    )


    # ========================================================
    # PERMANENT CYCLE RECORD
    # ========================================================

    result = {

        "cycle_number":
            previous_cycle[
                "cycle_number"
            ],

        "start_date":
            previous_cycle[
                "analysis_date"
            ],

        "end_date":
            str(
                cycle_end_date.date()
            ),

        "universe_snapshot":
            previous_cycle.get(
                "universe_snapshot",
                list(
                    previous_cycle[
                        "stocks"
                    ].keys()
                )
            ),

        "warming_up":
            previous_cycle.get(
                "warming_up",
                []
            ),

        "eligible_count":
            len(
                previous_cycle[
                    "stocks"
                ]
            ),

        "model_return":
            weighted_model_return,

        "baseline_return":
            baseline_return,

        "model_edge":
            model_edge,

        "selected_average":
            selected_average,

        "rejected_average":
            rejected_average,

        "selection_advantage":
            selection_advantage,

        "starting_model_balance":
            starting_model_balance,

        "ending_model_balance":
            ending_model_balance,

        "starting_baseline_balance":
            starting_baseline_balance,

        "ending_baseline_balance":
            ending_baseline_balance,

        "stocks":
            details
    }


    return result


# ============================================================
# 19. WHOLE-EXPERIMENT STATISTICS
# ============================================================

def calculate_experiment_stats(
    history
):
    """
    Analyze every completed cycle together.
    """

    if not history:

        return None


    cycle_count = len(
        history
    )


    final_model_balance = float(
        history[-1][
            "ending_model_balance"
        ]
    )


    final_baseline_balance = float(
        history[-1][
            "ending_baseline_balance"
        ]
    )


    cumulative_model_return = (

        final_model_balance
        / STARTING_BALANCE
        - 1
    )


    cumulative_baseline_return = (

        final_baseline_balance
        / STARTING_BALANCE
        - 1
    )


    cumulative_edge = (

        cumulative_model_return
        - cumulative_baseline_return
    )


    average_edge = (

        sum(
            cycle[
                "model_edge"
            ]

            for cycle
            in history
        )

        / cycle_count
    )


    beat_baseline_count = sum(

        1

        for cycle
        in history

        if (
            cycle[
                "model_return"
            ]

            >

            cycle[
                "baseline_return"
            ]
        )
    )


    beat_baseline_rate = (

        beat_baseline_count
        / cycle_count
    )


    positive_cycle_count = sum(

        1

        for cycle
        in history

        if cycle[
            "model_return"
        ] > 0
    )


    positive_cycle_rate = (

        positive_cycle_count
        / cycle_count
    )


    # ========================================================
    # MAXIMUM DRAWDOWN
    # ========================================================

    balances = [
        STARTING_BALANCE
    ]


    balances.extend(

        float(
            cycle[
                "ending_model_balance"
            ]
        )

        for cycle
        in history
    )


    peak = balances[0]

    maximum_drawdown = 0.0


    for balance in balances:

        if balance > peak:
            peak = balance


        drawdown = (

            balance
            / peak
            - 1
        )


        if drawdown < maximum_drawdown:

            maximum_drawdown = (
                drawdown
            )


    return {

        "completed_cycles":
            cycle_count,

        "final_model_balance":
            final_model_balance,

        "final_baseline_balance":
            final_baseline_balance,

        "cumulative_model_return":
            cumulative_model_return,

        "cumulative_baseline_return":
            cumulative_baseline_return,

        "cumulative_edge":
            cumulative_edge,

        "average_cycle_edge":
            average_edge,

        "beat_baseline_rate":
            beat_baseline_rate,

        "positive_cycle_rate":
            positive_cycle_rate,

        "maximum_drawdown":
            maximum_drawdown
    }


# ============================================================
# 20. PRINT CURRENT RANKING
# ============================================================

def print_ranking(
    ranking
):

    print(
        "\nCURRENT RANKING"
    )

    print(
        "-" * 78
    )


    for position, (
        ticker,
        row
    ) in enumerate(
        ranking.iterrows(),
        start=1
    ):

        print(

            f"{position:>2}. "

            f"{ticker:<5} "

            f"score={row['Score']:.3f}   "

            f"10d={row['Momentum10']:+.2%}   "

            f"30d={row['Momentum30']:+.2%}   "

            f"vol={row['Volatility30']:.2%}"
        )


# ============================================================
# 21. PRINT NEW PORTFOLIO
# ============================================================

def print_portfolio(
    portfolio,
    balance
):

    print(
        "\nNEW MODEL PORTFOLIO"
    )

    print(
        f"Model balance: ${balance:.2f}"
    )

    print(
        "-" * 50
    )


    for ticker, row in (
        portfolio.iterrows()
    ):

        print(

            f"{ticker:<5} "

            f"{row['Weight']:>7.2%}   "

            f"${row['PaperAmount']:.2f}"
        )


# ============================================================
# 22. PRINT FINISHED CYCLE
# ============================================================

def print_cycle_result(
    result
):

    print(
        f"\nCYCLE "
        f"{result['cycle_number']} "
        "RESULT"
    )


    print(
        f"{result['start_date']} "
        f"-> "
        f"{result['end_date']}"
    )


    print(
        "-" * 65
    )


    for stock in result[
        "stocks"
    ]:

        label = (

            "TOP 5"

            if stock[
                "selected"
            ]

            else "CUT"
        )


        print(

            f"{stock['ticker']:<5} "

            f"{label:<5} "

            f"{stock['return']:+.2%}"
        )


    print()


    print(
        "Model return:          "
        f"{result['model_return']:+.2%}"
    )


    print(
        "Baseline return:       "
        f"{result['baseline_return']:+.2%}"
    )


    print(
        "Model edge:            "
        f"{result['model_edge']:+.2%}"
    )


    print()


    if result[
        "selected_average"
    ] is not None:

        print(
            "Top-5 average:        "
            f"{result['selected_average']:+.2%}"
        )


    if result[
        "rejected_average"
    ] is not None:

        print(
            "Rejected average:     "
            f"{result['rejected_average']:+.2%}"
        )


    if result[
        "selection_advantage"
    ] is not None:

        print(
            "Selection advantage:  "
            f"{result['selection_advantage']:+.2%}"
        )


    print()


    print(
        "Model balance:         "
        f"${result['starting_model_balance']:.2f}"
        " -> "
        f"${result['ending_model_balance']:.2f}"
    )


    print(
        "Baseline balance:      "
        f"${result['starting_baseline_balance']:.2f}"
        " -> "
        f"${result['ending_baseline_balance']:.2f}"
    )


# ============================================================
# 23. PRINT WHOLE-EXPERIMENT SUMMARY
# ============================================================

def print_experiment_summary(
    history
):

    stats = (
        calculate_experiment_stats(
            history
        )
    )


    if stats is None:
        return


    print(
        "\nEXPERIMENT SUMMARY"
    )

    print(
        "-" * 55
    )


    print(
        "Completed cycles:      "
        f"{stats['completed_cycles']}"
    )


    print(
        "Model balance:         "
        f"${stats['final_model_balance']:.2f}"
    )


    print(
        "Baseline balance:      "
        f"${stats['final_baseline_balance']:.2f}"
    )


    print()


    print(
        "Cumulative model:      "
        f"{stats['cumulative_model_return']:+.2%}"
    )


    print(
        "Cumulative baseline:   "
        f"{stats['cumulative_baseline_return']:+.2%}"
    )


    print(
        "Cumulative edge:       "
        f"{stats['cumulative_edge']:+.2%}"
    )


    print()


    print(
        "Average cycle edge:    "
        f"{stats['average_cycle_edge']:+.2%}"
    )


    print(
        "Beat baseline cycles:  "
        f"{stats['beat_baseline_rate']:.2%}"
    )


    print(
        "Positive cycles:       "
        f"{stats['positive_cycle_rate']:.2%}"
    )


    print(
        "Maximum drawdown:      "
        f"{stats['maximum_drawdown']:.2%}"
    )


# ============================================================
# 24. MAIN PROGRAM
# ============================================================

def main():

    print(
        "\n========================================"
    )

    print(
        "        STOCK MODEL V1"
    )

    print(
        "========================================"
    )


    # ========================================================
    # Load memory
    # ========================================================

    previous = (
        load_current_cycle()
    )


    history = (
        load_history()
    )


    # --------------------------------------------------------
    # Safety check:
    #
    # If history exists but active state disappeared,
    # don't accidentally restart at Cycle 1.
    # --------------------------------------------------------

    if (
        previous is None
        and history
    ):

        raise RuntimeError(
            "cycle_history.json exists but "
            "current_cycle.json is missing. "
            "Refusing to restart experiment automatically."
        )


    # ========================================================
    # Determine all tickers that must be downloaded
    # ========================================================

    tickers_to_fetch = set(
        universe
    )


    # We may have removed a stock from today's universe,
    # but still need it to evaluate the old cycle.
    if previous is not None:

        tickers_to_fetch.update(

            previous[
                "stocks"
            ].keys()
        )


    # ========================================================
    # Download prices
    # ========================================================

    print(
        "\nDownloading market data...\n"
    )


    prices = fetch_prices(
        sorted(
            tickers_to_fetch
        )
    )


    if not prices:

        raise RuntimeError(
            "No market data could be downloaded."
        )


    # ========================================================
    # FIRST EVER RUN
    # ========================================================

    if previous is None:

        print(
            "\nNo previous cycle found."
        )

        print(
            "Creating Cycle 1."
        )


        cycle_number = 1


        model_balance = (
            STARTING_BALANCE
        )


        baseline_balance = (
            STARTING_BALANCE
        )


        # Make sure current universe exists in downloaded data.
        require_prices(
            prices,
            universe,
            "Cannot start Cycle 1"
        )


        analysis_date = (
            latest_common_date(
                prices,
                universe
            )
        )


    # ========================================================
    # EXISTING ACTIVE CYCLE
    # ========================================================

    else:

        (
            cycle_end_date,
            available_days,
            late_days
        ) = find_cycle_end_date(
            previous,
            prices
        )


        # ====================================================
        # Not yet 10 trading days
        # ====================================================

        if cycle_end_date is None:

            print(
                f"\nCycle "
                f"{previous['cycle_number']} "
                "is still active."
            )


            print(
                f"{available_days} / "
                f"{REBALANCE_EVERY} "
                "trading days completed."
            )


            print_experiment_summary(
                history
            )


            return


        # ====================================================
        # Evaluate old cycle at EXACT Day 10
        # ====================================================

        result = (
            evaluate_previous_cycle(
                previous,
                prices,
                cycle_end_date
            )
        )


        # ====================================================
        # Save permanent historical record
        # ====================================================

        append_history(
            result
        )


        # Reload in case append succeeded
        history = (
            load_history()
        )


        # ====================================================
        # Display completed cycle
        # ====================================================

        print_cycle_result(
            result
        )


        # ====================================================
        # Carry balances into new cycle
        # ====================================================

        model_balance = (
            result[
                "ending_model_balance"
            ]
        )


        baseline_balance = (
            result[
                "ending_baseline_balance"
            ]
        )


        cycle_number = (

            previous[
                "cycle_number"
            ]

            + 1
        )


        # ====================================================
        # Start next live cycle using CURRENT available data
        # ====================================================

        require_prices(
            prices,
            universe,
            "Cannot create next cycle"
        )


        analysis_date = (
            latest_common_date(
                prices,
                universe
            )
        )


        # ====================================================
        # User ran late
        # ====================================================

        if late_days > 0:

            print(
                "\nWARNING"
            )


            print(
                f"The script was run "
                f"{late_days} trading days late."
            )


            print(
                "The finished cycle was still evaluated "
                "at its exact 10-trading-day boundary."
            )


            print(
                "The new live cycle begins on the latest "
                "available common market date."
            )


            print(
                "The gap is intentionally not treated "
                "as if a portfolio existed retroactively."
            )


    # ========================================================
    # SCORE CURRENT UNIVERSE
    # ========================================================

    ranking, warming_up = (
        build_ranking(
            prices,
            universe,
            analysis_date
        )
    )


    # ========================================================
    # CREATE NEW TOP-5 PORTFOLIO
    # ========================================================

    portfolio = (
        create_portfolio(
            ranking,
            model_balance
        )
    )


    # ========================================================
    # DISPLAY NEW CYCLE
    # ========================================================

    print(
        f"\nAnalysis date: "
        f"{analysis_date.date()}"
    )


    print_ranking(
        ranking
    )


    print_portfolio(
        portfolio,
        model_balance
    )


    print(
        "\nEqual-weight baseline balance: "
        f"${baseline_balance:.2f}"
    )


    # ========================================================
    # SAVE CURRENT ACTIVE CYCLE
    # ========================================================

    save_current_cycle(

        cycle_number,

        ranking,

        portfolio,

        model_balance,

        baseline_balance,

        analysis_date,

        universe,

        warming_up
    )


    print(
        f"\nCycle {cycle_number} "
        "is now active."
    )


    # ========================================================
    # SHOW LONG-TERM EXPERIMENT RESULTS
    # ========================================================

    print_experiment_summary(
        history
    )


# ============================================================
# 25. START PROGRAM
# ============================================================

if __name__ == "__main__":

    main()