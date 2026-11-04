"""AWS inventory with explicit collection coverage and unknown detail values."""
import concurrent.futures
from functools import partial, wraps
from datetime import datetime, timezone

from services.aws_connection import get_connection
from botocore.exceptions import ClientError


def _error(error):
    # Do not echo SDK messages, request parameters or credentials into responses.
    code = error.response.get("Error", {}).get("Code", "AWSAPIError") if isinstance(error, ClientError) else type(error).__name__
    return {"code": code, "message": "AWS inspection failed; check permissions and retry."}


def _pages(client, operation, key, **kwargs):
    items, errors = [], []
    try:
        for page in client.get_paginator(operation).paginate(**kwargs):
            items.extend(page[key])
    except Exception as error:
        errors.append({"operation": operation, **_error(error)})
    return items, errors


def _result(key, items, errors, inventory_complete=True):
    state = "complete" if not errors else "partial" if items or inventory_complete else "failed"
    return {"status": {"complete": "ok", "partial": "partial", "failed": "error"}[state],
            "coverage": state, "inventory_complete": inventory_complete,
            "errors": errors, "error": "Some AWS inspections failed" if errors else None,
            "user_count" if key == "users" else "count": len(items), key: items}


def _scan(key):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = datetime.now(timezone.utc).isoformat()
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                result = _result(key, [], [_error(error)], False)
            return {**result, "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat()}
        return wrapped
    return decorate


@_scan("instances")
def scan_ec2(region: str = "") -> dict:
    client = get_connection(region).client("ec2", region_name=region)
    raw, errors = _pages(client, "describe_instances", "Reservations", Filters=[{
        "Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    instances = [{"id": item.get("InstanceId", ""),
                  "name": next((t["Value"] for t in item.get("Tags", []) if t["Key"] == "Name"), ""),
                  "type": item.get("InstanceType", ""), "state": item.get("State", {}).get("Name", ""),
                  "public_ip": item.get("PublicIpAddress"), "private_ip": item.get("PrivateIpAddress"),
                  "launch_time": str(item.get("LaunchTime", "Unknown")),
                  "security_group_ids": [sg["GroupId"] for sg in item.get("SecurityGroups", [])]}
                 for reservation in raw for item in reservation.get("Instances", [])]
    return _result("instances", instances, errors, not errors)


def _public_block(client, operation, **kwargs):
    try:
        return getattr(client, operation)(**kwargs)["PublicAccessBlockConfiguration"], None
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchPublicAccessBlockConfiguration", "NoSuchPublicAccessBlock"):
            return {}, None
        return None, _error(error)
    except Exception as error:
        return None, _error(error)


def _effective_flag(bucket, account, key):
    if any(config is not None and config.get(key) is True for config in (bucket, account)):
        return True
    return False if bucket is not None and account is not None else None


@_scan("buckets")
def scan_s3() -> dict:
    """Inspect bucket ACL/policy configuration; object ACLs/access points are outside scope."""
    connection = get_connection()
    client = connection.client("s3")
    raw, errors = _pages(client, "list_buckets", "Buckets")
    inventory_complete = not errors
    account_block, account_error = _public_block(connection.client("s3control"), "get_public_access_block", AccountId=connection.account_id)
    if account_error:
        errors.append({"operation": "get_account_public_access_block", **account_error})
    buckets = []
    for item in raw:
        name = item["Name"]
        detail_errors = []
        block, error = _public_block(client, "get_public_access_block", Bucket=name)
        if error:
            detail_errors.append({"operation": "get_public_access_block", **error})
        acl_public = policy_public = None
        try:
            grants = client.get_bucket_acl(Bucket=name)["Grants"]
            acl_public = any(g.get("Grantee", {}).get("URI") in (
                "http://acs.amazonaws.com/groups/global/AllUsers",
                "http://acs.amazonaws.com/groups/global/AuthenticatedUsers") for g in grants)
        except Exception as error:
            detail_errors.append({"operation": "get_bucket_acl", **_error(error)})
        try:
            policy_public = client.get_bucket_policy_status(Bucket=name)["PolicyStatus"]["IsPublic"]
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "NoSuchBucketPolicy":
                policy_public = False
            else:
                detail_errors.append({"operation": "get_bucket_policy_status", **_error(error)})
        except Exception as error:
            detail_errors.append({"operation": "get_bucket_policy_status", **_error(error)})
        ignore_acl = _effective_flag(block, account_block, "IgnorePublicAcls")
        restrict_policy = _effective_flag(block, account_block, "RestrictPublicBuckets")
        acl_exposed = False if ignore_acl is True or acl_public is False else True if ignore_acl is False and acl_public is True else None
        policy_exposed = False if restrict_policy is True or policy_public is False else True if restrict_policy is False and policy_public is True else None
        is_public = True if True in (acl_exposed, policy_exposed) else False if acl_exposed is False and policy_exposed is False else None
        flags = [_effective_flag(block, account_block, flag) for flag in (
            "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")]
        all_blocked = False if False in flags else True if all(f is True for f in flags) else None
        buckets.append({"name": name, "created": str(item.get("CreationDate", "Unknown")),
                        "is_public": is_public, "block_public_access": all_blocked,
                        "acl_public": acl_public, "policy_public": policy_public,
                        "public_access_status": "unknown" if is_public is None else "public" if is_public else "not_public",
                        "errors": detail_errors, "coverage": "partial" if detail_errors or account_error else "complete"})
        errors.extend({"resource_id": name, **error} for error in detail_errors)
    result = _result("buckets", buckets, errors, inventory_complete)
    result["scope"] = "Bucket ACLs/policies and bucket/account public-access blocks only. Object ACLs, access points and organization controls are not assessed; public grants do not prove anonymous object readability."
    return result


def _fetch_user_details(iam_client, user: dict) -> dict:
    username = user["UserName"]
    errors, fields = [], {}
    def detail(operation, key):
        items, failures = _pages(iam_client, operation, key, UserName=username)
        fields[operation] = "unknown" if failures else "complete"
        errors.extend(failures)
        return items if not failures else None
    mfa = detail("list_mfa_devices", "MFADevices")
    attached = detail("list_attached_user_policies", "AttachedPolicies")
    inline = detail("list_user_policies", "PolicyNames")
    groups = detail("list_groups_for_user", "Groups")
    keys = detail("list_access_keys", "AccessKeyMetadata")
    access_keys = None if keys is None else []
    for key in keys or []:
        last_used = None
        try:
            date = iam_client.get_access_key_last_used(AccessKeyId=key["AccessKeyId"])["AccessKeyLastUsed"].get("LastUsedDate")
            last_used = str(date) if date else "Never"
        except Exception as error:
            errors.append({"operation": "get_access_key_last_used", **_error(error)})
        access_keys.append({"key_id": key["AccessKeyId"][:12] + "...", "status": key["Status"],
                            "last_used": last_used, "last_used_status": "unknown" if last_used is None else "complete"})
    return {"username": username, "user_id": user.get("UserId", ""),
            "created": str(user.get("CreateDate", "Unknown")),
            "has_mfa": None if mfa is None else bool(mfa),
            "last_login": str(user["PasswordLastUsed"]) if user.get("PasswordLastUsed") else "Never",
            "attached_policies": None if attached is None else [{"name": p["PolicyName"], "arn": p["PolicyArn"]} for p in attached],
            "inline_policies": inline, "groups": None if groups is None else [g["GroupName"] for g in groups],
            "access_keys": access_keys, "field_statuses": fields, "errors": errors,
            "coverage": "partial" if errors else "complete"}


@_scan("users")
def scan_iam() -> dict:
    client = get_connection().client("iam")
    raw, errors = _pages(client, "list_users", "Users")
    inventory_complete = not errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        users = list(executor.map(partial(_fetch_user_details, client), raw))
    errors.extend({"resource_id": user["username"], **error} for user in users for error in user["errors"])
    return _result("users", users, errors, inventory_complete)


@_scan("security_groups")
def scan_security_groups(region: str = "") -> dict:
    client = get_connection(region).client("ec2", region_name=region)
    raw, errors = _pages(client, "describe_security_groups", "SecurityGroups")
    groups = []
    for sg in raw:
        rules, public = [], []
        for rule in sg.get("IpPermissions", []):
            entry = {"port": rule.get("FromPort"), "to_port": rule.get("ToPort"),
                     "protocol": str(rule.get("IpProtocol", "unknown")),
                     "ipv4_cidrs": [r["CidrIp"] for r in rule.get("IpRanges", [])],
                     "ipv6_cidrs": [r["CidrIpv6"] for r in rule.get("Ipv6Ranges", [])],
                     "prefix_list_ids": [r["PrefixListId"] for r in rule.get("PrefixListIds", [])],
                     "source_security_groups": rule.get("UserIdGroupPairs", [])}
            rules.append(entry)
            sources = [cidr for cidr in entry["ipv4_cidrs"] + entry["ipv6_cidrs"] if cidr in ("0.0.0.0/0", "::/0")]
            if sources:
                public.append({**entry, "sources": sources})
        groups.append({"id": sg.get("GroupId", ""), "name": sg.get("GroupName", ""),
                       "description": sg.get("Description", ""), "vpc_id": sg.get("VpcId"),
                       "ingress_rules": rules, "open_to_internet": public, "is_dangerous": bool(public)})
    return _result("security_groups", groups, errors, not errors)


@_scan("vpcs")
def scan_vpc(region: str = "") -> dict:
    client = get_connection(region).client("ec2", region_name=region)
    raw, errors = _pages(client, "describe_vpcs", "Vpcs")
    inventory_complete = not errors
    subnets, failures = _pages(client, "describe_subnets", "Subnets")
    errors.extend(failures)
    vpcs = [{"id": vpc["VpcId"],
             "name": next((t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"), ""),
             "cidr": vpc.get("CidrBlock", ""), "is_default": vpc.get("IsDefault", False),
             "state": vpc.get("State", ""),
             "subnet_count": None if failures else sum(sn["VpcId"] == vpc["VpcId"] for sn in subnets),
             "subnet_count_status": "unknown" if failures else "complete"} for vpc in raw]
    return _result("vpcs", vpcs, errors, inventory_complete)


def scan_existing_infra_for_context(region: str = "") -> str:
    """Scan existing AWS resources and return a human-readable string of resource IDs for LLM context. Silently skips any service that fails."""
    lines = [f"EXISTING AWS INFRASTRUCTURE (region: {region}):"]
    lines.append("Reference these IDs directly in HCL — do NOT create duplicates.\n")

    try:
        ec2 = (
            get_connection(region).client("ec2", region_name=region)
        )

        try:
            sg_paginator = ec2.get_paginator("describe_security_groups")
            sgs = [sg for page in sg_paginator.paginate() for sg in page["SecurityGroups"]]
            if sgs:
                lines.append("SECURITY GROUPS:")
                for sg in sgs:
                    ports = []
                    for rule in sg.get("IpPermissions", []):
                        fp = rule.get("FromPort")
                        tp = rule.get("ToPort")
                        if fp is not None:
                            ports.append(str(fp) if fp == tp else f"{fp}-{tp}")
                    port_str = ",".join(ports) if ports else "none"
                    vpc = sg.get("VpcId", "no-vpc") or "no-vpc"
                    lines.append(
                        f'  {sg["GroupId"]} | "{sg["GroupName"]}" | vpc={vpc} | inbound-ports: {port_str}'
                    )
                lines.append("")
        except Exception:
            pass

        try:
            vpc_pag = ec2.get_paginator("describe_vpcs")
            vpcs = [v for page in vpc_pag.paginate() for v in page["Vpcs"]]
            if vpcs:
                lines.append("VPCS:")
                for vpc in vpcs:
                    name = next(
                        (t["Value"] for t in vpc.get("Tags", []) if t["Key"] == "Name"), ""
                    )
                    label = f' "{name}"' if name else ""
                    default = " [DEFAULT]" if vpc.get("IsDefault") else ""
                    lines.append(
                        f'  {vpc["VpcId"]}{label} | {vpc["CidrBlock"]}{default}'
                    )
                lines.append("")
        except Exception:
            pass

        try:
            sn_pag = ec2.get_paginator("describe_subnets")
            subnets = [sn for page in sn_pag.paginate() for sn in page["Subnets"]]
            if subnets:
                lines.append("SUBNETS:")
                for sn in subnets:
                    name = next(
                        (t["Value"] for t in sn.get("Tags", []) if t["Key"] == "Name"), ""
                    )
                    label = f' "{name}"' if name else ""
                    lines.append(
                        f'  {sn["SubnetId"]}{label} | {sn["CidrBlock"]} | az={sn["AvailabilityZone"]} | vpc={sn["VpcId"]}'
                    )
                lines.append("")
        except Exception:
            pass

    except Exception:
        lines.append("  (EC2/VPC scan unavailable)\n")

    try:
        iam = (
            get_connection().client("iam")
        )
        role_paginator = iam.get_paginator("list_roles")
        roles = [r for page in role_paginator.paginate() for r in page["Roles"]]
        if roles:
            lines.append("IAM ROLES:")
            for r in roles[:20]:  # cap at 20 to avoid token explosion
                lines.append(f'  {r["RoleName"]} | {r["Arn"]}')
            if len(roles) > 20:
                lines.append(f"  ... and {len(roles) - 20} more roles")
            lines.append("")
    except Exception:
        pass

    try:
        s3 = (
            get_connection().client("s3")
        )
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if buckets:
            lines.append("S3 BUCKETS (names are globally unique — do not reuse):")
            for name in buckets:
                lines.append(f"  {name}")
            lines.append("")
    except Exception:
        pass

    if len(lines) <= 2:
        return "No existing infrastructure data available."

    return "\n".join(lines)


@_scan("security_groups")
def scan_sg_usage(region: str = "") -> dict:
    """Inspect ENI associations; absence of an ENI is not proof an SG can be deleted."""
    client = get_connection(region).client("ec2", region_name=region)
    groups, errors = _pages(client, "describe_security_groups", "SecurityGroups")
    inventory_complete = not errors
    interfaces, failures = _pages(client, "describe_network_interfaces", "NetworkInterfaces")
    errors.extend(failures)
    usage = {group["GroupId"]: [] for group in groups}
    for eni in interfaces:
        eni_id = eni["NetworkInterfaceId"]
        instance = eni.get("Attachment", {}).get("InstanceId")
        for group in eni.get("Groups", []):
            if group["GroupId"] in usage:
                usage[group["GroupId"]].append({"resource": instance or eni.get("Description") or eni_id,
                                              "resource_type": "EC2" if instance else "ENI", "eni_id": eni_id})
    unused = None if errors else [sg for sg, associations in usage.items() if not associations]
    return {**_result("security_groups", groups, errors, inventory_complete),
            "total_count": len(groups), "sg_usage": usage, "unused_sg_ids": unused,
            "unused_count": None if unused is None else len(unused),
            "scope": "ENI associations only. Other references and future service associations are not assessed; do not infer deletion safety."}
