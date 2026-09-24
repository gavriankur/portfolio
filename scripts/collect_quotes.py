"""Collect public Yahoo Finance quote data. No account, API key or mock prices."""
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta
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


FUNDS = {
    '103504': 'SBI Large Cap Fund',
    '113177': 'Nippon India Small Cap Fund',
    '104683': 'ICICI Prudential Arbitrage Fund',
    '113296': 'Nippon India Index Fund - Nifty 50 Plan',
}
NAV_URL = 'https://portal.amfiindia.com/spages/NAVAll.txt'


def parse_funds(text):
    result = {}
    for line in text.splitlines():
        fields = [part.strip() for part in line.split(';')]
        if not fields or fields[0] not in FUNDS:
            continue
        code = fields[0]
        # AMFI supports both a single name field and separate name/plan/option fields.
        name = ' '.join(fields[3:-2])
        if FUNDS[code].lower() not in name.lower() or 'growth' not in name.lower() or 'direct' in name.lower():
            raise ValueError('AMFI scheme identity changed: ' + code)
        if code in result:
            raise ValueError('Duplicate AMFI scheme: ' + code)
        nav = float(fields[-2])
        nav_date = datetime.strptime(fields[-1], '%d-%b-%Y').date()
        from zoneinfo import ZoneInfo
        if not positive(nav) or nav_date > datetime.now(ZoneInfo('Asia/Kolkata')).date():
            raise ValueError('Invalid NAV/date: ' + code)
        result[code] = {'schemeCode': code, 'name': FUNDS[code], 'plan': 'Regular Growth',
                        'nav': nav, 'navDate': nav_date.isoformat()}
    if set(result) != set(FUNDS):
        raise ValueError('AMFI response is missing a selected fund')
    return [result[code] for code in FUNDS]


def weekly_comparison(fund, payload):
    if str(payload.get('meta', {}).get('scheme_code')) != fund['schemeCode'] or payload.get('status') != 'SUCCESS':
        raise ValueError('Unexpected historical scheme')
    name = payload['meta'].get('scheme_name', '').lower()
    if 'direct' in name or 'growth' not in name:
        raise ValueError('Unexpected historical plan')
    latest = datetime.fromisoformat(fund['navDate']).date()
    target = latest - timedelta(days=7)
    candidates = {}
    for row in payload.get('data', []):
        day = datetime.strptime(row['date'], '%d-%m-%Y').date()
        nav = float(row['nav'])
        if not positive(nav):
            continue
        if day == latest and abs(nav - fund['nav']) > 0.00011:
            raise ValueError('Latest NAV differs between sources')
        if target - timedelta(days=7) <= day <= target:
            if day in candidates and candidates[day] != nav:
                raise ValueError('Conflicting historical NAV')
            candidates[day] = nav
    if not candidates:
        raise ValueError('No published NAV near comparison date')
    day = max(candidates)
    baseline = candidates[day]
    return {'navDate': day.isoformat(), 'nav': baseline,
            'change': round(fund['nav'] - baseline, 4),
            'changePercent': round((fund['nav'] / baseline - 1) * 100, 4),
            'source': 'AMFI'}


def weekly_series(fund, rows):
    latest = datetime.fromisoformat(fund['navDate']).date()
    history = {}
    for day, nav in rows:
        if not positive(nav):
            continue
        if day in history and history[day] != nav:
            raise ValueError('Conflicting historical NAV')
        history[day] = nav
    if latest in history and abs(history[latest] - fund['nav']) > 0.00011:
        raise ValueError('Latest NAV mismatch')
    history[latest] = fund['nav']
    samples = []
    for weeks in range(13, -1, -1):
        target = latest - timedelta(days=weeks * 7)
        eligible = [d for d in history if target - timedelta(days=6) <= d <= target]
        samples.append((max(eligible), history[max(eligible)]) if eligible else None)
    result = []
    for previous, current in zip(samples, samples[1:]):
        if previous is None or current is None:
            result.append(None)
            continue
        day, nav = current
        prior_day, prior_nav = previous
        result.append({'navDate': day.isoformat(), 'nav': nav,
                       'previousDate': prior_day.isoformat(), 'previousNav': prior_nav,
                       'change': round(nav-prior_nav, 4),
                       'changePercent': round((nav/prior_nav-1)*100, 4)})
    return result


def collect_weekly(funds):
    # Reuse history for the same published NAV date, avoiding repeated large downloads.
    try:
        saved = json.loads((ROOT / 'quotes.json').read_text())['mutualFunds']['funds']
        for fund in funds:
            old = next((f for f in saved if f['schemeCode'] == fund['schemeCode']), {})
            if old.get('navDate') == fund['navDate'] and old.get('nav') == fund['nav'] and len(old.get('weeklyHistory', [])) == 13 and all(old['weeklyHistory']):
                fund['weeklyHistory'] = old['weeklyHistory']
        if all('weeklyHistory' in f for f in funds):
            return funds
    except (OSError, ValueError, KeyError):
        pass
    dates = [datetime.fromisoformat(f['navDate']).date() for f in funds]
    start, end = min(dates) - timedelta(days=97), max(dates)
    histories = {code: [] for code in FUNDS}
    try:
        while start <= end:
            stop = min(start + timedelta(days=27), end)
            url = ('https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx'
                   f'?frmdt={start:%d-%b-%Y}&todt={stop:%d-%b-%Y}')
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as response:
                for raw in response:
                    fields = raw.decode('utf-8-sig').strip().split(';')
                    if len(fields) < 6 or fields[0] not in FUNDS:
                        continue
                    name = ' '.join(fields[1:4]).lower()
                    if 'direct' in name or 'growth' not in name:
                        raise ValueError('Unexpected historical plan')
                    histories[fields[0]].append((datetime.strptime(fields[-1].strip(), '%d-%b-%Y').date(), float(fields[-2])))
            start = stop + timedelta(days=1)
        for fund in funds:
            fund['weeklyHistory'] = weekly_series(fund, histories[fund['schemeCode']])
            fund.pop('weekly', None)
    except Exception as error:
        print('WARNING: NAV chart history unavailable:', type(error).__name__)
        for fund in funds:
            fund['historyError'] = 'Historical NAV refresh failed'
    return funds


def collect_funds():
    request = Request(NAV_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=25) as response:
        funds = parse_funds(response.read().decode('utf-8-sig'))
    funds = collect_weekly(funds)
    return {'source': 'AMFI', 'checkedAt': datetime.now(timezone.utc).isoformat(),
            'refreshFailed': False, 'funds': funds}


def main():
    data = collect_live()
    data['mode'] = 'snapshot'
    try:
        data['mutualFunds'] = collect_funds()
    except Exception as error:
        print('WARNING: mutual fund NAV refresh failed:', type(error).__name__)
        prior = {}
        try:
            prior = json.loads((ROOT / 'quotes.json').read_text()).get('mutualFunds', {})
        except (OSError, ValueError):
            pass
        data['mutualFunds'] = {**prior, 'refreshFailed': True,
                              'error': 'AMFI NAV refresh failed; any values shown are previously collected.'}

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

