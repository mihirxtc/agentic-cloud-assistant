import concurrent.futures
from collections import defaultdict
from functools import partial

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


def get_aws_session(credentials: dict) -> boto3.Session:
    """Build a boto3 Session from explicit credentials. Accepts both 'aws_region' and 'region' key names; prevents env-var or instance-role credentials from bleeding across requests."""
    return boto3.Session(
        aws_access_key_id=credentials.get("aws_access_key_id"),
        aws_secret_access_key=credentials.get("aws_secret_access_key"),
        region_name=credentials.get(
            "aws_region", credentials.get("region", "us-east-1")
        ),
    )


def scan_ec2(region: str = "us-east-1", credentials: dict = None) -> dict:
    """Scan all EC2 instances in the given region and return their metadata."""

    try:
        ec2_client = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
        )

        paginator = ec2_client.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[{
                "Name":   "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            }]
        )

        instances = []

        for page in pages:
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    name = ""
                    for tag in instance.get("Tags", []):
                        if tag.get("Key") == "Name":
                            name = tag.get("Value", "")
                            break

                    security_group_ids = []
                    for sg in instance.get("SecurityGroups", []):
                        group_id = sg.get("GroupId")
                        if group_id:
                            security_group_ids.append(group_id)

                    launch_time_raw = instance.get("LaunchTime")
                    launch_time = str(launch_time_raw) if launch_time_raw else "Unknown"

                    instances.append(
                        {
                            "id": instance.get("InstanceId", ""),
                            "name": name,
                            "type": instance.get("InstanceType", ""),
                            "state": instance.get("State", {}).get("Name", ""),
                            "public_ip": instance.get("PublicIpAddress"),
                            "private_ip": instance.get("PrivateIpAddress"),
                            "launch_time": launch_time,
                            "security_group_ids": security_group_ids,
                        }
                    )

        return {
            "status": "ok",
            "count": len(instances),
            "instances": instances,
        }

    except NoCredentialsError:
        return {
            "status": "error",
            "error": "AWS credentials not found. Run 'aws configure' to set them up.",
            "count": 0,
            "instances": [],
        }
    except ClientError as error:
        return {
            "status": "error",
            "error": f"AWS API error: {error.response.get('Error', {}).get('Message', str(error))}",
            "count": 0,
            "instances": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "error": f"Unexpected error: {str(error)}",
            "count": 0,
            "instances": [],
        }


def scan_s3(credentials: dict = None) -> dict:
    """Scan all S3 buckets and determine public-access status via BPA settings then ACL."""

    ALL_USERS_URI = "http://acs.amazonaws.com/groups/global/AllUsers"

    try:
        s3_client = (
            get_aws_session(credentials).client("s3")
            if credentials
            else boto3.client("s3")
        )

        response = s3_client.list_buckets()

        buckets = []

        for bucket in response.get("Buckets", []):
            bucket_name = bucket.get("Name", "")
            created_raw = bucket.get("CreationDate")
            created = str(created_raw) if created_raw else "Unknown"

            # --- Step 1: Block Public Access settings ---
            # IgnorePublicAcls=True means any AllUsers ACL grants are silently
            # ignored by S3, so the bucket is NOT publicly accessible via ACL.
            bpa_ignores_acls = False
            try:
                bpa = s3_client.get_public_access_block(Bucket=bucket_name)
                cfg = bpa.get("PublicAccessBlockConfiguration", {})
                bpa_ignores_acls = cfg.get("IgnorePublicAcls", False)
                block_public_access = (
                    cfg.get("BlockPublicAcls", False)
                    and cfg.get("IgnorePublicAcls", False)
                    and cfg.get("BlockPublicPolicy", False)
                    and cfg.get("RestrictPublicBuckets", False)
                )
            except ClientError as bpa_err:
                code = bpa_err.response.get("Error", {}).get("Code", "")
                if code == "NoSuchPublicAccessBlockConfiguration":
                    # No BPA config set — account default applies (no block)
                    block_public_access = False
                else:
                    block_public_access = False

            # --- Step 2: ACL check (only meaningful when BPA does not override) ---
            is_public = False
            if not bpa_ignores_acls:
                try:
                    acl_response = s3_client.get_bucket_acl(Bucket=bucket_name)
                    for grant in acl_response.get("Grants", []):
                        grantee = grant.get("Grantee", {})
                        if grantee.get("URI") == ALL_USERS_URI:
                            is_public = True
                            break
                except Exception:
                    is_public = False

            buckets.append(
                {
                    "name": bucket_name,
                    "created": created,
                    "is_public": is_public,
                    "block_public_access": block_public_access,
                }
            )

        return {
            "status": "ok",
            "count": len(buckets),
            "buckets": buckets,
        }

    except NoCredentialsError:
        return {
            "status": "error",
            "error": "AWS credentials not found. Run 'aws configure' to set them up.",
            "count": 0,
            "buckets": [],
        }
    except ClientError as error:
        return {
            "status": "error",
            "error": f"AWS API error: {error.response.get('Error', {}).get('Message', str(error))}",
            "count": 0,
            "buckets": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "error": f"Unexpected error: {str(error)}",
            "count": 0,
            "buckets": [],
        }


def _fetch_user_details(iam_client, user: dict) -> dict:
    """Fetch all per-user IAM detail calls for a single user. boto3 clients are thread-safe, so sharing one client across threads is correct."""
    username = user.get("UserName", "")

    has_mfa = False
    try:
        mfa_response = iam_client.list_mfa_devices(UserName=username)
        has_mfa = len(mfa_response.get("MFADevices", [])) > 0
    except Exception:
        pass

    attached_policies = []
    try:
        pol = iam_client.list_attached_user_policies(UserName=username)
        attached_policies = [
            {"name": p["PolicyName"], "arn": p["PolicyArn"]}
            for p in pol.get("AttachedPolicies", [])
        ]
    except Exception:
        pass

    inline_policies = []
    try:
        inline = iam_client.list_user_policies(UserName=username)
        inline_policies = inline.get("PolicyNames", [])
    except Exception:
        pass

    groups = []
    try:
        grp = iam_client.list_groups_for_user(UserName=username)
        groups = [g["GroupName"] for g in grp.get("Groups", [])]
    except Exception:
        pass

    access_keys = []
    try:
        keys = iam_client.list_access_keys(UserName=username)
        for k in keys.get("AccessKeyMetadata", []):
            key_id = k["AccessKeyId"]
            last_used = "Never"
            try:
                lu = iam_client.get_access_key_last_used(AccessKeyId=key_id)
                lu_date = lu.get("AccessKeyLastUsed", {}).get("LastUsedDate")
                last_used = str(lu_date) if lu_date else "Never"
            except Exception:
                pass
            access_keys.append({
                "key_id": key_id[:12] + "...",
                "status": k["Status"],
                "last_used": last_used,
            })
    except Exception:
        pass

    last_login_raw = user.get("PasswordLastUsed")
    created_raw = user.get("CreateDate")

    return {
        "username": username,
        "user_id": user.get("UserId", ""),
        "created": str(created_raw) if created_raw else "Unknown",
        "has_mfa": has_mfa,
        "last_login": str(last_login_raw) if last_login_raw else "Never",
        "attached_policies": attached_policies,
        "inline_policies": inline_policies,
        "groups": groups,
        "access_keys": access_keys,
    }


def scan_iam(credentials: dict = None) -> dict:
    """Scan all IAM users, fetching MFA status and login history in parallel via ThreadPoolExecutor."""

    try:
        iam_client = (
            get_aws_session(credentials).client("iam")
            if credentials
            else boto3.client("iam")
        )

        paginator = iam_client.get_paginator("list_users")
        all_users = [u for page in paginator.paginate() for u in page["Users"]]

        fetch = partial(_fetch_user_details, iam_client)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            users = list(executor.map(fetch, all_users))

        return {
            "status": "ok",
            "user_count": len(users),
            "users": users,
        }

    except NoCredentialsError:
        return {
            "status": "error",
            "error": "AWS credentials not found. Run 'aws configure' to set them up.",
            "user_count": 0,
            "users": [],
        }
    except ClientError as error:
        return {
            "status": "error",
            "error": f"AWS API error: {error.response.get('Error', {}).get('Message', str(error))}",
            "user_count": 0,
            "users": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "error": f"Unexpected error: {str(error)}",
            "user_count": 0,
            "users": [],
        }


def scan_security_groups(region: str = "us-east-1", credentials: dict = None) -> dict:
    """Scan all EC2 security groups and flag any with inbound rules open to 0.0.0.0/0 or ::/0."""

    try:
        ec2_client = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
        )

        paginator = ec2_client.get_paginator("describe_security_groups")
        all_sgs = [sg for page in paginator.paginate() for sg in page["SecurityGroups"]]

        security_groups = []

        for sg in all_sgs:
            open_to_internet = []

            for rule in sg.get("IpPermissions", []):
                port = rule.get("FromPort", "all")
                protocol = rule.get("IpProtocol", "unknown")

                for ip_range in rule.get("IpRanges", []):
                    if ip_range.get("CidrIp") == "0.0.0.0/0":
                        open_to_internet.append(
                            {
                                "port": port,
                                "protocol": protocol,
                            }
                        )

                for ipv6_range in rule.get("Ipv6Ranges", []):
                    if ipv6_range.get("CidrIpv6") == "::/0":
                        already_added = any(
                            entry["port"] == port and entry["protocol"] == protocol
                            for entry in open_to_internet
                        )
                        if not already_added:
                            open_to_internet.append(
                                {
                                    "port": port,
                                    "protocol": protocol,
                                }
                            )
                        break

            security_groups.append(
                {
                    "id": sg.get("GroupId", ""),
                    "name": sg.get("GroupName", ""),
                    "description": sg.get("Description", ""),
                    "vpc_id": sg.get("VpcId"),
                    "open_to_internet": open_to_internet,
                    "is_dangerous": len(open_to_internet) > 0,
                }
            )

        return {
            "status": "ok",
            "count": len(security_groups),
            "security_groups": security_groups,
        }

    except NoCredentialsError:
        return {
            "status": "error",
            "error": "AWS credentials not found. Run 'aws configure' to set them up.",
            "count": 0,
            "security_groups": [],
        }
    except ClientError as error:
        return {
            "status": "error",
            "error": f"AWS API error: {error.response.get('Error', {}).get('Message', str(error))}",
            "count": 0,
            "security_groups": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "error": f"Unexpected error: {str(error)}",
            "count": 0,
            "security_groups": [],
        }


def scan_vpc(region: str = "us-east-1", credentials: dict = None) -> dict:
    """Scan all VPCs and count subnets per VPC."""

    try:
        ec2_client = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
        )

        vpc_paginator = ec2_client.get_paginator("describe_vpcs")
        all_vpcs = [v for page in vpc_paginator.paginate() for v in page["Vpcs"]]

        subnet_paginator = ec2_client.get_paginator("describe_subnets")
        subnet_counts: defaultdict = defaultdict(int) # counts subnets per vpc!
        for page in subnet_paginator.paginate():
            for sn in page["Subnets"]:
                subnet_counts[sn["VpcId"]] += 1

        vpcs = []

        for vpc in all_vpcs:
            vpc_id = vpc.get("VpcId", "")

            name = ""
            for tag in vpc.get("Tags", []):
                if tag.get("Key") == "Name":
                    name = tag.get("Value", "")
                    break

            subnet_count = subnet_counts[vpc_id]

            vpcs.append(
                {
                    "id": vpc_id,
                    "name": name,
                    "cidr": vpc.get("CidrBlock", ""),
                    "is_default": vpc.get("IsDefault", False),
                    "state": vpc.get("State", ""),
                    "subnet_count": subnet_count,
                }
            )

        return {
            "status": "ok",
            "count": len(vpcs),
            "vpcs": vpcs,
        }

    except NoCredentialsError:
        return {
            "status": "error",
            "error": "AWS credentials not found. Run 'aws configure' to set them up.",
            "count": 0,
            "vpcs": [],
        }
    except ClientError as error:
        return {
            "status": "error",
            "error": f"AWS API error: {error.response.get('Error', {}).get('Message', str(error))}",
            "count": 0,
            "vpcs": [],
        }
    except Exception as error:
        return {
            "status": "error",
            "error": f"Unexpected error: {str(error)}",
            "count": 0,
            "vpcs": [],
        }


def scan_existing_infra_for_context(region: str = "us-east-1", credentials: dict = None) -> str:
    """Scan existing AWS resources and return a human-readable string of resource IDs for LLM context. Silently skips any service that fails."""
    lines = [f"EXISTING AWS INFRASTRUCTURE (region: {region}):"]
    lines.append("Reference these IDs directly in HCL — do NOT create duplicates.\n")

    try:
        ec2 = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
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
            get_aws_session(credentials).client("iam")
            if credentials
            else boto3.client("iam")
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
            get_aws_session(credentials).client("s3")
            if credentials
            else boto3.client("s3")
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


def scan_sg_usage(region: str = "us-east-1", credentials: dict = None) -> dict:
    """Build a complete map of security group → attached resources using ENIs, which covers all AWS resource types."""
    try:
        ec2 = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
        )

        # Seed the map with every SG so unused ones appear as empty lists
        sg_paginator = ec2.get_paginator("describe_security_groups")
        all_sgs = [sg for page in sg_paginator.paginate() for sg in page["SecurityGroups"]]
        sg_usage = {sg["GroupId"]: [] for sg in all_sgs}

        # Walk every ENI — one ENI (elastic network interface) can reference multiple SGs
        eni_paginator = ec2.get_paginator("describe_network_interfaces")
        for page in eni_paginator.paginate():
            for eni in page["NetworkInterfaces"]:
                eni_id     = eni["NetworkInterfaceId"]
                desc       = eni.get("Description", "")
                attachment = eni.get("Attachment", {})
                instance_id = attachment.get("InstanceId", "")

                # Classify resource type from ENI description / interface type
                if instance_id:
                    resource       = instance_id
                    resource_type  = "EC2"
                elif desc.startswith("ELB"):
                    resource       = desc
                    resource_type  = "ELB"
                elif "RDS" in desc or "rds" in desc.lower():
                    resource       = desc or eni_id
                    resource_type  = "RDS"
                elif "Lambda" in desc or "lambda" in desc.lower():
                    resource       = desc or eni_id
                    resource_type  = "Lambda"
                elif "ECS" in desc or "ecs" in desc.lower():
                    resource       = desc or eni_id
                    resource_type  = "ECS"
                else:
                    resource       = desc or eni_id
                    resource_type  = "ENI"

                for group in eni.get("Groups", []):
                    sg_id = group["GroupId"]
                    if sg_id in sg_usage:
                        sg_usage[sg_id].append({
                            "resource":      resource,
                            "resource_type": resource_type,
                            "eni_id":        eni_id,
                        })

        unused_ids = [sg_id for sg_id, usages in sg_usage.items() if not usages]    # security grps attached to no resources

        return {
            "status":        "ok",
            "total_count":   len(sg_usage),
            "unused_count":  len(unused_ids),
            "unused_sg_ids": unused_ids,
            "sg_usage":      sg_usage,
        }

    except ClientError as e:
        return {"status": "error", "error": str(e), "sg_usage": {}, "unused_sg_ids": [], "unused_count": 0, "total_count": 0}
    except Exception as e:
        return {"status": "error", "error": str(e), "sg_usage": {}, "unused_sg_ids": [], "unused_count": 0, "total_count": 0}


def revoke_sg_ingress_rule(
    sg_id: str,
    port: int,
    region: str = "us-east-1",
    credentials: dict = None,
) -> dict:
    """Revoke all 0.0.0.0/0 and ::/0 ingress rules for a port on a security group. Calls the AWS SDK directly since Terraform cannot remove individual rules from unmanaged groups."""
    try:
        ec2 = (
            get_aws_session(credentials).client("ec2", region_name=region)
            if credentials
            else boto3.client("ec2", region_name=region)
        )

        response = ec2.describe_security_groups(GroupIds=[sg_id])
        sgs = response.get("SecurityGroups", [])
        if not sgs:
            return {"success": False, "revoked": 0, "message": f"Security group {sg_id} not found."}

        rules_to_revoke = []
        for perm in sgs[0].get("IpPermissions", []):
            from_port = perm.get("FromPort", -1)
            to_port   = perm.get("ToPort",   -1)

            # Match rules that cover the target port (including all-protocol rules)
            proto = perm.get("IpProtocol", "")
            port_match = (
                proto == "-1"   # all protocol rule, covers all ports
                or (isinstance(from_port, int) and isinstance(to_port, int) and from_port <= port <= to_port)   # explicit port range, covers tartget port
            )
            if not port_match:
                continue

            open_v4 = [r for r in perm.get("IpRanges",   []) if r.get("CidrIp")   == "0.0.0.0/0"]   # open internet CIDR entries
            open_v6 = [r for r in perm.get("Ipv6Ranges", []) if r.get("CidrIpv6") == "::/0"]

            if open_v4 or open_v6:
                revoke_perm = {"IpProtocol": proto}
                if proto != "-1":
                    revoke_perm["FromPort"] = from_port
                    revoke_perm["ToPort"]   = to_port
                if open_v4:
                    revoke_perm["IpRanges"]   = open_v4
                if open_v6:
                    revoke_perm["Ipv6Ranges"] = open_v6
                rules_to_revoke.append(revoke_perm)

        if not rules_to_revoke:
            return {"success": True, "revoked": 0, "message": "No open 0.0.0.0/0 rules found for this port."}

        ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=rules_to_revoke)
        return {
            "success": True,
            "revoked": len(rules_to_revoke),
            "message": f"Revoked {len(rules_to_revoke)} open rule(s) on port {port} for {sg_id}.",
        }

    except ClientError as e:
        return {"success": False, "revoked": 0, "message": str(e)}
    except Exception as e:
        return {"success": False, "revoked": 0, "message": str(e)}
