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
import re
from collections import defaultdict
from typing import Tuple, List, Set

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

REGION_LIST = [
    "asia-east1",
    "asia-east2",
    "asia-northeast1",
    "asia-northeast2",
    "asia-northeast3",
    "asia-south1",
    "asia-southeast1",
    "asia-southeast2",
    "australia-southeast1",
    "europe-north1",
    "europe-west1",
    "europe-west2",
    "europe-west3",
    "europe-west4",
    "europe-west5",
    "europe-west6",
    "northamerica-northeast1",
    "southamerica-east1",
    "us-central1",
    "us-central2",
    "us-east1",
    "us-east2",
    "us-east4",
    "us-west1",
    "us-west2",
    "us-west3",
    "us-west4"
]

DIGITS = [str(i) for i in range(0, 10)]
DIGITS16 = DIGITS + ['a', 'b', 'c', 'd', 'e', 'f']

PEERING_ROUTE_TYPES = [
    rules.Route.PEERING_SUBNET,
    rules.Route.PEERING_STATIC,
    rules.Route.PEERING_DYNAMIC,
]

WORDS_AND_DIGITS = re.compile(r'([a-zA-Z\d]+)')


def wordToCamelCase(w: str) -> str:
    """
    Transform a word w to its CamelCase version.
    This word may already be a CamelCase.

    Cannot correctly handle two successive UPPERWORDS. Will output: Upperwords
    """
    res = ""
    for i, c in enumerate(w):
        if c.isupper() and (
                i == 0 or not w[i - 1].isupper() or
                i != len(w) - 1 and not w[i + 1].isupper()
        ):
            res += c
        else:
            res += c.lower()

    return res


def toCamelCase(s: str) -> str:
    """
    Transform a string into its corresponding CamelCase form.
    All characters that are not letter or digit are deleted, e.g., spaces, underscores, and dashes.
    """
    l = [wordToCamelCase(word) for word in re.findall(WORDS_AND_DIGITS, s)]
    return ''.join(l)


def genId(num=18) -> str:
    return "".join(random.choices(DIGITS, k=num))


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


def getTrimmedRoutes(routes: List[rules.Route], keep_priority: bool = False) -> Set[str]:
    """
    To test the derivation, the routes of the generated model should match the expected model.
    But we cannot derive many fields for exact match, e.g., id, name, etc.
    This is because the id is generated randomly internally in GCP, and the name uses the router name as a part, which
    is not provided in the current proto.

    Besides, the order of routes in the model does not affect the routing behavior, and the GCP does not document the
    rules to order the routes. So we put the routes into a set to disregard the order.

    Also, a auto-generated protobuf type is not associated with a __hash__ function. So convert the trimmed routes to str
    """

    result = set()
    for route in routes:
        trimmed = trimRoute(route, keep_priority)

        result.add(str(trimmed))

    return result


def trimRoute(route, keep_priority = False):
    trimmed = rules.Route()
    trimmed.CopyFrom(route)
    trimmed.id = "id"
    trimmed.name = "name"
    trimmed.priority = route.priority if keep_priority else 0
    return trimmed


def findNetwork(model: entities.Model, url: str):
    # return next((network for network in model.networks if network.url == networkUrl), None)
    return findByTypeAndUrl(model, "networks", url)


def findVpnTunnel(model: entities.Model, url: str):
    # return next((vpn_tunnel for vpn_tunnel in model.vpn_tunnels if vpn_tunnel.url == url), None)
    return findByTypeAndUrl(model, "vpn_tunnels", url)


def findByTypeAndUrl(model: entities.Model, type: str, url: str):
    return next((element for element in model.getattr(type) if element.url == url), None)


def clearNextHopsInRoute(derived):
    derived.ClearField("next_hop_network")
    derived.ClearField("next_hop_gateway")
    derived.ClearField("next_hop_tunnel")
    derived.ClearField("next_hop_peering")
    derived.ClearField("next_hop_blackhole")
    derived.ClearField("next_hop_vpn_gateway")


#
# def listVpnTunnelPairs(model: entities.Model, networkUrl: str) -> \
#         List[Tuple[entities.VPNTunnel, entities.VPNTunnel]]:
#     """
#     Return a list of pairs of VPN tunnels in the model. For each pair, the first element is a VPN tunnel in the given
#     network, and the second is the peering VPN tunnel.
#
#     If the peer of a VPN tunnel is not in model, the returned list does not contain this pair.
#     """
#     res = []
#
#     # collect vpn gateways in the current network
#     vpnGateways = list(filter(lambda gw: gw.network == networkUrl, model.vpn_gateways))
#     vpnGatewayUrls = map(lambda gw: gw.url, vpnGateways)
#
#     # collect the attached vpn tunnels of the vpn gateways in the current network
#     gwToTunnels = defaultdict([])
#     for tunnel in model.vpn_tunnels:
#         if tunnel.gateway in vpnGatewayUrls:
#             gwToTunnels[tunnel.gateway].append(tunnel)
#
#     for gw in vpnGateways:
#         # find peer vpn tunnels from ips in gateway
#         peerTunnels = list(filter(lambda tunnel: tunnel.peer_ip in gw.ip, model.vpn_tunnels))
#
#         # sort peer tunnels by ip
#         if len(peerTunnels) > 1 and peerTunnels[0].peer_ip != gw.ip[0]:
#             peerTunnels = [peerTunnels[1], peerTunnels[0]]
#
#         # sort tunnels by interface #
#         tunnels = sorted(gwToTunnels[gw.url], key=lambda tunnel: tunnel.vpn_gateway_interface)
#
#         # then the tunnels and peer tunnels are matched, and in order
#         res += list(zip(tunnels, peerTunnels))
#
#     return res
#

def listBgpPeers(model: entities.Model, networkUrl: str) -> List[Tuple[entities.Network, str, str]]:
    """
    Return a list of tuples, where each tuple element is:
    BGP peer, the region where the BGP session is running, and the vpn tunnel in this network.

    If a BGP peer is beyond the input model, skip that BGP peer.
    """
    res = []

    # collect vpn gateways in the current network
    vpnGateways = list(filter(lambda gw: gw.network == networkUrl, model.vpn_gateways))
    vpnGatewayUrls = map(lambda gw: gw.url, vpnGateways)

    # collect the attached vpn tunnels of the vpn gateways in the current network
    gwToTunnels = defaultdict([])
    for tunnel in model.vpn_tunnels:
        if tunnel.gateway in vpnGatewayUrls:
            gwToTunnels[tunnel.gateway].append(tunnel)

    peerTunnels = []
    for gw in vpnGateways:
        # find peer vpn tunnels from ips in gateway
        peerTunnelsOfGw = list(filter(lambda tunnel: tunnel.peer_ip in gw.ip, model.vpn_tunnels))

        # sort peer tunnels by ip
        if len(peerTunnelsOfGw) > 1 and peerTunnelsOfGw[0].peer_ip != gw.ip[0]:
            peerTunnelsOfGw = [peerTunnelsOfGw[1], peerTunnelsOfGw[0]]

        # sort tunnels by interface #
        tunnels = sorted(gwToTunnels[gw.url], key=lambda tunnel: tunnel.vpn_gateway_interface)

        # only consider DYNAMIC and ESTABLISHED BGP sessions.
        for tunnel, peerTunnel in list(zip(tunnels, peerTunnelsOfGw)):
            if tunnel.routing_type == entities.VPNTunnel.RoutingType.DYNAMIC and \
                    tunnel.status == "ESTABLISHED" and peerTunnel.status == "ESTABLISHED":
                peerTunnels.append(peerTunnel)

    # just traverse once
    for gateway in model.vpn_gateways:
        for peerTunnel in peerTunnels:
            if gateway.url == peerTunnel.vpn_gateway:
                res += (findNetwork(model, gateway.network), peerTunnel.region, peerTunnel.url)

    return res
