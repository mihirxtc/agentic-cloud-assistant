export const CHART_COLORS = {
  blue:    '#0070f3',
  success: '#50e3c2',
  warning: '#f5a623',
  error:   '#ff4444',
  muted:   '#555',
}

export const RULE_WEIGHTS = {
  SSH_PORT_OPEN:            3,
  RDP_PORT_OPEN:            3,
  S3_BUCKET_PUBLIC:         3,
  UNRESTRICTED_ALL_TRAFFIC: 3,
  IAM_USER_NO_MFA:          2,
  IAM_USER_INACTIVE:        1,
  DEFAULT_VPC_IN_USE:       1,
}

export const RULE_TOTAL = 16

export const QUICK_REQUESTS = [
  'EC2 t3.micro + security group',
  'S3 bucket with versioning',
  'VPC with public + private subnets',
  'IAM role for EC2 with S3 read',
]

export const RESOURCE_TYPES = ['All', 'EC2', 'S3', 'IAM', 'VPC', 'Terraform', 'General']

export const AGENT_STEPS = [
  'Scanning AWS infrastructure…',
  'Classifying security findings…',
  'Generating Terraform fix…',
  'Validating HCL syntax…',
  'Running terraform plan…',
  'Summarising changes for review…',
]

export const TWEAK_DEFAULTS = {
  accent:      '#0070f3',
  panelRadius: 8,
  density:     'regular',
  monoFont:    'JetBrains Mono',
  showScore:   true,
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
