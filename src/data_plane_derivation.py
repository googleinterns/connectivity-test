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

import random

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

from src.network_hierarchy import *

"""
Upon a configuration change from the customer or some automatic systems, the data plane is updated according to a series
of rules, among which the route tables are more complex because a single change to a route may be propagated to other 
networks. 

Possible propagations within GCP:
1. VPC network peering
2. BGP session

This module derive the new data plane model based on the current data plane model and the update configuration. 

This derivation is considered valid based on equivalence instead of equality because:
1) a propagated route may be given id and name randomly 
2) a dynamic route is subject to an unpredictable priority degradation after propagation to another region 

The detailed scenarios are listed below:

Dynamic routes
1. In a BGP session, an IP prefix is announced or revoked from the on-premise network
2. A VPN tunnel is removed: remove involved routes and propagated routes
3. A custom IP prefix is added to or deleted from the BGP session of a VPN Tunnel, while the other end is also in GCP
4. The MED of a BGP session is changed, then the priorities of custom route and dynamic routes are all updated 

Subnet routes
1. A subnet is added, (of course) with new IP ranges, or deleted. 
2. An IP range is added to a subnet (cannot delete)
3. An IP range of a subnet is changed (can only be larger)

Static routes
1. A static route is added or removed
2. The priority, tags, etc. of a static route is changed
"""

DIGITS = [str(i) for i in range(0, 10)]
DIGITS16 = DIGITS + ['a', 'b', 'c', 'd', 'e', 'f']

PEERING_ROUTE_TYPES = [
    rules.Route.PEERING_SUBNET,
    rules.Route.PEERING_STATIC,
    rules.Route.PEERING_DYNAMIC,
]


def getNetworkViaUrl(model: entities.Model, url: str) -> entities.Network:
    for network in model.networks:
        network: entities.Network
        if network.url == url:
            return network


def genId(num=18) -> str:
    return "".join(random.choices(DIGITS, k=18))


def genHex(num: int) -> str:
    return "".join(random.choices(DIGITS16, k=num))


def getPeeringRouteType(x: rules.Route.RouteType) -> rules.Route.RouteType:
    return {
        rules.Route.RouteType.SUBNET: rules.Route.RouteType.PEERING_SUBNET,
        rules.Route.RouteType.STATIC: rules.Route.RouteType.PEERING_STATIC,
        rules.Route.RouteType.DYNAMIC: rules.Route.RouteType.PEERING_DYNAMIC
    }[x]


def isPeeringRouteType(t1: rules.Route.RouteType, t2: rules.Route.RouteType) -> bool:
    return getPeeringRouteType(t1) == t2


def _operateOnPeeringRoutes(parsed: Model, rootRoutes: List[rules.Route],
                            op: Callable[[Model, rules.Route], None],
                            operateRootRoutes: bool = True):
    for route in rootRoutes:
        route: rules.Route
        if operateRootRoutes:
            op(parsed, route)
        if route.route_type == rules.Route.RouteType.DYNAMIC and route.from_local == False:
            continue

        # delete the dependents: routes that are exported/imported via peering. No other possibilities.
        network: Network = parsed.GetNetwork(route.instance_filter.network)
        globalRouting = network.proto.routing_mode == entities.Network.RoutingMode.GLOBAL

        for peerNetwork, peerProto in network.peers.values():
            peerNetwork: Network
            peerProto: entities.NetworkPeer
            activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE

            if not peerProto.export_custom_routes or peerProto.state != activeState:
                continue

            peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
            if not peerProtoOnPeer.import_custom_routes or peerProtoOnPeer.state != activeState:
                continue

            for peerRoute in peerNetwork.routes:
                peerRoute: rules.Route

                if isPeeringRouteType(route.route_type, peerRoute.route_type) \
                        and peerRoute.next_hop_peering == peerProtoOnPeer.name \
                        and peerRoute.dest_range == route.dest_range:
                    op(parsed, peerRoute)


def addDynamicRoutesAndPropagateToPeers(
        parsed: Model, rootRoutes: List[rules.Route],
        regionCost: Callable[[str, str], int] = lambda r1, r2: 100  # input two regions, output the regional cost
):
    for route in rootRoutes:
        route: rules.Route
        # delete the dependents: routes that are exported/imported via peering. No other possibilities.
        network: Network = parsed.GetNetwork(route.instance_filter.network)
        globalRouting = network.proto.routing_mode == entities.Network.RoutingMode.GLOBAL

        if globalRouting:
            regions = REGION_LIST  # network.proto.regions
        elif route.region in network.proto.regions:
            regions = [route.region]
        else:
            return

        for region in regions:
            id = genId()

            derived: rules.Route = rules.Route(
                id=id,
                name=route.name[0:-18] + id,
                priority=route.priority + regionCost(region, route.region),
                dest_range=route.dest_range,
                instance_filter=rules.InstanceFilter(network=network.proto.url),
                next_hop_tunnel=route.next_hop_tunnel,
                url=route.url,
                route_type=rules.Route.RouteType.DYNAMIC,
                region=region,
                from_local=region == route.region
            )

            parsed.model.routes.append(derived)

        for peerNetwork, peerProto in network.peers.values():
            peerNetwork: Network
            peerProto: entities.NetworkPeer
            activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE

            if not peerProto.export_custom_routes or peerProto.state != activeState:
                continue

            peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
            if not peerProtoOnPeer.import_custom_routes or peerProtoOnPeer.state != activeState:
                continue

            regions = []
            if globalRouting:
                regions = peerNetwork.proto.regions
            elif route.region in peerNetwork.proto.regions:
                regions = [route.region]

            for region in regions:
                id = genId()
                derived: rules.Route = rules.Route(
                    id=id,
                    name=ParseProjectFromUrl(peerNetwork.proto.url) + "::imported-custom-route-" + id,
                    priority=route.priority + regionCost(region, route.region),
                    dest_range=route.dest_range,
                    instance_filter=rules.InstanceFilter(network=peerNetwork.proto.url),
                    next_hop_peering=peerProtoOnPeer.name,
                    url="imported-custom-route-" + IPv4RangeToStr(route.dest_range),
                    route_type=rules.Route.RouteType.PEERING_DYNAMIC,
                    region=region,
                    next_hop_region=route.region,
                )

                parsed.model.routes.append(derived)


def addStaticRoutesAndPropagateToPeers(parsed: Model, rootRoutes: List[rules.Route]):
    for route in rootRoutes:
        route: rules.Route
        # delete the dependents: routes that are exported/imported via peering. No other possibilities.
        network: Network = parsed.GetNetwork(route.instance_filter.network)

        parsed.model.routes.append(route)

        for peerNetwork, peerProto in network.peers.values():
            peerNetwork: Network
            peerProto: entities.NetworkPeer
            activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE

            if peerProto.state != activeState: continue

            peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
            if peerProtoOnPeer.state != activeState: continue

            projectName = ParseProjectFromUrl(peerNetwork.proto.url)
            hexId = genHex(16)
            id = genId()

            derived: rules.Route = rules.Route(
                id=id,
                name=projectName + "::peering-route-" + hexId,
                priority=route.priority,
                dest_range=route.dest_range,
                instance_filter=rules.InstanceFilter(network=peerNetwork.proto.url),
                next_hop_peering=peerProtoOnPeer.name,
                url="projects/%s/global/routes/peering-route-%s" % (projectName, hexId),
                route_type=rules.Route.RouteType.PEERING_SUBNET,
            )

            parsed.model.routes.append(derived)


def removeRoutesAndPropagate(parsed: Model, rootRoutes: List[rules.Route]):
    _operateOnPeeringRoutes(parsed, rootRoutes, lambda parsed, route: parsed.model.routes.remove(route))


def deriveAfterLearnedBGP_Announcements(model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str],
                                        prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    Some prefixes are announced from the other end of the vpnTunnel. Will insert the routes, and propagate them to
    peering networks, if the custom routes are enabled.
    """
    result: entities.Model = entities.Model()
    result.CopyFrom(model)

    parsed: Model = Model(result)

    if isinstance(vpnTunnel, str):
        vpnTunnel = parsed.vpnTunnels[vpnTunnel]

    MED = 100  # TODO get med from proto
    networkUrl = parsed.vpnGateways[vpnTunnel.vpn_gateway].network

    routes = []
    for prefix in prefixes:
        prefixStr = IPv4RangeToStr(prefix)
        id = genId()
        route: rules.Route = rules.Route(
            id=id,  # TODO what is the logic to generate id?   r"\d{18}"
            # name=r"[-\da-z]+::dynamic-route-\d{18}",  # TODO the router name is needed here. Just walk around
            name=ParseNetworkFromUrl(networkUrl) + "::dynamic-route-" + id,
            priority=MED,
            dest_range=prefix,
            instance_filter=rules.InstanceFilter(network=networkUrl),
            next_hop_tunnel=vpnTunnel.url,
            url="dynamic-route-" + prefixStr,
            route_type=rules.Route.RouteType.DYNAMIC,
            region=vpnTunnel.region
        )
        routes.append(route)

    addDynamicRoutesAndPropagateToPeers(parsed, routes)  # TODO input a real regional cost

    return result


def deriveAfterCustomRoutesAdded(model: entities.Model, vpnTunnel: entities.VPNTunnel,
                                 prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    Some prefixes are announced this side. If the other side happens to be in the model, then install and propagate.
    But do not install any route on this side.
    """
    parsed: Model = Model(model)
    if isinstance(vpnTunnel, str):
        vpnTunnel = parsed.vpnTunnels[vpnTunnel]

    peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
    if not peerTunnel: return

    return deriveAfterLearnedBGP_Announcements(model, peerTunnel, prefixes)


def deriveAfterCustomRoutesRemoved(model: entities.Model, vpnTunnel: entities.VPNTunnel,
                                 prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    Some existing prefixes are removed on this side. If the other side happens to be in the model,
    then remove the installed routes and propagate.
    But do not remove any route on this side.
    """
    parsed: Model = Model(model)
    if isinstance(vpnTunnel, str):
        vpnTunnel = parsed.vpnTunnels[vpnTunnel]

    peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
    if not peerTunnel: return

    return deriveAfterBGP_Revokations(model, peerTunnel, prefixes)


def deriveAfterBGP_Revokations(model: entities.Model, vpnTunnel: entities.VPNTunnel,
                               prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    When the other side revokes some prefixes, deleted the rules on this side.
    The prefixes should be existing, and no prefix aggregation is done inside this function.
    """
    result: entities.Model = entities.Model()
    result.CopyFrom(model)
    parsed: Model = Model(result)
    if isinstance(vpnTunnel, str):
        vpnTunnel = parsed.vpnTunnels[vpnTunnel]

    peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
    if not peerTunnel: return result

    rootRoutes: List[rules.Route] = list(filter(lambda r: r.dest_range in prefixes,
                                                parsed.vpnTunnelToRoutes[peerTunnel.url]))

    removeRoutesAndPropagate(parsed, rootRoutes)

    return result

