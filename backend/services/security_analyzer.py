from datetime import datetime, timezone


def port_exposed(entry: dict, port: int) -> bool:
    if str(entry.get("protocol")) == "-1":
        return True
    protocols = ("tcp", "6", "udp", "17") if port == 3389 else ("tcp", "6")
    if str(entry.get("protocol")) not in protocols:
        return False
    start = entry.get("port")
    end = entry.get("to_port", start)
    return isinstance(start, int) and isinstance(end, int) and start <= port <= end


SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def make_finding(
    finding_id: str,
    resource_id: str,
    resource_type: str,
    rule: str,
    severity: str,
    title: str,
    description: str,
    recommendation: str,
    metadata: dict,
) -> dict:
    """Create a security finding dict with the standard schema."""
    return {
        "finding_id": finding_id,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "rule": rule,
        "severity": severity,
        "title": title,
        "description": description,
        "recommendation": recommendation,
        "metadata": metadata,
    }


def check_ssh_port_open(security_groups_data: dict) -> list:
    """Return HIGH findings for security groups with SSH (port 22) open to the internet."""
    findings = []

    try:
        if security_groups_data.get("status") not in ("ok", "partial"):
            return []

        security_groups = security_groups_data.get("security_groups", [])

        for i, sg in enumerate(security_groups):
            sg_id = sg.get("id", "unknown")
            sg_name = sg.get("name", "unknown")

            for entry in sg.get("open_to_internet", []):
                if port_exposed(entry, 22):
                    finding_id = f"SSH_PORT_OPEN-{sg_id}-{i}"

                    findings.append(
                        make_finding(
                            finding_id=finding_id,
                            resource_id=sg_id,
                            resource_type="EC2_SECURITY_GROUP",
                            rule="SSH_PORT_OPEN",
                            severity="HIGH",
                            title="SSH port exposed to the internet",
                            description=(
                                f"Security group {sg_name} allows SSH (port 22) access "
                                f"from public IPv4 and/or IPv6 sources. This exposes the "
                                f"server to brute-force and credential attacks."
                            ),
                            recommendation=(
                                "Restrict port 22 to specific trusted IP addresses "
                                "only. Review and remove the public IPv4/IPv6 rules."
                            ),
                            metadata={"port": 22, "group_name": sg_name, "protocol": entry.get("protocol"),
                                      "from_port": entry.get("port"), "to_port": entry.get("to_port", entry.get("port")),
                                      "sources": entry.get("sources", [])},
                        )
                    )
                    break

    except Exception:
        return []

    return findings


def check_rdp_port_open(security_groups_data: dict) -> list:
    """Return HIGH findings for security groups with RDP (port 3389) open to the internet."""
    findings = []

    try:
        if security_groups_data.get("status") not in ("ok", "partial"):
            return []

        security_groups = security_groups_data.get("security_groups", [])

        for i, sg in enumerate(security_groups):
            sg_id = sg.get("id", "unknown")
            sg_name = sg.get("name", "unknown")

            for entry in sg.get("open_to_internet", []):
                if port_exposed(entry, 3389):
                    finding_id = f"RDP_PORT_OPEN-{sg_id}-{i}"

                    findings.append(
                        make_finding(
                            finding_id=finding_id,
                            resource_id=sg_id,
                            resource_type="EC2_SECURITY_GROUP",
                            rule="RDP_PORT_OPEN",
                            severity="HIGH",
                            title="RDP port exposed to the internet",
                            description=(
                                f"Security group {sg_name} allows RDP (port 3389) from "
                                f"any IP. This exposes Windows servers to attacks."
                            ),
                            recommendation=(
                                "Restrict port 3389 to specific trusted IP addresses. "
                                "Consider using AWS Systems Manager Session Manager "
                                "instead of RDP for remote access."
                            ),
                            metadata={"port": 3389, "group_name": sg_name, "protocol": entry.get("protocol"),
                                      "from_port": entry.get("port"), "to_port": entry.get("to_port", entry.get("port")),
                                      "sources": entry.get("sources", [])},
                        )
                    )
                    break

    except Exception:
        return []

    return findings


def check_s3_bucket_public(s3_data: dict) -> list:
    """Return HIGH findings for S3 buckets where is_public is True."""
    findings = []

    try:
        if s3_data.get("status") not in ("ok", "partial"):
            return []

        buckets = s3_data.get("buckets", [])

        for i, bucket in enumerate(buckets):
            bucket_name = bucket.get("name", "unknown")

            if bucket.get("is_public") is True:
                finding_id = f"S3_BUCKET_PUBLIC-{bucket_name}-{i}"

                findings.append(
                    make_finding(
                        finding_id=finding_id,
                        resource_id=bucket_name,
                        resource_type="S3_BUCKET",
                        rule="S3_BUCKET_PUBLIC",
                        severity="HIGH",
                        title="S3 bucket configuration permits public grants",
                        description=(
                            f"Bucket {bucket_name} has public bucket ACL or policy grants "
                            f"not suppressed by inspected bucket/account access blocks. "
                            f"Actual object access also depends on other controls; "
                            f"anonymous object readability was not tested."
                        ),
                        recommendation=(
                            "Review and remove public bucket ACL/policy grants. "
                            "Enable S3 Block Public Access settings. Review "
                            "bucket contents for sensitive data."
                        ),
                        metadata={"bucket_name": bucket_name},
                    )
                )

    except Exception:
        return []

    return findings


def check_iam_user_no_mfa(iam_data: dict) -> list:
    """Return MEDIUM findings for IAM users with no MFA device enabled."""
    findings = []

    try:
        if iam_data.get("status") not in ("ok", "partial"):
            return []

        users = iam_data.get("users", [])

        for i, user in enumerate(users):
            username = user.get("username", "unknown")

            if user.get("has_mfa") is False:
                finding_id = f"IAM_USER_NO_MFA-{username}-{i}"

                findings.append(
                    make_finding(
                        finding_id=finding_id,
                        resource_id=username,
                        resource_type="IAM_USER",
                        rule="IAM_USER_NO_MFA",
                        severity="MEDIUM",
                        title="IAM user has no MFA enabled",
                        description=(
                            f"User {username} does not have multi-factor "
                            f"authentication enabled. A stolen password alone "
                            f"could allow access subject to the user permissions."
                        ),
                        recommendation=(
                            "Enable MFA for this user immediately. Use a "
                            "virtual MFA app or hardware security key."
                        ),
                        metadata={"username": username},
                    )
                )

    except Exception:
        return []

    return findings


def check_unrestricted_traffic(security_groups_data: dict) -> list:
    """Return HIGH findings for security groups allowing all traffic.

    boto3 represents allow-all protocol as the string '-1', not an integer.
    """
    findings = []

    try:
        if security_groups_data.get("status") not in ("ok", "partial"):
            return []

        security_groups = security_groups_data.get("security_groups", [])

        for i, sg in enumerate(security_groups):
            sg_id = sg.get("id", "unknown")
            sg_name = sg.get("name", "unknown")

            for entry in sg.get("open_to_internet", []):
                if str(entry.get("protocol")) == "-1":
                    finding_id = f"UNRESTRICTED_ALL_TRAFFIC-{sg_id}-{i}"

                    findings.append(
                        make_finding(
                            finding_id=finding_id,
                            resource_id=sg_id,
                            resource_type="EC2_SECURITY_GROUP",
                            rule="UNRESTRICTED_ALL_TRAFFIC",
                            severity="HIGH",
                            title="All traffic allowed from the internet",
                            description=(
                                f"Security group {sg_name} allows ALL traffic from any "
                                f"IP address. This completely removes network "
                                f"protection for attached resources."
                            ),
                            recommendation=(
                                "Remove this rule immediately. Define specific "
                                "inbound rules for only the ports and protocols "
                                "your application actually needs."
                            ),
                            metadata={"group_name": sg_name, "protocol": "all"},
                        )
                    )
                    break

    except Exception:
        return []

    return findings


def check_iam_user_inactive(iam_data: dict) -> list:
    """Return LOW findings for IAM users inactive for 90+ days.

    Console use only: a never-used user must also be older than 90 days.
    """
    findings = []

    NINETY_DAYS_SECONDS = 90 * 24 * 60 * 60

    try:
        if iam_data.get("status") not in ("ok", "partial"):
            return []

        users = iam_data.get("users", [])

        now_utc = datetime.now(timezone.utc)

        for i, user in enumerate(users):
            username = user.get("username", "unknown")
            last_login = user.get("last_login")

            is_inactive = False

            if last_login == "Never":
                try:
                    created = datetime.fromisoformat(user.get("created", ""))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    is_inactive = (now_utc - created).total_seconds() > NINETY_DAYS_SECONDS
                except (ValueError, TypeError):
                    continue

            elif last_login is not None:
                try:
                    last_login_dt = datetime.fromisoformat(str(last_login))

                    if last_login_dt.tzinfo is None:
                        last_login_dt = last_login_dt.replace(tzinfo=timezone.utc)

                    age_seconds = (now_utc - last_login_dt).total_seconds()

                    if age_seconds > NINETY_DAYS_SECONDS:
                        is_inactive = True

                except (ValueError, TypeError):
                    continue

            if is_inactive:
                finding_id = f"IAM_USER_INACTIVE-{username}-{i}"

                findings.append(
                    make_finding(
                        finding_id=finding_id,
                        resource_id=username,
                        resource_type="IAM_USER",
                        rule="IAM_USER_INACTIVE",
                        severity="LOW",
                        title="IAM user has no observed console login for 90+ days",
                        description=(
                            f"User {username} has not logged in for over 90 "
                            f"days (last console login: {last_login}). This does not "
                            f"establish inactivity of access keys or other programmatic access."
                        ),
                        recommendation=(
                            "Review whether this user still needs access. "
                            "If not, disable or delete the account. If yes, "
                            "ensure credentials are rotated."
                        ),
                        metadata={"username": username, "last_login": last_login},
                    )
                )

    except Exception:
        return []

    return findings


def check_default_vpc(vpc_data: dict) -> list:
    """Return LOW findings where the AWS-managed default VPC is present."""
    findings = []

    try:
        if vpc_data.get("status") not in ("ok", "partial"):
            return []

        vpcs = vpc_data.get("vpcs", [])

        for i, vpc in enumerate(vpcs):
            vpc_id = vpc.get("id", "unknown")
            cidr = vpc.get("cidr", "unknown")

            if vpc.get("is_default") is True:
                finding_id = f"DEFAULT_VPC_IN_USE-{vpc_id}-{i}"

                findings.append(
                    make_finding(
                        finding_id=finding_id,
                        resource_id=vpc_id,
                        resource_type="VPC",
                        rule="DEFAULT_VPC_IN_USE",
                        severity="LOW",
                        title="Default VPC exists",
                        description=(
                            f"The default VPC ({vpc_id}) exists in this region. "
                            f"The default VPC has permissive settings and is not "
                            f"recommended for production workloads."
                        ),
                        recommendation=(
                            "Create a custom VPC with appropriate CIDR ranges, "
                            "subnet configuration, and security controls. "
                            "Migrate resources out of the default VPC."
                        ),
                        metadata={"vpc_id": vpc_id, "cidr": cidr},
                    )
                )

    except Exception:
        return []

    return findings


def run_security_analysis(scan_data: dict) -> list:
    """Run all 7 security rules and return findings sorted HIGH → MEDIUM → LOW."""

    security_groups_data = scan_data.get("security_groups", {})
    s3_data = scan_data.get("s3", {})
    iam_data = scan_data.get("iam", {})
    vpc_data = scan_data.get("vpc", {})

    all_findings = []

    all_findings.extend(check_ssh_port_open(security_groups_data))
    all_findings.extend(check_rdp_port_open(security_groups_data))
    all_findings.extend(check_s3_bucket_public(s3_data))
    all_findings.extend(check_iam_user_no_mfa(iam_data))
    all_findings.extend(check_unrestricted_traffic(security_groups_data))
    all_findings.extend(check_iam_user_inactive(iam_data))
    all_findings.extend(check_default_vpc(vpc_data))

    return sorted(all_findings, key=lambda f: SEVERITY_ORDER.get(f["severity"], 99))


