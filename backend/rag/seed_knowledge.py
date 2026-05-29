import logging
import sys
import os

# Allow running as: python -m rag.seed_knowledge from the backend/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from rag.knowledge_base import knowledge_base

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

SEED_DOCUMENTS = [
    {
        "doc_id": "aws-s3-security",
        "metadata": {
            "source": "aws-best-practices",
            "resource_type": "s3",
            "severity": "critical",
        },
        "text": """S3 Bucket Security Best Practices

Public Access Controls:
Always enable S3 Block Public Access at the account level and per-bucket
level. This prevents buckets from being accidentally made public. Navigate
to S3 console, select your bucket, go to Permissions, and enable all four
Block Public Access settings. Public buckets expose all objects to the
internet without authentication.

Encryption:
Enable server-side encryption (SSE) on all S3 buckets. Use SSE-S3 for
basic encryption or SSE-KMS for fine-grained access control via AWS KMS.
Enforce encryption in transit by using bucket policies that deny requests
where aws:SecureTransport is false.

Access Control:
Prefer bucket policies over ACLs. ACLs are a legacy feature and can be
confusing to reason about. Bucket policies use the same IAM syntax as
other AWS policies, making them easier to audit. Use S3 Access Points
for granular, per-application access control on shared buckets.

Versioning and Lifecycle:
Enable versioning on critical data buckets to protect against accidental
deletion and ransomware. Pair versioning with lifecycle rules to manage
storage costs. Enable MFA Delete on buckets containing sensitive or
compliance-critical data.

Logging and Monitoring:
Enable S3 Server Access Logging to track requests to your bucket. Enable
AWS CloudTrail data events for S3 to record GetObject, PutObject, and
DeleteObject calls. Set up CloudWatch alarms for unusual access patterns.

CIS AWS Foundations Benchmark S3 Controls:
CIS 2.1.1: Ensure S3 bucket policy is set to deny HTTP requests.
CIS 2.1.2: Ensure MFA Delete is enabled on S3 buckets.
CIS 2.1.5: Ensure S3 buckets are configured with Block Public Access.""",
    },
    {
        "doc_id": "aws-ec2-security",
        "metadata": {
            "source": "aws-best-practices",
            "resource_type": "ec2",
            "severity": "high",
        },
        "text": """EC2 Instance Security Best Practices

Security Groups:
Security groups act as virtual firewalls. Never use 0.0.0.0/0 (all IPv4)
or ::/0 (all IPv6) as an ingress source unless serving public web traffic
on port 80 or 443. Restrict SSH (port 22) and RDP (port 3389) to specific
known IP addresses only — ideally your office IP range or a VPN endpoint.
Review security group rules quarterly and remove unused rules.

Instance Access:
Use AWS Systems Manager Session Manager instead of SSH for shell access
where possible. Session Manager requires no open inbound ports, logs all
sessions to CloudTrail, and integrates with IAM for access control. If SSH
is required, use EC2 Instance Connect rather than maintaining static key
pairs. Never embed AWS credentials in instance user data.

IAM Instance Profiles:
Attach an IAM instance profile (role) to every EC2 instance that needs
AWS API access. Never put AWS access keys on an instance directly. Use
the principle of least privilege — grant only the specific API actions
the instance needs, scoped to specific resources where possible.

Storage:
Encrypt all EBS volumes. For new volumes, encryption is now enabled by
default in most regions, but verify this in your account settings. Enable
EBS snapshot encryption to ensure backups are also protected.

CIS AWS Foundations Benchmark EC2 Controls:
CIS 5.1: Ensure no security groups allow ingress from 0.0.0.0/0 to port 22.
CIS 5.2: Ensure no security groups allow ingress from 0.0.0.0/0 to port 3389.
CIS 5.4: Ensure the default security group of every VPC restricts all traffic.""",
    },
    {
        "doc_id": "aws-iam-security",
        "metadata": {
            "source": "aws-best-practices",
            "resource_type": "iam",
            "severity": "critical",
        },
        "text": """IAM Security Best Practices

Root Account:
Never use the root account for day-to-day tasks. The root account has
unrestricted access to all AWS services and cannot be limited by IAM
policies. Enable MFA on the root account immediately after account
creation. Delete or do not create root access keys.

MFA:
Enable MFA for all IAM users, especially those with console access or
administrative permissions. Use hardware MFA tokens for privileged
accounts. Enforce MFA using an IAM policy condition:
aws:MultiFactorAuthPresent: true.

Least Privilege:
Grant only the permissions required to perform a task. Start with AWS
managed policies for standard roles, then scope them down using condition
keys. Avoid wildcard actions and wildcard resources in policy statements.
Review and remove unused permissions quarterly using IAM Access Analyzer.

Access Keys:
Rotate IAM access keys every 90 days. Delete access keys for inactive
users. Never commit access keys to source code repositories. Use IAM
roles for applications running on AWS services rather than access keys.

CIS AWS Foundations Benchmark IAM Controls:
CIS 1.4: Ensure no root account access key exists.
CIS 1.5: Ensure MFA is enabled for the root account.
CIS 1.10: Ensure MFA is enabled for all IAM users with console access.
CIS 1.14: Ensure access keys are rotated every 90 days.
CIS 1.16: Ensure IAM policies are attached only to groups or roles.""",
    },
    {
        "doc_id": "aws-vpc-security",
        "metadata": {
            "source": "aws-best-practices",
            "resource_type": "vpc",
            "severity": "medium",
        },
        "text": """VPC Network Security Best Practices

Flow Logs:
Enable VPC Flow Logs on all VPCs to capture information about IP traffic.
Flow logs help detect anomalous traffic patterns, troubleshoot
connectivity, and support incident response. Send logs to CloudWatch Logs
or S3. CIS 3.9: Ensure VPC Flow Logging is enabled in all VPCs.

Network ACLs vs Security Groups:
Use both network ACLs (subnet-level, stateless) and security groups
(instance-level, stateful) for defence in depth. Network ACLs are
evaluated before security groups and can deny traffic before it reaches
an instance.

Default VPC:
Do not use the default VPC for production workloads. Create purpose-built
VPCs with intentional subnet design (public, private, data tiers). The
default security group in every VPC should restrict all inbound traffic.

Subnets:
Place internet-facing resources in public subnets. Place application
servers and databases in private subnets with no direct internet route.
Use NAT Gateway for outbound internet access from private subnets. Never
place a database in a public subnet.

VPC Endpoints:
Use VPC endpoints for AWS services (S3, DynamoDB, STS) to keep traffic
within the AWS network and avoid NAT gateway costs.""",
    },
    {
        "doc_id": "terraform-security-patterns",
        "metadata": {
            "source": "terraform-best-practices",
            "resource_type": "terraform",
            "severity": "high",
        },
        "text": """Terraform Security Configuration Patterns

S3 Bucket with Encryption and Block Public Access:
resource "aws_s3_bucket" "secure_bucket" {
  bucket = "my-secure-bucket"
}
resource "aws_s3_bucket_public_access_block" "block" {
  bucket = aws_s3_bucket.secure_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "encrypt" {
  bucket = aws_s3_bucket.secure_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

Secure Security Group restricting SSH:
resource "aws_security_group" "web" {
  name   = "web-sg"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS from internet"
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_cidr]
    description = "SSH from admin IP only"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

IAM Role with Least Privilege:
resource "aws_iam_role" "app_role" {
  name = "app-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}""",
    },
]

def seed_all() -> None:
    logger.info("=" * 60)
    logger.info("Seeding AWS security knowledge base...")
    logger.info("=" * 60)

    total_chunks = 0
    for doc in SEED_DOCUMENTS:
        result = knowledge_base.add_document(
            doc_id=doc["doc_id"],
            text=doc["text"],
            metadata=doc["metadata"],
        )
        total_chunks += result["chunks_added"]
        logger.info(
            f"  [ok] {doc['doc_id']:35s} — {result['chunks_added']} chunk(s)"
        )

    logger.info("-" * 60)
    logger.info(
        f"Seeding complete. {len(SEED_DOCUMENTS)} documents, "
        f"{total_chunks} chunks total in ChromaDB."
    )
    logger.info("=" * 60)

    logger.info("\nSpot-check search results:")

    q1 = "How do I fix an S3 bucket that is publicly accessible?"
    r1 = knowledge_base.search(q1, n_results=1)
    logger.info(f"\n  Query : {q1}")
    if r1:
        logger.info(f"  Score : {r1[0]['relevance_score']}")
        logger.info(f"  Source: {r1[0]['metadata'].get('doc_id')} "
                    f"(chunk {r1[0]['metadata'].get('chunk_index')})")
        logger.info(f"  Text  : {r1[0]['text'][:120]}...")

    q2 = "What CIS control covers SSH port open to the internet?"
    r2 = knowledge_base.search(q2, n_results=1)
    logger.info(f"\n  Query : {q2}")
    if r2:
        logger.info(f"  Score : {r2[0]['relevance_score']}")
        logger.info(f"  Source: {r2[0]['metadata'].get('doc_id')} "
                    f"(chunk {r2[0]['metadata'].get('chunk_index')})")
        logger.info(f"  Text  : {r2[0]['text'][:120]}...")

    logger.info("")


if __name__ == "__main__":
    seed_all()
