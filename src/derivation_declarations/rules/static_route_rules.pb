# proto-file: proto/derivation_rules.proto
# proto-message: RuleSet

rules {
  filters {
    route_type: STATIC
    next_hop_gateway: INTERNET_GATEWAY
  }
  lambda_filters: "any([attr.tag for attr in route.instance_filter.attributes])"
}

rules {
  filters {
    route_type: STATIC
  }

  destinations: {
    destination: VPC_PEERS_CUSTOM_ROUTING

    route_template {
      route_type: PEERING_STATIC
    }
  }
}