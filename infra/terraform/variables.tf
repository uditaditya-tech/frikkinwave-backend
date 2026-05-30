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

variable "database_url" {
  description = "DATABASE_URL for the container. Placeholder until RDS lands in 1.10 (health check needs no DB)."
  type        = string
  default     = "postgres://placeholder:placeholder@localhost:5432/placeholder"
}

variable "cors_allowed_origins" {
  description = "Comma-separated CORS origins for the container."
  type        = string
  default     = "https://frikkinwave.com"
}
