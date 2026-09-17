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
    'JIOFIN': 'JIOFIN.NS', 'CIPLA': 'CIPLA.NS',
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
    stamp = meta.get('regularMarketTime')
    if not all(positive(v) for v in (price, previous, stamp)):
        raise ValueError('Missing or invalid price, previous close or timestamp')
    if stamp > time.time() + 300:
        raise ValueError('Quote timestamp is in the future')
    return {'symbol': display, 'ticker': ticker, 'price': price, 'previousClose': previous,
            'asOf': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
            'sourceUrl': 'https://finance.yahoo.com/quote/' + ticker + '/'}


def main():
    quotes = []
    for display, ticker in SYMBOLS.items():
        url = f'https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d'
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        # Fail visibly on rate limits or schema changes. Do not overwrite the last
        # successful snapshot with partial results, zeros or invented values.
        with urlopen(request, timeout=25) as response:
            quotes.append(parse_quote(json.load(response), display, ticker))
        print(f'Collected {display} ({ticker})')
        time.sleep(1)
    data = {'source': 'Yahoo Finance · NSE', 'collectedAt': datetime.now(timezone.utc).isoformat(),
            'quotes': quotes}
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
    print('Saved six verified quotes and updated the standalone HTML snapshot.')


if __name__ == '__main__':
    main()
