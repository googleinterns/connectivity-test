# proto-file: proto/derivation_rules.proto
# proto-message: RuleSet

rules {
  filters {
    route_type: DYNAMIC
  }
  destinations: {
    destination: VPC_PEERS_CUSTOM_ROUTING

    route_template {
      route_type: PEERING_DYNAMIC
    }
  }
}
