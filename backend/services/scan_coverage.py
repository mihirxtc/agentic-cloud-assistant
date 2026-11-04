"""One coverage contract for inventory and security assessments."""
from datetime import datetime, timezone

SERVICE_ITEMS = {"ec2": "instances", "s3": "buckets", "iam": "users",
                 "security_groups": "security_groups", "vpc": "vpcs"}


def scan_coverage(scan):
    services = {}
    for name, key in SERVICE_ITEMS.items():
        data = scan.get(name, {})
        if not isinstance(data, dict):
            data = {}
        items = data.get(key)
        valid = isinstance(items, list) and all(isinstance(item, dict) for item in items)
        state = {"ok": "complete", "partial": "partial", "error": "failed"}.get(data.get("status"), "failed")
        if data.get("coverage", state) != state:
            state = "failed"
        if not valid or state not in ("complete", "partial", "failed"):
            state = "failed"
        services[name] = {"status": state, "observed_count": len(items) if valid else None,
                          "inventory_complete": data.get("inventory_complete", state == "complete"),
                          "errors": data.get("errors", []) or ([] if state == "complete" else [{"message": data.get("error") or "Service was not inspected"}]),
                          "started_at": data.get("started_at"), "completed_at": data.get("completed_at")}
        if data.get("scope"):
            services[name]["scope"] = data["scope"]
    rules = rule_coverage(scan, services)
    states = [service["status"] for service in services.values()] + [rule["status"] for rule in rules.values()]
    status = "complete" if all(s == "complete" for s in states) else "failed" if all(s == "failed" for s in states) else "partial"
    return {"status": status, "services": services, "rules": rules}


def assessment(scan):
    from services.security_analyzer import run_security_analysis
    coverage = scan_coverage(scan)
    usable = {name: data for name, data in scan.items() if name in SERVICE_ITEMS and coverage["services"][name]["status"] != "failed"}
    # Invalid entries remain unknown in coverage, but cannot suppress valid peers.
    if "security_groups" in usable:
        usable["security_groups"] = {**usable["security_groups"], "security_groups": [
            {**item, "open_to_internet": [entry for entry in item.get("open_to_internet", []) if isinstance(entry, dict)]
             if isinstance(item.get("open_to_internet"), list) else []}
            for item in usable["security_groups"]["security_groups"]]}
    findings = run_security_analysis(usable)
    for finding in findings:
        if scan.get("scan_id"):
            finding["scan_id"] = scan["scan_id"]
            finding["aws_identity"] = scan.get("aws_identity")
    from services.health_score import health_score
    score = health_score(coverage, findings)
    counts = {level: sum(f["severity"] == level for f in findings) for level in ("HIGH", "MEDIUM", "LOW")}
    errors = {name: service["errors"] for name, service in coverage["services"].items() if service["status"] != "complete"}
    if coverage["status"] != "complete":
        summary = f"Assessment {coverage['status']}: {len(findings)} confirmed finding(s) in inspected data. Uninspected resources and unknown details may contain additional issues."
    elif findings:
        summary = f"The built-in checks found {len(findings)} issue(s) within the inspected scope."
    else:
        summary = "No findings in the completed built-in checks. This limited assessment does not establish that the AWS account is secure."
    return {"status": coverage["status"], "coverage": coverage, "findings": findings,
            "total_findings": len(findings), "severity_counts": counts, "health_score": score, "errors": errors,
            "scan_id": scan.get("scan_id"), "aws_identity": scan.get("aws_identity"),
            "started_at": scan.get("started_at"), "completed_at": scan.get("completed_at") or datetime.now(timezone.utc).isoformat(),
            "llm_summary": summary}


def rule_coverage(scan, services):
    """Expose field-level unknowns even when list/inventory operations succeeded."""
    def login_known(user):
        value = user.get("last_login")
        if value == "Never":
            value = user.get("created")
        try:
            datetime.fromisoformat(value)
            return True
        except (ValueError, TypeError):
            return False

    def rules_known(item):
        entries = item.get("open_to_internet")
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, dict):
                return False
            protocol = str(entry.get("protocol"))
            if protocol in ("tcp", "6", "udp", "17"):
                start, end = entry.get("port"), entry.get("to_port", entry.get("port"))
                if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= 65535:
                    return False
            elif protocol not in ("-1", "icmp", "icmpv6") and not (protocol.isdigit() and 0 <= int(protocol) <= 255):
                return False
        return True

    checks = {
        "SSH_PORT_OPEN": ("security_groups", rules_known),
        "RDP_PORT_OPEN": ("security_groups", rules_known),
        "UNRESTRICTED_ALL_TRAFFIC": ("security_groups", rules_known),
        "S3_BUCKET_PUBLIC": ("s3", lambda item: isinstance(item.get("is_public"), bool)),
        "IAM_USER_NO_MFA": ("iam", lambda item: isinstance(item.get("has_mfa"), bool)),
        "IAM_USER_INACTIVE": ("iam", login_known),
        "DEFAULT_VPC_IN_USE": ("vpc", lambda item: isinstance(item.get("is_default"), bool)),
    }
    result = {}
    for rule, (service, known) in checks.items():
        data = scan.get(service, {})
        items = data.get(SERVICE_ITEMS[service], []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        unknown = [item.get("id") or item.get("name") or item.get("username") or "unknown"
                   for item in items if isinstance(item, dict) and not known(item)]
        state = services[service]["status"]
        result[rule] = {"status": "partial" if unknown and state != "failed" else state,
                        "unknown_resources": unknown,
                        "evaluated_count": sum(isinstance(item, dict) and known(item) for item in items) if state != "failed" else 0,
                        "observed_count": len(items)}
    return result
