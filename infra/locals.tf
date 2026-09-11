locals {
  # Container apps are capped at 32 characters; the managed environment allows 60.
  shortname = substr(var.environment, 0, 4)

  name           = "${var.app_name}-${var.environment}"
  robot_app_name = "${var.app_name}-${local.shortname}-app"

  # Non-overlapping VNets per per environment
  # /27 is the minimum for a workload profile environment
  network = {
    staging     = { vnet = "10.20.0.0/24", app_subnet = "10.20.0.0/27" }
    production  = { vnet = "10.21.0.0/24", app_subnet = "10.21.0.0/27" }
    development = { vnet = "10.22.0.0/24", app_subnet = "10.22.0.0/27" }
  }

  vnet_address_space        = local.network[var.environment].vnet
  app_subnet_address_prefix = local.network[var.environment].app_subnet

  minimum_resource_tags = {
    "Created by"  = var.created_by
    "Environment" = var.environment
    "Owner"       = var.owner
    "Project"     = var.project
    "Region"      = var.region
  }
}
