output "identity_client_id" {
  description = "Client ID of the robots' managed identity, for granting it DESTINY repository roles"
  value       = azurerm_user_assigned_identity.app.client_id
}

output "identity_principal_id" {
  description = "Object ID of the robots' managed identity"
  value       = azurerm_user_assigned_identity.app.principal_id
}

output "container_app_name" {
  value = azurerm_container_app.robot.name
}

output "egress_ip_address" {
  description = "Static public IP all outbound robot traffic leaves from. Add this to any upstream IP allowlist."
  value       = azurerm_public_ip.nat.ip_address
}
