# ---------------------------------------------------------------------------
# VPC — public subnets only, no NAT gateway.
#
# Same cost trade the ECS stack made: a NAT gateway is ~$32/mo, so nodes sit in
# public subnets with public IPs. Security comes from security groups, not from
# private addressing. For a production system handling real user data you would
# add private subnets + NAT (or VPC endpoints) and move the nodes there.
#
# The kubernetes.io/* subnet tags are load-bearing: without them the AWS Load
# Balancer Controller cannot discover where to place an ALB, and it fails with
# no useful error. This is the single most common EKS setup mistake.
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true # required for EKS private endpoint resolution
  tags                 = merge(local.tags, { Name = "${local.name}-vpc" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${local.name}-igw" })
}

resource "aws_subnet" "public" {
  # Three AZs, not the two EKS requires as a minimum.
  #
  # Learned the hard way: with two subnets the node group could not balance,
  # because ap-south-1a had no t4g.small capacity at all
  # (InsufficientInstanceCapacity, retried every 4 min for half an hour). Both
  # nodes ended up in 1b and the cluster had zero AZ redundancy while looking
  # multi-AZ. A third AZ gives the ASG somewhere else to go when one is full.
  count                   = 3
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true # nodes need public IPs to reach the EKS API / ECR

  tags = merge(local.tags, {
    Name = "${local.name}-public-${count.index}"
    # Tells the load balancer controller this subnet may host internet-facing ALBs.
    "kubernetes.io/role/elb" = "1"
    # Marks the subnet as usable by this cluster.
    "kubernetes.io/cluster/${local.name}" = "shared"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.tags, { Name = "${local.name}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
