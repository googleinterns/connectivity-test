# proto-file: proto/derivation_rules.proto
# proto-message: RuleSet

rules {
  filters {
    route_type: SUBNET
  }

  destinations: {
    destination: VPC_PEERS

    route_template {
      route_type: PEERING_SUBNET
    }
  }

  destinations: {
    destination: BGP_PEERS

    route_template {
      route_type: DYNAMIC
    }
  }
}
