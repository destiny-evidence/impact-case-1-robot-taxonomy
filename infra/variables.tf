variable "app_name" {
  description = "Name for the shared resources (resource group, identity, container app environment) and the ACR image repository. Container apps are named separately."
  type        = string
  default     = "destiny-taxonomy-robot"
}

variable "environment" {
  description = "The environment this stack is being deployed to. Also passed to the app as ENV."
  type        = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Allowed values for environment are \"development\", \"staging\", or \"production\"."
  }
}

variable "region" {
  description = "The Azure region resources will be deployed into"
  type        = string
  default     = "swedencentral"
}

variable "budget_code" {
  description = "Budget code for tagging resource groups. Required tag for resource groups"
  type        = string
}

variable "created_by" {
  description = "Creator of this infrastructure"
  type        = string
}

variable "owner" {
  description = "Owner email for this infrastructure"
  type        = string
}

variable "project" {
  description = "Project name for tagging"
  type        = string
  default     = "DESTINY"
}

variable "robot_id" {
  description = "Client id the robot uses with the DESTINY repository"
  type        = string
}

variable "robot_secret" {
  description = "HMAC secret the robot uses with the DESTINY repository."
  type        = string
  sensitive   = true
}

variable "memory" {
  type    = string
  default = "2Gi"
}

variable "replicas" {
  type    = number
  default = 1
}

variable "interval_seconds" {
  type    = number
  default = 30
}

variable "batch_size" {
  type    = number
  default = 500
}

variable "concurrent_batches" {
  type    = number
  default = 1
}

variable "batch_lease_seconds" {
  description = "How long the repository leases a polled batch to the robot. Must outlast the time it takes to annotate a batch, or the repository redelivers it. Null uses the repository's own default."
  type        = number
  default     = null
}

variable "extra_env" {
  description = "Environment variables applied to the robot."
  type        = map(string)
  default     = {}
}

# DESTINY repository
variable "destiny_repository_url" {
  description = "DESTINY repository API endpoint the robots poll"
  type        = string
  default     = "https://api.staging.evidence-repository.org"
}

# LLM
variable "llm_max_concurrent_extractions" {
  description = "Maximum LLM requests in flight per container. Divide by replica count if the robot is scaled out."
  type        = number
  default     = 100
}

variable "llm_requests_per_minute" {
  description = "LLM requests per minute per container, against the Foundry deployment quota. Divide by replica count if the robot is scaled out."
  type        = number
  default     = 1200
}

variable "llm_tokens_per_minute" {
  description = "LLM tokens per minute per container, against the Foundry deployment quota. Divide by replica count if the robot is scaled out."
  type        = number
  default     = 1200000
}

variable "llm_azure_api_base" {
  description = "Base URL for Azure OpenAI"
  type        = string
}

variable "llm_azure_api_key" {
  description = "API key for Azure OpenAI"
  type        = string
  sensitive   = true
}

# Vocabulary
variable "vocabulary_uid" {
  description = "Project UID of the published vocabulary in the Vocabulary Builder"
  type        = string
}
variable "vocabulary_version" {
  description = "Published vocabulary version"
  type        = string
}

variable "otel_enabled" {
  description = "Whether to export traces to Honeycomb."
  type        = bool
  default     = true
}

variable "honeycomb_api_key" {
  description = "Honeycomb ingest key (x-honeycomb-team). Empty disables export."
  type        = string
  default     = ""
  sensitive   = true
}

variable "honeycomb_trace_endpoint" {
  description = "Honeycomb OTLP/HTTP traces endpoint."
  type        = string
  default     = "https://api.honeycomb.io/v1/traces"
}

# Container Registry (shared)
variable "shared_container_registry_name" {
  description = "The name of the shared container registry"
  type        = string
}

variable "shared_resource_group_name" {
  description = "The resource group containing the shared container registry"
  type        = string
}

# GitHub Actions
variable "github_repo" {
  description = "GitHub repository for Actions OIDC"
  type        = string
  default     = "destiny-evidence/impact-case-1-robot-taxonomy"
}

variable "github_owner_id" {
  description = "Immutable numeric ID of the GitHub organisation."
  type        = string
}

variable "github_app_id" {
  description = "GitHub App ID for configuring repository environments"
  type        = string
}

variable "github_app_installation_id" {
  description = "GitHub App installation ID"
  type        = string
}

variable "github_app_pem" {
  description = "GitHub App private key PEM file contents"
  type        = string
  sensitive   = true
}
