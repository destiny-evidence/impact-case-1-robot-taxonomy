data "azurerm_subscription" "current" {}

data "azuread_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.region
  tags     = merge({ "Budget Code" = var.budget_code }, local.minimum_resource_tags)
}

data "azurerm_container_registry" "this" {
  name                = var.shared_container_registry_name
  resource_group_name = var.shared_resource_group_name
}

resource "azurerm_user_assigned_identity" "app" {
  name                = local.name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.minimum_resource_tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  scope                = data.azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = local.name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.minimum_resource_tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = local.name
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = local.minimum_resource_tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

locals {
  shared_env = {
    ENV                            = var.environment
    BASE_URL                       = var.destiny_repository_url
    AZURE_API_BASE                 = var.llm_azure_api_base
    LLM_MAX_CONCURRENT_EXTRACTIONS = var.llm_max_concurrent_extractions
    LLM_REQUESTS_PER_MINUTE        = var.llm_requests_per_minute
    LLM_TOKENS_PER_MINUTE          = var.llm_tokens_per_minute
    VOCABULARY_UID                 = var.vocabulary_uid
    VOCABULARY_VERSION             = var.vocabulary_version
    OTEL_ENABLED                   = var.otel_enabled
  }
}

resource "azurerm_container_app" "robot" {
  name                         = local.robot_app_name
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.minimum_resource_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = data.azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name  = "robot-secret"
    value = var.robot_secret
  }

  secret {
    name  = "llm-azure-api-key"
    value = var.llm_azure_api_key
  }

  secret {
    name = "otel-config"
    value = jsonencode({
      trace_endpoint = var.honeycomb_trace_endpoint
      api_key        = var.honeycomb_api_key
    })
  }

  template {
    min_replicas = var.replicas
    max_replicas = var.replicas

    container {
      name = "taxonomy"

      # Placeholder only. The deploy workflow owns the image from then on, and
      # the lifecycle block below stops Terraform reverting it.
      image   = "mcr.microsoft.com/k8se/quickstart:latest"
      memory  = var.memory
      cpu     = tonumber(trimsuffix(var.memory, "Gi")) / 2
      command = ["robot"]

      dynamic "env" {
        for_each = merge(
          local.shared_env,
          {
            ROBOT_ID           = var.robot_id
            INTERVAL_SECONDS   = var.interval_seconds
            BATCH_SIZE         = var.batch_size
            CONCURRENT_BATCHES = var.concurrent_batches
          },
          var.extra_env,
        )
        content {
          name  = env.key
          value = tostring(env.value)
        }
      }

      env {
        name        = "ROBOT_SECRET"
        secret_name = "robot-secret"
      }

      env {
        name        = "AZURE_API_KEY"
        secret_name = "llm-azure-api-key"
      }

      env {
        name        = "OTEL_CONFIG"
        secret_name = "otel-config"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
