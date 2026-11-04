export const CHART_COLORS = {
  blue:    '#0070f3',
  success: '#50e3c2',
  warning: '#f5a623',
  error:   '#ff4444',
  muted:   '#555',
}

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
