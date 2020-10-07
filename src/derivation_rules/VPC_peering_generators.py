# Copyright 2020 Google LLC
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     https://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import Any

import proto.cloud_network_model_pb2 as entities
import proto.derivation_rules_pb2 as derivation_rules
import proto.rules_pb2 as rules
from src.utils.derivation_utils import clear_next_hops, getPeeringRouteType

Destination = derivation_rules.DestinationAndGeneration.Destination
DestinationContext = derivation_rules.DestinationAndGeneration.DestinationContext


def VPCPeersGeneratorCommon(derived: rules.Route, context: DestinationContext,
                            model: entities.Model) -> rules.Route:
    clear_next_hops(derived)
    derived.next_hop_peering = context.peer_info
    derived.route_type = getPeeringRouteType(derived.route_type)
    return derived

def VPCPeersCustomRoutingGenerator(derived: rules.Route, context: DestinationContext,
                                      model: entities.Model) -> rules.Route:
    return VPCPeersGeneratorCommon(derived, context, model)


def VPCPeersNoCustomRoutingGenerator(derived: rules.Route, context: DestinationContext,
                                        model: entities.Model) -> rules.Route:
    return VPCPeersGeneratorCommon(derived, context, model)
