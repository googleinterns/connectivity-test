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
from src.utils.derivation_utils import clearNextHopsInRoute, getPeeringRouteType, genHex
from src.utils.ip_utils import ipv4RangeToStr
from src.utils.url_parsers import ParseProjectFromUrl

Destination = derivation.DestinationAndGeneration.Destination
DestinationContext = derivation.DestinationAndGeneration.DestinationContext


def VpcPeersGeneratorCommon(derived: rules.Route, context: DestinationContext,
                            model: entities.Model) -> rules.Route:
    clearNextHopsInRoute(derived)

    originalRouteType = derived.route_type
    projectName = ParseProjectFromUrl(derived.url)
    hexId = genHex(16)

    if originalRouteType == rules.Route.SUBNET:
        derived.name = projectName + "::default-route-" + hexId
        derived.url = "projects/%s/global/routes/peering-route-%s" % (projectName, hexId)
    else:
        # TODO here the project name should be the peer's. Make no difference after route trimming, though.
        derived.name = projectName + "::imported-custom-route-" + derived.id
        derived.url = "imported-custom-route-" + ipv4RangeToStr(derived.dest_range)
        if originalRouteType == rules.Route.DYNAMIC:
            derived.next_hop_region=derived.region
            derived.region = context.region

    derived.next_hop_peering = context.peer_info
    derived.route_type = getPeeringRouteType(originalRouteType)
    return derived


def VpcPeersCustomRoutingGenerator(derived: rules.Route, context: DestinationContext,
                                   model: entities.Model) -> rules.Route:
    return VpcPeersGeneratorCommon(derived, context, model)


def RegionsOfVpcPeersCustomRoutingGenerator(derived: rules.Route, context: DestinationContext,
                                     model: entities.Model) -> rules.Route:
    return VpcPeersGeneratorCommon(derived, context, model)
