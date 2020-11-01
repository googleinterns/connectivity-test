# proto-file: proto/derivation_rules.proto
# proto-message: RuleSet

rules {
  filters {
    route_type: PEERING_SUBNET
  }
  filters {
    route_type: PEERING_STATIC
  }
  filters {
    route_type: PEERING_DYNAMIC
  }
  # The matching rules are not exported to any destinations.
}
