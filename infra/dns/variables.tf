variable "region" {
  description = "AWS region. Must match the app stack."
  type        = string
  default     = "ap-south-1" # Mumbai
}

variable "project" {
  description = "Project name, used as a resource name prefix. Must match the app stack."
  type        = string
  default     = "frikkinwave"
}

variable "environment" {
  description = "Environment name, used as a resource name prefix. Must match the app stack."
  type        = string
  default     = "prod"
}

variable "api_domain" {
  description = "Public hostname for the backend API."
  type        = string
  default     = "api.frikkinwave.com"
}
