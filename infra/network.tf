resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [local.vnet_address_space]
  tags                = local.minimum_resource_tags
}

# Dedicated to the Container Apps environment.
resource "azurerm_subnet" "app" {
  name                 = "sn-${local.name}-app"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [local.app_subnet_address_prefix]

  delegation {
    name = "app"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Standard SKU: StandardV2 is not supported for Container Apps integration, and a Standard NAT
# gateway requires a Standard public IP.
resource "azurerm_public_ip" "nat" {
  name                = "pip-${local.name}-nat"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.minimum_resource_tags
}

# Moves outbound traffic off the platform's shared SNAT pool.
resource "azurerm_nat_gateway" "this" {
  name                = "nat-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = "Standard"
  tags                = local.minimum_resource_tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  nat_gateway_id       = azurerm_nat_gateway.this.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_subnet_nat_gateway_association" "app" {
  subnet_id      = azurerm_subnet.app.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}
