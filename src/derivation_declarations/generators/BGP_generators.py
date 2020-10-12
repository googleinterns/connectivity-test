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
import proto.derivation_rules_pb2 as derivation
import proto.rules_pb2 as rules
from src.utils.derivation_utils import findNetwork, listBgpPeers, REGION_LIST, clearNextHopsInRoute

Destination = derivation.DestinationAndGeneration.Destination
DestinationContext = derivation.DestinationAndGeneration.DestinationContext


def BgpPeersGeneratorCommon(derived: rules.Route, context: DestinationContext,
                            model: entities.Model) -> rules.Route:
    clearNextHopsInRoute(derived)
    derived.next_hop_tunnel = context.peer_info
    # derived.region currently contains the original route's region
    derived.from_local = derived.region == context.region
    derived.region = context.region
    derived.route_type = rules.Route.DYNAMIC
    return derived


def BgpPeersGenerator(derived: rules.Route, context: DestinationContext,
                      model: entities.Model) -> rules.Route:
    return BgpPeersGeneratorCommon(derived, context, model)


def OtherRegionsWhenGlobalRouringGenerator(derived: rules.Route, context: DestinationContext,
                                           model: entities.Model) -> rules.Route:
    return BgpPeersGeneratorCommon(derived, context, model)
