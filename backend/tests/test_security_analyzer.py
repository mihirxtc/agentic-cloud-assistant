"""
Unit tests for services/security_analyzer.py.

All 7 rule functions are pure Python with no I/O — no AWS credentials or
mocking required. Run with:
    cd backend
    python -m pytest tests/test_security_analyzer.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta, timezone

from services.security_analyzer import (
    make_finding,
    check_ssh_port_open,
    check_rdp_port_open,
    check_s3_bucket_public,
    check_iam_user_no_mfa,
    check_unrestricted_traffic,
    check_iam_user_inactive,
    check_default_vpc,
    run_security_analysis,
)


def _sg_data(*entries):
    return {
        "status": "ok",
        "security_groups": [
            {"id": "sg-001", "name": "test-sg", "open_to_internet": list(entries)}
        ],
    }


def _iam_data(*users):
    return {"status": "ok", "users": list(users)}


def _s3_data(*buckets):
    return {"status": "ok", "buckets": list(buckets)}


def _vpc_data(*vpcs):
    return {"status": "ok", "vpcs": list(vpcs)}


def test_make_finding_contains_all_nine_fields():
    f = make_finding(
        "id", "res", "EC2_SECURITY_GROUP", "SSH_PORT_OPEN",
        "HIGH", "title", "desc", "rec", {"k": "v"},
    )
    for key in (
        "finding_id", "resource_id", "resource_type", "rule",
        "severity", "title", "description", "recommendation", "metadata",
    ):
        assert key in f


def test_ssh_open_detects_port_22():
    result = check_ssh_port_open(_sg_data({"port": 22, "protocol": "tcp"}))
    assert len(result) == 1
    assert result[0]["rule"] == "SSH_PORT_OPEN"
    assert result[0]["severity"] == "HIGH"
    assert result[0]["resource_id"] == "sg-001"
    assert result[0]["resource_type"] == "EC2_SECURITY_GROUP"


def test_ssh_open_ignores_other_ports():
    assert check_ssh_port_open(_sg_data({"port": 80, "protocol": "tcp"})) == []


def test_ssh_open_no_internet_exposure():
    data = {"status": "ok", "security_groups": [
        {"id": "sg-001", "name": "test-sg", "open_to_internet": []}
    ]}
    assert check_ssh_port_open(data) == []


def test_ssh_open_status_not_ok():
    assert check_ssh_port_open({"status": "error", "message": "failed"}) == []


def test_ssh_open_empty_dict():
    assert check_ssh_port_open({}) == []


def test_rdp_open_detects_port_3389():
    result = check_rdp_port_open(_sg_data({"port": 3389, "protocol": "tcp"}))
    assert len(result) == 1
    assert result[0]["rule"] == "RDP_PORT_OPEN"
    assert result[0]["severity"] == "HIGH"


def test_rdp_open_ignores_other_ports():
    assert check_rdp_port_open(_sg_data({"port": 22, "protocol": "tcp"})) == []


def test_rdp_open_status_not_ok():
    assert check_rdp_port_open({"status": "error"}) == []


def test_s3_public_detects_public_bucket():
    result = check_s3_bucket_public(_s3_data({"name": "my-bucket", "is_public": True}))
    assert len(result) == 1
    assert result[0]["rule"] == "S3_BUCKET_PUBLIC"
    assert result[0]["severity"] == "HIGH"
    assert result[0]["resource_id"] == "my-bucket"


def test_s3_public_ignores_private_bucket():
    assert check_s3_bucket_public(_s3_data({"name": "my-bucket", "is_public": False})) == []


def test_s3_public_status_not_ok():
    assert check_s3_bucket_public({"status": "error"}) == []


def test_iam_no_mfa_detects_user_without_mfa():
    result = check_iam_user_no_mfa(_iam_data({"username": "alice", "has_mfa": False}))
    assert len(result) == 1
    assert result[0]["rule"] == "IAM_USER_NO_MFA"
    assert result[0]["severity"] == "MEDIUM"
    assert result[0]["resource_id"] == "alice"


def test_iam_no_mfa_ignores_user_with_mfa():
    assert check_iam_user_no_mfa(_iam_data({"username": "bob", "has_mfa": True})) == []


def test_iam_no_mfa_status_not_ok():
    assert check_iam_user_no_mfa({"status": "error"}) == []


def test_unrestricted_traffic_detects_all_protocol():
    result = check_unrestricted_traffic(_sg_data({"port": None, "protocol": "-1"}))
    assert len(result) == 1
    assert result[0]["rule"] == "UNRESTRICTED_ALL_TRAFFIC"
    assert result[0]["severity"] == "HIGH"


def test_unrestricted_traffic_ignores_specific_protocol():
    assert check_unrestricted_traffic(_sg_data({"port": 443, "protocol": "tcp"})) == []


def test_unrestricted_traffic_status_not_ok():
    assert check_unrestricted_traffic({"status": "error"}) == []


def test_iam_inactive_never_logged_in():
    result = check_iam_user_inactive(_iam_data({"username": "alice", "last_login": "Never"}))
    assert len(result) == 1
    assert result[0]["rule"] == "IAM_USER_INACTIVE"
    assert result[0]["severity"] == "LOW"
    assert result[0]["resource_id"] == "alice"


def test_iam_inactive_login_over_90_days_ago():
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    result = check_iam_user_inactive(_iam_data({"username": "bob", "last_login": old}))
    assert len(result) == 1


def test_iam_inactive_recent_login_no_finding():
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert check_iam_user_inactive(_iam_data({"username": "carol", "last_login": recent})) == []


def test_iam_inactive_status_not_ok():
    assert check_iam_user_inactive({"status": "error"}) == []


def test_default_vpc_detected():
    result = check_default_vpc(_vpc_data({"id": "vpc-001", "cidr": "172.31.0.0/16", "is_default": True}))
    assert len(result) == 1
    assert result[0]["rule"] == "DEFAULT_VPC_IN_USE"
    assert result[0]["severity"] == "LOW"
    assert result[0]["resource_id"] == "vpc-001"


def test_default_vpc_ignores_custom_vpc():
    assert check_default_vpc(_vpc_data({"id": "vpc-002", "cidr": "10.0.0.0/16", "is_default": False})) == []


def test_default_vpc_status_not_ok():
    assert check_default_vpc({"status": "error"}) == []


def test_run_analysis_findings_sorted_high_to_low():
    scan_data = {
        "security_groups": _sg_data({"port": 22, "protocol": "tcp"}),
        "s3": {"status": "ok", "buckets": []},
        "iam": _iam_data(
            {"username": "alice", "has_mfa": False, "last_login": "Never"}
        ),
        "vpc": _vpc_data({"id": "vpc-001", "cidr": "172.31.0.0/16", "is_default": True}),
    }
    findings = run_security_analysis(scan_data)
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=lambda s: order[s])


def test_run_analysis_empty_scan_data_returns_empty():
    assert run_security_analysis({}) == []


def test_run_analysis_clean_infrastructure_no_findings():
    scan_data = {
        "security_groups": {"status": "ok", "security_groups": []},
        "s3": {"status": "ok", "buckets": []},
        "iam": {"status": "ok", "users": []},
        "vpc": {"status": "ok", "vpcs": []},
    }
    assert run_security_analysis(scan_data) == []
