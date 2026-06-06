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
  description = "Number of web Fargate tasks to run."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Number of Celery worker Fargate tasks to run."
  type        = number
  default     = 1
}

# --- OpenAI (Phase 2 AI) --------------------------------------------------

variable "openai_api_key" {
  description = "OPENAI_API_KEY for embeddings/blurbs/coach. Set in terraform.tfvars (git-ignored). Empty disables AI calls (features degrade gracefully)."
  type        = string
  sensitive   = true
  default     = ""
}

# --- ElastiCache (Redis / Celery broker) ----------------------------------

variable "redis_node_type" {
  description = "ElastiCache node type. cache.t4g.micro is the cheapest Graviton option."
  type        = string
  default     = "cache.t4g.micro"
}

# --- Application runtime config -------------------------------------------
# NOTE: plain env vars for 1.9. Moves to SSM/Secrets Manager in 1.10.

variable "django_secret_key" {
  description = "DJANGO_SECRET_KEY for the running container. Set in terraform.tfvars (git-ignored)."
  type        = string
  sensitive   = true
}

variable "cors_allowed_origins" {
  description = "Comma-separated CORS origins for the container (the Vercel frontend)."
  type        = string
  default     = "https://frikkinwave.com,https://www.frikkinwave.com"
}

variable "api_domain" {
  description = "Public hostname for the backend API."
  type        = string
  default     = "api.frikkinwave.com"
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
  description = "Skip the final snapshot on destroy. Default false = take a final snapshot for data retention; set true to wipe cleanly."
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Block terraform destroy until disabled. false = free to cycle."
  type        = bool
  default     = false
}

variable "db_snapshot_identifier" {
  description = "Restore RDS from this snapshot on create (manual or final-on-destroy snapshot id). Empty = fresh empty DB. Only consulted when the instance is first created; ignored on in-place updates."
  type        = string
  default     = ""
}
