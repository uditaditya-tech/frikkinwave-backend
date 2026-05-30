variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1" # Mumbai
}

variable "project" {
  description = "Project name, used as a resource name prefix."
  type        = string
  default     = "frikkinwave"
}

variable "environment" {
  description = "Environment name, used as a resource name prefix."
  type        = string
  default     = "prod"
}

variable "container_port" {
  description = "Port the app listens on inside the container (gunicorn bind)."
  type        = number
  default     = 8000
}

variable "image_tag" {
  description = "Tag of the image in ECR that the task definition runs."
  type        = string
  default     = "latest"
}

variable "desired_count" {
  description = "Number of Fargate tasks to run."
  type        = number
  default     = 1
}

# --- Application runtime config -------------------------------------------
# NOTE: plain env vars for 1.9. Moves to SSM/Secrets Manager in 1.10.

variable "django_secret_key" {
  description = "DJANGO_SECRET_KEY for the running container. Set in terraform.tfvars (git-ignored)."
  type        = string
  sensitive   = true
}

variable "cors_allowed_origins" {
  description = "Comma-separated CORS origins for the container."
  type        = string
  default     = "https://frikkinwave.com"
}

# --- Database (RDS) -------------------------------------------------------

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "frikkinwave"
}

variable "db_username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "frikkinwave"
}

variable "db_engine_version" {
  description = "Postgres major version."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro is the cheapest Graviton option."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GB."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = "Automated backup retention. 0 disables backups (cheapest for dev)."
  type        = number
  default     = 1
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. true = ephemeral dev DB."
  type        = bool
  default     = true
}

variable "db_deletion_protection" {
  description = "Block terraform destroy until disabled. false = free to cycle."
  type        = bool
  default     = false
}
