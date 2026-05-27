"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — Yahoo Finance (institutional framing)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        — r/wallstreetbets, r/stocks, r/investing

The agent does not use tool-calling; the data is in the prompt from
turn 0. The LLM produces the sentiment report in a single invocation.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages
from tradingagents.market_resolver import resolve_market, Market
from tradingagents.dataflows.akshare_china import (
    get_social_sentiment,
    get_zt_pool_summary,
    get_hot_up_rank,
    get_lhb_summary,
    get_xueqiu_attention,
)
from tradingagents.dataflows.google_trends import get_google_trends


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a sentiment report in a
    single LLM call.
    """

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)

        # Pre-fetch all sources. Each fetcher degrades gracefully and
        # returns a string (no exceptions surface from here), so the LLM
        # always sees something — either real data or a clear placeholder.
        news_block = get_news.func(ticker, start_date, end_date)
        reddit_block = fetch_reddit_posts(ticker)

        # Route social sentiment by market: StockTwits/Google Trends (US/crypto) vs
        # Eastmoney 个股热度 (A-share / HK).  Both blocks are always passed to
        # the prompt template; the unused one carries an informative placeholder.
        _market = resolve_market(ticker)
        # curr_date: use trade_date (analysis date) for historical accuracy.
        # TODO: if sentiment_analyst_node is ever refactored to take a signature
        #   parameter, wire curr_date through properly. For now, use end_date
        #   (the trade_date from state) which is accurate for non-backtest runs.
        curr_date = end_date

        if _market in (Market.A_SHARE, Market.HK):
            stocktwits_block = (
                "<stocktwits not queried: ticker is a non-US listing "
                "(A-share / HK); CN/HK retail signal is in the eastmoney_social_block below>"
            )
            eastmoney_social_block = get_social_sentiment(ticker)
            google_trends_block = "<google trends not queried: ticker is a non-US listing>"
        else:
            stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
            eastmoney_social_block = (
                "<eastmoney social not queried: ticker is a non-CN/HK listing; "
                "US/crypto retail signal is in the stocktwits_block above>"
            )
            google_trends_block = get_google_trends(ticker, lookback_days=30, geo="US")

        # A-share-only blocks
        if _market == Market.A_SHARE:
            zt_pool_block = get_zt_pool_summary(curr_date)
            hot_up_block = get_hot_up_rank()
            lhb_block = get_lhb_summary(ticker, curr_date, days_back=5)
            xueqiu_block = get_xueqiu_attention(ticker)
        else:
            zt_pool_block = "<涨停板 not applicable: A-share only>"
            hot_up_block = "<飙升榜 not applicable: A-share only>"
            lhb_block = "<龙虎榜 not applicable: A-share only>"
            xueqiu_block = "<雪球 attention not applicable: A-share only>"

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            eastmoney_social_block=eastmoney_social_block,
            zt_pool_block=zt_pool_block,
            hot_up_block=hot_up_block,
            lhb_block=lhb_block,
            xueqiu_block=xueqiu_block,
            google_trends_block=google_trends_block,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # No bind_tools — the data is already in the prompt; a single LLM
        # call produces the report directly.
        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    eastmoney_social_block: str,
    zt_pool_block: str = "<涨停板 not applicable: A-share only>",
    hot_up_block: str = "<飙升榜 not applicable: A-share only>",
    lhb_block: str = "<龙虎榜 not applicable: A-share only>",
    xueqiu_block: str = "<雪球 attention not applicable: A-share only>",
    google_trends_block: str = "<google trends not queried: ticker is a non-US listing>",
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Eastmoney 股吧 retail attention — CN-mainland & HK retail social signal
(active when ticker is A-share or HK; eastmoney 个股热度 ranks stocks by retail
discussion volume on the Eastmoney 股吧 platform — the closest CN equivalent to
StockTwits. Lower rank number = more retail attention.)
<start_of_eastmoney_social>
{eastmoney_social_block}
<end_of_eastmoney_social>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

### A-share 涨停板 池 (retail FOMO snapshot for this trading day)
<start_of_zt_pool>
{zt_pool_block}
<end_of_zt_pool>

### A-share attention 飙升榜 (stocks suddenly trending in retail attention)
<start_of_hot_up>
{hot_up_block}
<end_of_hot_up>

### A-share 龙虎榜 (large-fund / hot-money desk transactions — hard capital flow signal)
<start_of_lhb>
{lhb_block}
<end_of_lhb>

### 雪球 attention rank (CN retail social — most-similar to StockTwits)
<start_of_xueqiu>
{xueqiu_block}
<end_of_xueqiu>

### Google Trends interest (US retail search-attention signal)
<start_of_google_trends>
{google_trends_block}
<end_of_google_trends>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context — the title alone often misleads.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this caveat explicitly. If the sources are silent on a given subreddit, say so.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

9. **When the eastmoney_social_block contains a Retail attention rank section (i.e., the ticker is A-share or HK), use the rank-direction signal: rising attention often precedes price moves both ways; combine with news to judge whether the attention is positive or negative narrative.**

10. **A-share 涨停板:** If the analyzed ticker is in today's 涨停板, that's a strong FOMO signal; if NOT but sector peers are in 涨停板, a narrative tailwind may still exist.

11. **A-share 飙升榜:** Sudden appearance signals an attention spike (positive or negative); always cross-reference the 涨跌幅 sign to determine direction of the narrative.

12. **A-share 龙虎榜:** This is a "hard signal" — large-fund net buy/sell is directly visible via real capital flows; the 解读 field reveals whether activity was institutional vs hot-money desk.

13. **雪球 attention:** Percentile rank — top 1% (rank ≤ ~56 of 5604) = mainstream CN retail focus; high weekly rank delta = current momentum surge. Low cumulative rank AND high weekly rank = recent attention spike.

14. **Google Trends:** Rising trend is a leading retail-attention indicator; sharp peaks often coincide with news events — compare with news flow to disambiguate positive vs negative narrative.

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** — Bullish / Bearish / Neutral / Mixed — with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** — what each of news / StockTwits / Reddit / A-share attention signals (where applicable) is telling you, with specific evidence (cite message counts, ratios, notable posts).
3. **Divergences, alignments, and key narratives** across sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
