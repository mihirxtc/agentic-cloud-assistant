"""Weighted built-in check-family score, not an account-wide security rating."""
RULE_WEIGHTS = {'SSH_PORT_OPEN': 3, 'RDP_PORT_OPEN': 3, 'S3_BUCKET_PUBLIC': 3,
                'UNRESTRICTED_ALL_TRAFFIC': 3, 'IAM_USER_NO_MFA': 2,
                'IAM_USER_INACTIVE': 1, 'DEFAULT_VPC_IN_USE': 1}
METHOD = 'weighted_applicable_rule_families_v1'


def health_score(coverage, findings):
    result = {'value': None, 'method': METHOD, 'scope': 'Built-in checks on inspected resource types',
              'applicable_rules': [], 'failing_rules': [], 'total_weight': 0, 'failed_weight': 0}
    rules = coverage.get('rules', {})
    if coverage.get('status') != 'complete' or any(rules.get(rule, {}).get('status') != 'complete' for rule in RULE_WEIGHTS):
        return {**result, 'reason': 'Incomplete assessment; unknown checks cannot be scored'}
    applicable = [rule for rule in RULE_WEIGHTS if rules[rule].get('evaluated_count', 0) > 0]
    if not applicable:
        return {**result, 'reason': 'No applicable resources were inspected'}
    failing = {item.get('rule') for item in findings}
    if not failing.issubset(applicable):
        return {**result, 'reason': 'Findings do not match the applicable check coverage'}
    total = sum(RULE_WEIGHTS[rule] for rule in applicable)
    failed = sum(RULE_WEIGHTS[rule] for rule in failing)
    return {**result, 'value': round((total - failed) / total * 100),
            'applicable_rules': applicable, 'failing_rules': sorted(failing),
            'total_weight': total, 'failed_weight': failed,
            'reason': 'Each applicable check family counts once, regardless of the number of affected resources'}
