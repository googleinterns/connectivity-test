# proto-file: proto/derivation_rules.proto
# proto-message: RuleSet

rules {
  filters {
    route_type: DYNAMIC
    from_local: true
  }
  destinations: {
    destination: REGIONS_OF_VPC_PEERS_CUSTOM_ROUTING

    route_template {
      route_type: PEERING_DYNAMIC
    }
  }

  destinations: {
    destination: OTHER_REGIONS_WHEN_GLOBAL_ROUTING
  }
}

rules {
  filters {
    route_type: DYNAMIC
    from_local: false
  }
}
