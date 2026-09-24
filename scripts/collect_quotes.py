"""Collect public Yahoo Finance quote data. No account, API key or mock prices."""
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = {
    'ZOMIIM': 'ETERNAL.NS', 'ADAPOR': 'ADANIPORTS.NS',
    'ADAPOW': 'ADANIPOWER.NS', 'YESBANK': 'YESBANK.NS',
    'JIOFIN': 'JIOFIN.NS', 'CIPLA': 'CIPLA.NS', 'ADAENT': 'ADANIENT.NS',
}


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def parse_quote(payload, display, ticker):
    chart = payload['chart']
    if chart.get('error'):
        raise ValueError('Provider returned an error')
    meta = chart['result'][0]['meta']
    if meta.get('symbol') != ticker or meta.get('currency') != 'INR' or meta.get('exchangeName') != 'NSI':
        raise ValueError('Unexpected symbol, exchange or currency')
    price = meta.get('regularMarketPrice')
    # With range=1d, chartPreviousClose is the previous trading session close.
    previous = meta.get('previousClose', meta.get('chartPreviousClose'))
    low, high = meta.get('fiftyTwoWeekLow'), meta.get('fiftyTwoWeekHigh')
    if not positive(low) or not positive(high) or low > high:
        raise ValueError('Missing or invalid 52-week range')
    stamp = meta.get('regularMarketTime')
    if not all(positive(v) for v in (price, previous, stamp)):
        raise ValueError('Missing or invalid price, previous close or timestamp')
    if stamp > time.time() + 300:
        raise ValueError('Quote timestamp is in the future')
    return {'symbol': display, 'ticker': ticker, 'price': price, 'previousClose': previous,
            'low52': low, 'high52': high,
            'asOf': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            'sourceUrl': 'https://finance.yahoo.com/quote/' + ticker + '/'}


def fetch_quote(item):
    display, ticker = item
    url = f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d&_={time.time_ns()}'
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=10) as response:
        return parse_quote(json.load(response), display, ticker)


def collect_live():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        quotes = list(pool.map(fetch_quote, SYMBOLS.items()))
    return {'source': 'Yahoo Finance · NSE', 'mode': 'on-demand',
            'collectedAt': datetime.now(timezone.utc).isoformat(), 'quotes': quotes}


def main():
    data = collect_live()
    data['mode'] = 'snapshot'
    serialized = json.dumps(data, indent=2, ensure_ascii=False)
    page = ROOT / 'index.html'
    html = page.read_text()
    html, count = re.subn(r'(<script id="initial-quotes" type="application/json">).*?(</script>)',
                         lambda m: m[1] + serialized.replace('<', '\\u003c') + m[2], html, flags=re.S)
    if count != 1:
        raise ValueError('Expected exactly one initial-quotes placeholder')
    for path, content in [(ROOT / 'quotes.json', serialized + '\n'), (page, html)]:
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content)
        temporary.replace(path)
    print('Saved all verified quotes and updated the standalone HTML snapshot.')


if __name__ == '__main__':
    main()

