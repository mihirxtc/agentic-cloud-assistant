"""Cost Explorer reports with explicit calendar periods and unavailable states."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from services.aws_connection import get_connection


def utc_today():
    return datetime.now(timezone.utc).date()


def _bounds(start, end):
    return {'start': start.isoformat(), 'end': end.isoformat(), 'end_exclusive': True}


def _query(start, end, grouped=False):
    """Collect every page or report failure; incomplete totals are never zero."""
    if start >= end:
        return {'status': 'not_available', 'rows': [], 'reason': 'No completed days in this period'}
    rows, seen = [], set()
    request = {'TimePeriod': {'Start': start.isoformat(), 'End': end.isoformat()},
               'Granularity': 'MONTHLY', 'Metrics': ['UnblendedCost']}
    if grouped:
        request['GroupBy'] = [{'Type': 'DIMENSION', 'Key': 'SERVICE'}]
    try:
        ce = get_connection().client('ce', region_name='us-east-1')
        while True:
            page = ce.get_cost_and_usage(**request)
            batch = page.get('ResultsByTime')
            if not isinstance(batch, list):
                raise ValueError('Invalid billing response')
            rows.extend(batch)
            token = page.get('NextPageToken')
            if not token:
                break
            if not isinstance(token, str) or token in seen:
                raise ValueError('Billing pagination did not advance')
            seen.add(token)
            request['NextPageToken'] = token
        return {'status': 'complete' if rows else 'not_available', 'rows': rows,
                'reason': None if rows else 'Cost Explorer returned no data for this period'}
    except Exception:
        return {'status': 'failed', 'rows': [], 'reason': 'Cost Explorer data unavailable; check billing access and retry'}


def _money(metric):
    amount = Decimal(metric['Amount'])
    unit = metric['Unit']
    if not amount.is_finite() or not isinstance(unit, str) or not unit:
        raise ValueError('Invalid cost metric')
    return amount, unit


def _totals(rows):
    output = []
    seen = set()
    for row in rows:
        period = row['TimePeriod']
        start, end = date.fromisoformat(period['Start']), date.fromisoformat(period['End'])
        if start >= end or (start, end) in seen:
            raise ValueError('Invalid or repeated billing period')
        seen.add((start, end))
        amount, currency = _money(row['Total']['UnblendedCost'])
        output.append({'month': start.strftime('%Y-%m'), 'amount': float(amount.quantize(Decimal('.01'))),
                       'currency': currency, **_bounds(start, end), 'estimated': bool(row.get('Estimated', True)),
                       'complete_month': start.day == 1 and end == start + relativedelta(months=1),
                       'status': 'complete'})
    return sorted(output, key=lambda item: item['start'])


def get_current_month_cost(as_of=None):
    today = as_of or utc_today()
    start = today.replace(day=1)
    response = _query(start, today)
    result = {'amount': None, 'currency': None, 'period': f'{start} to {today} (end exclusive)',
              **_bounds(start, today), 'status': response['status'], 'estimated': None}
    if response['status'] == 'complete':
        try:
            totals = _totals(response['rows'])
            if len(totals) != 1 or totals[0]['start'] != start.isoformat() or totals[0]['end'] != today.isoformat():
                raise ValueError('Unexpected month-to-date period')
            result.update({k: totals[0][k] for k in ('amount', 'currency', 'estimated')})
            return result
        except (KeyError, TypeError, ValueError, InvalidOperation):
            result['status'] = 'failed'
    result['error'] = response.get('reason') or 'Cost Explorer returned an invalid month-to-date result'
    return result


def _history(months, as_of):
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 12:
        raise ValueError('months must be between 1 and 12')
    end = as_of.replace(day=1)
    start = end - relativedelta(months=months)
    response = _query(start, end)
    result = {'status': response['status'], 'items': [], **_bounds(start, end)}
    if response['status'] == 'complete':
        try:
            items = _totals(response['rows'])
            expected = [(start + relativedelta(months=i)).isoformat() for i in range(months)]
            if [item['start'] for item in items] != expected or not all(item['complete_month'] for item in items):
                raise ValueError('Historical months are missing or incomplete')
            if len({item['currency'] for item in items}) > 1:
                raise ValueError('Historical currencies differ')
            result['items'] = items
            return result
        except (KeyError, TypeError, ValueError, InvalidOperation):
            result['status'] = 'failed'
    result['error'] = response.get('reason') or 'Completed-month history is missing or invalid'
    return result


def get_monthly_trend(months=3, as_of=None):
    """Only completed calendar months; empty means unavailable, never zero spend."""
    return _history(months, as_of or utc_today())['items']


def _services(as_of):
    start = as_of.replace(day=1)
    response = _query(start, as_of, grouped=True)
    result = {'status': response['status'], 'items': [], **_bounds(start, as_of)}
    if response['status'] == 'complete':
        try:
            services, seen = {}, set()
            estimated = False
            for row in response['rows']:
                if row['TimePeriod'] != {'Start': start.isoformat(), 'End': as_of.isoformat()}:
                    raise ValueError('Unexpected grouped billing period')
                estimated |= bool(row.get('Estimated', True))
                groups = row['Groups']
                if not isinstance(groups, list):
                    raise ValueError('Missing service groups')
                for group in groups:
                    name = group['Keys'][0]
                    if not isinstance(name, str) or not name or name in seen:
                        raise ValueError('Repeated or invalid service group')
                    seen.add(name)
                    amount, unit = _money(group['Metrics']['UnblendedCost'])
                    services[name] = {'service': name, 'amount': float(amount.quantize(Decimal('.01'))), 'currency': unit}
            if len({item['currency'] for item in services.values()}) > 1:
                raise ValueError('Service currencies differ')
            result.update(items=sorted((v for v in services.values() if v['amount'] != 0), key=lambda v: v['amount'], reverse=True), estimated=estimated)
            return result
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
            result['status'] = 'failed'
    result['error'] = response.get('reason') or 'Cost Explorer returned an invalid service breakdown'
    return result


def get_cost_by_service(as_of=None):
    return _services(as_of or utc_today())['items']


def detect_cost_anomaly(monthly_trend):
    """Compare the latest two contiguous completed months in a single currency."""
    unknown = {'status': 'not_available', 'detected': None, 'percentage': None,
               'message': 'Two comparable completed months are required', 'basis': 'completed_calendar_months'}
    if len(monthly_trend) < 2:
        return unknown
    try:
        previous, current = monthly_trend[-2:]
        for item in (previous, current):
            start, end = date.fromisoformat(item['start']), date.fromisoformat(item['end'])
            if item.get('status') != 'complete' or not item.get('complete_month') or start.day != 1 or end != start + relativedelta(months=1):
                return unknown
        if previous['end'] != current['start'] or previous['currency'] != current['currency']:
            return unknown
        old, new = Decimal(str(previous['amount'])), Decimal(str(current['amount']))
        if not old.is_finite() or not new.is_finite() or old <= 0 or new < 0:
            return {**unknown, 'message': 'Percentage comparison is unavailable for zero or negative billing baselines'}
        change = (new - old) / old * 100
        return {'status': 'complete', 'detected': change >= 20, 'percentage': float(change.quantize(Decimal('.1'))),
                'basis': 'completed_calendar_months', 'current_period': {k: current[k] for k in ('start', 'end', 'amount', 'currency')},
                'previous_period': {k: previous[k] for k in ('start', 'end', 'amount', 'currency')},
                'estimated': previous.get('estimated', True) or current.get('estimated', True),
                'message': f"{previous['month']} to {current['month']}: {change:+.1f}% ({old:.2f} → {new:.2f} {current['currency']})."}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return unknown


def get_cost_report(months=3, as_of=None):
    today = as_of or utc_today()
    current = get_current_month_cost(today)
    history = _history(months, today)
    services = _services(today)
    errors = {name: value['error'] for name, value in (('current_month', current), ('monthly_trend', history), ('by_service', services)) if value.get('error')}
    statuses = [current['status'], history['status'], services['status']]
    comparison = detect_cost_anomaly(history['items'])
    return {'status': 'complete' if all(s == 'complete' for s in statuses) else 'failed' if all(s == 'failed' for s in statuses) else 'partial',
            'current_month': current, 'monthly_trend': history['items'], 'by_service': services['items'],
            'coverage': {'current_month': current['status'], 'monthly_trend': history['status'], 'by_service': services['status']},
            'comparison': comparison, 'anomaly': comparison,
            'errors': errors, 'as_of': today.isoformat(), 'months_fetched': len(history['items'])}
