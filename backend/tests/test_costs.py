"""Calendar-bound billing and failure semantics with a fake Cost Explorer client."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from services import cost_analyzer as cost


def total(start, end, amount, unit='USD', estimated=False):
    return {'TimePeriod': {'Start': start, 'End': end}, 'Estimated': estimated,
            'Total': {'UnblendedCost': {'Amount': str(amount), 'Unit': unit}}}


def group(start, end, name, amount, unit='USD'):
    return {'TimePeriod': {'Start': start, 'End': end}, 'Estimated': True,
            'Groups': [{'Keys': [name], 'Metrics': {'UnblendedCost': {'Amount': str(amount), 'Unit': unit}}}]}


@pytest.fixture
def ce(monkeypatch):
    client = Mock()
    client.get_cost_and_usage.return_value = {'ResultsByTime': []}
    monkeypatch.setattr(cost, 'get_connection', lambda: SimpleNamespace(client=lambda *a, **k: client))
    return client


def test_month_to_date_end_is_exclusive_and_never_future(ce):
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-11-01', '2026-11-04', 12)]}
    result = cost.get_current_month_cost(date(2026, 11, 4))
    assert result['amount'] == 12 and result['end_exclusive']
    assert ce.get_cost_and_usage.call_args.kwargs['TimePeriod'] == {'Start': '2026-11-01', 'End': '2026-11-04'}
    ce.reset_mock()
    result = cost.get_current_month_cost(date(2026, 11, 1))
    assert result['amount'] is None and result['status'] == 'not_available'
    ce.get_cost_and_usage.assert_not_called()


@pytest.mark.parametrize('today,start,end', [(date(2026, 1, 4), '2025-10-01', '2026-01-01'),
    (date(2024, 3, 1), '2023-12-01', '2024-03-01'), (date(2026, 11, 30), '2026-08-01', '2026-11-01')])
def test_history_uses_completed_calendar_months(ce, today, start, end):
    cost.get_monthly_trend(3, today)
    assert ce.get_cost_and_usage.call_args.kwargs['TimePeriod'] == {'Start': start, 'End': end}


def test_completed_month_comparison_keeps_signed_actual_delta(ce):
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-09-01', '2026-10-01', 100), total('2026-10-01', '2026-11-01', 90)]}
    trend = cost.get_monthly_trend(2, date(2026, 11, 4))
    comparison = cost.detect_cost_anomaly(trend)
    assert comparison['status'] == 'complete' and comparison['percentage'] == -10
    assert not comparison['detected']
    trend[-1]['amount'] = 120
    assert cost.detect_cost_anomaly(trend)['detected'] is True


@pytest.mark.parametrize('change', ['partial', 'missing', 'currency', 'zero', 'negative', 'legacy'])
def test_invalid_or_incomparable_periods_never_look_like_zero_change(ce, change):
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-09-01', '2026-10-01', 100), total('2026-10-01', '2026-11-01', 120)]}
    trend = cost.get_monthly_trend(2, date(2026, 11, 4))
    if change == 'partial': trend[-1]['end'] = '2026-10-04'
    if change == 'missing': trend[0]['end'] = '2026-09-30'
    if change == 'currency': trend[-1]['currency'] = 'EUR'
    if change == 'zero': trend[0]['amount'] = 0
    if change == 'negative': trend[0]['amount'] = -1
    if change == 'legacy': trend = [{'amount': 100}, {'amount': 120}]
    result = cost.detect_cost_anomaly(trend)
    assert result['detected'] is None and result['percentage'] is None


def test_failure_no_data_and_reported_zero_are_distinct(ce):
    ce.get_cost_and_usage.side_effect = RuntimeError('private billing data')
    failed = cost.get_current_month_cost(date(2026, 11, 4))
    assert failed['status'] == 'failed' and failed['amount'] is None
    assert 'private' not in str(failed)
    ce.get_cost_and_usage.side_effect = None
    assert cost.get_current_month_cost(date(2026, 11, 4))['amount'] is None
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-11-01', '2026-11-04', 0)]}
    zero = cost.get_current_month_cost(date(2026, 11, 4))
    assert zero['status'] == 'complete' and zero['amount'] == 0


def test_service_breakdown_paginates_and_preserves_credits(ce):
    ce.get_cost_and_usage.side_effect = [
        {'ResultsByTime': [group('2026-11-01', '2026-11-04', 'EC2', 10)], 'NextPageToken': 'next'},
        {'ResultsByTime': [group('2026-11-01', '2026-11-04', 'Credits', -2)]}]
    result = cost.get_cost_by_service(date(2026, 11, 4))
    assert [item['amount'] for item in result] == [10, -2]
    assert ce.get_cost_and_usage.call_args.kwargs['NextPageToken'] == 'next'


def test_pagination_failure_and_repeated_tokens_do_not_publish_partial_totals(ce):
    ce.get_cost_and_usage.side_effect = [
        {'ResultsByTime': [group('2026-11-01', '2026-11-04', 'EC2', 10)], 'NextPageToken': 'next'},
        RuntimeError('permission denied')]
    assert cost.get_cost_by_service(date(2026, 11, 4)) == []
    ce.get_cost_and_usage.side_effect = None
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [], 'NextPageToken': 'same'}
    assert cost.get_current_month_cost(date(2026, 11, 4))['status'] == 'failed'


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', 'not-money'])
def test_invalid_money_is_unavailable(ce, amount):
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-11-01', '2026-11-04', amount)]}
    assert cost.get_current_month_cost(date(2026, 11, 4))['amount'] is None


def test_missing_history_month_invalidates_comparison(ce):
    ce.get_cost_and_usage.return_value = {'ResultsByTime': [total('2026-10-01', '2026-11-01', 100)]}
    report = cost.get_cost_report(3, date(2026, 11, 4))
    assert report['monthly_trend'] == []
    assert report['coverage']['monthly_trend'] == 'failed'
    assert report['comparison']['percentage'] is None


def test_model_failure_preserves_billing_results(environment, monkeypatch):
    server, _, _, _ = environment
    from services import aws_connection as aws
    identity = SimpleNamespace(identity={'account_id': '111111111111', 'region': 'eu-west-1'})
    monkeypatch.setattr(aws, 'verify_connection', lambda *_: identity)
    report = {'status': 'complete', 'current_month': {'amount': 12}, 'comparison': {'percentage': -10}}
    monkeypatch.setattr(server, 'get_cost_report', lambda **_: report.copy())
    monkeypatch.setattr(server, 'prompt_llm', AsyncMock(side_effect=RuntimeError('private model error')))
    result = asyncio.run(server.get_cost_with_summary(region='eu-west-1'))
    assert result['current_month']['amount'] == 12
    assert result['comparison']['percentage'] == -10
    assert 'summary_error' in result
    assert 'private model error' not in str(result)
    monkeypatch.setattr(server, 'get_cost_report', lambda **_: {'status': 'failed'})
    server.prompt_llm.reset_mock()
    result = asyncio.run(server.get_cost_with_summary(region='eu-west-1'))
    server.prompt_llm.assert_not_called()
