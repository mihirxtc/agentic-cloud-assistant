"""Scores reflect applicable fully observed check families, never resource multiplicity."""
from copy import deepcopy
from services.scan_coverage import assessment, SERVICE_ITEMS


def empty_scan():
    return {name: {'status': 'ok', 'coverage': 'complete', field: []} for name, field in SERVICE_ITEMS.items()}


def test_empty_or_incomplete_assessment_has_no_score():
    scan = empty_scan()
    assert assessment(scan)['health_score']['value'] is None
    scan['s3']['buckets'] = [{'name': 'bucket', 'is_public': False}]
    assert assessment(scan)['health_score']['value'] == 100
    scan['iam']['status'] = 'error'
    assert assessment(scan)['health_score']['value'] is None


def test_each_applicable_family_counts_once_regardless_of_resource_count():
    scan = empty_scan()
    scan['s3']['buckets'] = [{'name': 'public', 'is_public': True}]
    scan['iam']['users'] = [{'username': 'user', 'has_mfa': True, 'last_login': '2026-11-04T00:00:00+00:00'}]
    first = assessment(scan)['health_score']
    assert first['total_weight'] == 6 and first['failed_weight'] == 3
    assert first['value'] == 50
    scan['s3']['buckets'] *= 100
    assert assessment(scan)['health_score'] == first


def test_unknown_detail_and_unknown_rule_cannot_receive_a_clean_score():
    from services.health_score import health_score
    scan = empty_scan()
    scan['s3']['buckets'] = [{'name': 'bucket', 'is_public': None}]
    assert assessment(scan)['health_score']['value'] is None
    scan['s3']['buckets'][0]['is_public'] = False
    result = assessment(scan)
    assert health_score(result['coverage'], [{'rule': 'UNRECOGNIZED'}])['value'] is None


def test_nonapplicable_rules_do_not_inflate_denominator():
    scan = empty_scan()
    scan['s3']['buckets'] = [{'name': 'bucket', 'is_public': True}]
    score = assessment(scan)['health_score']
    assert score['applicable_rules'] == ['S3_BUCKET_PUBLIC']
    assert score['value'] == 0 and score['total_weight'] == 3
    assert assessment(scan)['coverage']['rules']['S3_BUCKET_PUBLIC']['evaluated_count'] == 1
