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
import importlib
import os
from collections import defaultdict
from inspect import getmembers, isfunction
from typing import List, Any, Dict, Callable, Union

from google.protobuf import text_format

import proto.cloud_network_model_pb2 as entities
import proto.derivation_rules_pb2 as derivation_rules
import proto.rules_pb2 as rules
from src.utils.derivation_utils import findNetwork, listBgpPeers, REGION_LIST, toCamelCase, getTrimmedRoutes, trimRoute

Destination = derivation_rules.DestinationAndGeneration.Destination
DestinationContext = derivation_rules.DestinationAndGeneration.DestinationContext

"""
Upon a configuration change from the customer or automation systems, the data plane is updated according to a series
of derivation_declarations, among which the route tables are more complex because a single change to a route may be propagated to other 
networks. 

Possible propagations within GCP:
1. VPC network peering
2. BGP session

This module derive the new data plane model based on the current data plane model and the update configuration. 

This derivation is considered valid based on equivalence instead of equality because:
1) a propagated route may be given id and name randomly 
2) a dynamic route is subject to an unpredictable priority degradation after propagation to another region 
"""

# collects the derivation rules from the configuration files.
# the rule appear first in a single file has the highest priority
# rule files are optionally ordered by the optional `src/derivation_declarations/rule_order.pb` file
DerivationRules: List[derivation_rules.RouteDerivationRule] = []

# collects the route generators from the configuration files.
# the keys are the function names, and two functions should never share a name.
# the values are the functions. They modify the input route, and also return the same route.
RouteGenerators: Dict[str, Callable[[rules.Route, Any, entities.Model], rules.Route]] = defaultdict(
    lambda route, context, model: route
)


def initializeDerivationRules():
    global DerivationRules
    try:
        with open("src/derivation_declarations/rule_order.pb", "r") as f:
            fileContent = f.read()
            order = list(text_format.Parse(fileContent, derivation_rules.RuleOrder()).file_names)
    except FileNotFoundError:
        order = []

    def index(e) -> int:
        try:
            return order.index(e)
        except ValueError:
            return len(order)

    for dirName, subdirList, fileList in os.walk("src/derivation_declarations/rules"):
        print('Loading derivation rules in directory: %s. ' % dirName)

        # sort the rule files such that the mentioned files appear in the specified order and before the unmentioned files
        sortedList = sorted(fileList, key=index)

        for fname in sortedList:
            if not fname.endswith(".pb"): continue
            print('Parsing derivation rules in file: %s' % fname)

            with open(os.path.join(dirName, fname), "r") as f:
                fileContent = f.read()
                rules = text_format.Parse(fileContent, derivation_rules.RuleSet())

            DerivationRules += rules.rules


initializeDerivationRules()


def initializeRouteGenerators():
    global RouteGenerators

    try:
        with open("src/derivation_declarations/enabled_generators.pb", "r") as f:
            fileContent = f.read()
            filesToRead = list(text_format.Parse(fileContent, derivation_rules.EnabledGenerators()).file_names)
    except FileNotFoundError:
        filesToRead = []

    for dirName, subdirList, fileList in os.walk("src/derivation_declarations/generators"):
        print('Loading route generators in directory: %s. ' % dirName)

        for fname in set(fileList).intersection(filesToRead):
            if not fname.endswith(".py"): continue
            print('Parsing the route generator in file: %s' % fname)
            module = importlib.import_module("src.derivation_declarations." + fname[:-3])

            for member in getmembers(module):
                if isfunction(member[1]):
                    fnName = member[0]
                    if not fnName.endswith("Generator"): continue
                    fnName = fnName[0:-len("Generator")]
                    RouteGenerators[fnName] = member[1]


initializeRouteGenerators()


def IdentifyRootRoutes(model: entities.Model) -> List[rules.Route]:
    """
    Return the root routes in the parameter *model*.
    The root routes are defined as routes not derived by others
    """
    res = []

    not_root = []  # not_root = set()  # Error due to "unhashable object"

    for route in model.routes:
        if route in not_root: continue
        for derived in FindDerivedRoutes(model, route):
            not_root.append(derived)

    return res


def FindRootRoute(model: entities.Model, derived: rules.Route) -> rules.Route:
    """
    Return the root route of the input route. The input route *derived* itself may be its root route.
    """
    visited = set()

    if derived not in model.routes:
        raise ValueError("The input derived route does not present in the specified model")

    for route in model.routes:
        if route in visited: continue
        for route in FindDerivedRoutes(model, route):
            visited.add(route)
            if derived in visited:
                return route


def FindDerivedRoutes(model: entities.Model, start_route: rules.Route) -> List[rules.Route]:
    """
    Return the derived routes of a route *start_route*, based on the model.
    """
    if start_route not in model.routes:
        raise ValueError("The input start route does not present in the specified model")

    return Derive(model, [start_route], False)[0]


def match(route: rules.Route, rule: derivation_rules.RouteDerivationRule) -> bool:
    """
    Try to match a Route against a RouteDerivationRule. Return True if matched.

    The matching is the exact match for all present fields.
    Can specify many filters and if any filter is an exact match, this whole rule is a match.

    A limited programming ability is provided to specify some predicates based on the Route proto.
    In each field of the Route proto where the type is "string", use triple grave accent (```) in the beginning to
    start a Python boolean lambda function body. This lambda has the following implicit parameters:
    - *route* the route being matched
    And the return value is converted to a boolean. If exception are raised, consider it as a non-match.

    Example filters:
       filters {
         region: "us-west1"
       }
       and
       filters {
         region: "```route.region == 'us-west1'"
       }
    Both filters match routes that in region us-west1.
    """
    for filter in rule.filters:
        # collect the field name and value
        filterFields = dict(map(lambda item: (item[0].name, getattr(filter, item[0].name)), filter.ListFields()))
        routeFields = dict(map(lambda item: (item[0].name, getattr(route, item[0].name)), route.ListFields()))

        matched = True
        for field, value in filterFields.items():
            if isinstance(value, str) and value.startswith("```"):
                fun = eval("lambda route: %s" % value.replace("```", ""))
                matched &= fun(route) == value
            else:
                matched &= (field, value) in routeFields.items()

            if not matched: break

        if matched:
            return True

    for lambda_filter in rule.lambda_filters:
        try:
            fun = eval("lambda route: %s" % lambda_filter.replace("```", ""))
            if fun(route): return True
        except Exception as e:
            print("Error occurred in lambda filter: %s\n%s" % (lambda_filter, e))
            pass

    return False


def expand_vpc_peer_to_two_types(rule):
    peersAsDst = next(filter(lambda d: d.destination == Destination.VPC_PEERS, rule.destinations), None)
    if peersAsDst is not None:
        rule.destinations.remove(peersAsDst)

        for dst in [Destination.VPC_PEERS_CUSTOM_ROUTING, Destination.VPC_PEERS_NO_CUSTOM_ROUTING]:
            custom = derivation_rules.DestinationAndGeneration()
            custom.CopyFrom(peersAsDst)
            custom.destination = dst
            rule.destinations.append(custom)


def get_contexts(route: rules.Route, destination: Destination, model: entities.Model) -> List[DestinationContext]:
    """
    Get the contexts to install new routes, based on the given destination.
    The destination is configurable in the rule files and has fixed builtin semantics -- the returned context type is
    defined by the destination:

     - VPC_PEERS:                   all active peering VPC networks

     - VPC_PEERS_CUSTOM_ROUTING:    all active peering VPC networks that import custom routes from the route's network,
                                    and simultaneously, the route's network export custom routes to them

     - BGP_PEERS:                   all networks that hold one or more *live* BGP session with the current network, and
                                    the involved VPN tunnels in current network enable subnet advertising

     - OTHER_REGIONS_WHEN_GLOBAL_ROUTING:   all regions other than the route's region, when the current network enables
                                            global routing
    """
    res: List[DestinationContext] = []
    network = findNetwork(model, route.instance_filter.network)

    if network is None: return []

    if destination in [Destination.VPC_PEERS, Destination.VPC_PEERS_CUSTOM_ROUTING]:
        for peer in network.peers:
            peerNetwork = findNetwork(model, peer.peer_network)
            peerInPeer = next((peer for peer in peerNetwork.peers if peer.peer_network == network.url), None)

            if entities.NetworkPeer.NetworkPeeringState.INACTIVE in [peer.state, peerInPeer.state]: continue

            if destination == Destination.VPC_PEERS or peer.export_custom_routes and peerInPeer.import_custom_routes:
                res.append(DestinationContext(network=peerNetwork.url, peer_info=peerInPeer.url))
    elif destination == Destination.BGP_PEERS:
        for network, region, peer_info in listBgpPeers(model, network.url):
            res.append(DestinationContext(network=network.url, region=region, peer_info=peer_info))
    elif destination == Destination.OTHER_REGIONS_WHEN_GLOBAL_ROUTING:
        if network.routing_mode == entities.Network.RoutingMode.GLOBAL:
            for region in REGION_LIST - route.region:
                res.append(DestinationContext(network=network.url, region=region))
    else:
        raise ValueError("The destination is not supported: ", destination)

    return res


def Derive(model: entities.Model, start_routes: List[rules.Route],
           __insert_missing_derived_routes: bool = True) -> List[List[rules.Route]]:
    """
    Derive routes in the input model, starting from the routes *start_routes*.
    If the list *start_routes* is empty, consider all root routes as start_routes.

    Return a list, where the i-th element is a list, containing the derived routes of the i-th start route.
    A derived route can be an existing route, or a newly inserted route.

    - if *__insert_missing_derived_routes* is set to True, the input model may be modified during the derivation, and
      all the returned derived routes have two possible origins:
      - derived routes newly inserted into the model by this function
      - derived routes existing before this function call

    - if *__insert_missing_derived_routes* is set to False, only find existing derived routes, and will not create new
      routes. All the returned derived routes have two possible origins:
      - derived routes existing before this function call
      - derived routes newly created by this function and not inserted into this model yet
    """

    if not start_routes: start_routes = IdentifyRootRoutes(model)

    def dfs(start_routes: List[rules.Route]) -> List[List[rules.Route]]:
        res = [[] for route in start_routes]

        for i, route in enumerate(start_routes):
            rule: derivation_rules.RouteDerivationRule = None
            for _rule in DerivationRules:
                if match(route, _rule):
                    rule = _rule
                    break

            if rule is None:
                print("Route not covered by any rules: \n" + str(rule))
                continue

            expand_vpc_peer_to_two_types(rule)

            # from this line, destination is never VPC_PEERS
            for destination in rule.destinations:
                contexts = get_contexts(route, destination.destination, model)
                dstName = derivation_rules.DestinationAndGeneration.Destination.values_by_number[
                    destination.destination]
                fnames = ["COMMON", dstName, ]

                for context in contexts:
                    derived = rules.Route()

                    derived.CopyFrom(route)

                    for fname in fnames + (["%s_%s" % (rule.name, dstName)] if rule.name else []):
                        fname = toCamelCase(fname)
                        f = RouteGenerators[fname]
                        f(derived, context, model)

                    model.routes.append(derived)
                    res[i].append(derived)

                    returned = dfs(derived)
                    res[i] += returned[0]
        return res

    res = dfs(start_routes)

    routeIndex = defaultdict(lambda: None)  # from string of the trimmed route to the original route

    for route in model.routes:
        trimmed = trimRoute(route)
        routeIndex[str(trimmed)] = route  # use string because the protobuf itself is not hashable.

    for i in range(len(res)):
        derived_routes = res[i]

        for j in range(len(derived_routes)):
            route = derived_routes[j]
            trimmed = trimRoute(route)
            matched = routeIndex[str(trimmed)]
            if matched:
                res[i][j] = matched
            elif __insert_missing_derived_routes:
                model.routes.append(route)
    return res


#
# def deriveAfterCustomRoutesAdded(model: entities.Model, vpnTunnel: entities.VPNTunnel,
#                                  prefixes: List[rules.Ipv4Range]) -> entities.Model:
#     """
#     Some prefixes are announced this side. if the other side happens to be in the model, then install and propagate.
#     But do not install any route on this side.
#     """
#     if isinstance(vpnTunnel, str):
#         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
#
#     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
#     if not peerTunnel: return
#
#     return deriveAfterLearnedBGP_Announcements(model, peerTunnel, prefixes)
#
#
# def deriveAfterCustomRoutesRemoved(model: entities.Model, vpnTunnel: entities.VPNTunnel,
#                                    prefixes: List[rules.Ipv4Range]) -> entities.Model:
#     """
#     Some existing prefixes are removed on this side. if the other side happens to be in the model,
#     then remove the installed routes and propagate.
#     But do not remove any route on this side.
#     """
#     parsed: Model = Model(model)
#     if isinstance(vpnTunnel, str):
#         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
#
#     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
#     if not peerTunnel: return
#
#     return deriveAfterBGP_Revokations(model, peerTunnel, prefixes)
#
#
# def deriveAfterBGP_Revokations(model: entities.Model, vpnTunnel: entities.VPNTunnel,
#                                prefixes: List[rules.Ipv4Range]) -> entities.Model:
#     """
#     When the other side revokes some prefixes, deleted the derivation_declarations on this side.
#     The prefixes should be existing, and no prefix aggregation is done inside this function.
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#     parsed: Model = Model(result)
#     if isinstance(vpnTunnel, str):
#         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
#
#     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
#     if not peerTunnel: return result
#
#     rootRoutes: List[rules.Route] = list(filter(lambda r: r.dest_range in prefixes,
#                                                 parsed.vpnTunnelToRoutes[peerTunnel.url]))
#
#     removeRoutesAndPropagate(parsed, rootRoutes)
#
#     return result
#
#
# def deriveAfterVpnTunnelRemoval(model: entities.Model, vpnTunnel: entities.VPNTunnel) -> entities.Model:
#     """
#     Will remove all learned routes and custom routes, and propagate the removal
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#
#     parsed: Model = Model(result)
#
#     rootRoutes: List[rules.Route] = parsed.vpnTunnelToRoutes[vpnTunnel.url]
#
#     removeRoutesAndPropagate(parsed, rootRoutes)
#
#     # all routes that are installed via the peer. if the peer is in the model model
#     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
#     if peerTunnel:
#         rootRoutes = parsed.vpnTunnelToRoutes[peerTunnel.url]
#         removeRoutesAndPropagate(parsed, rootRoutes)
#
#     return result
#
#
# def deriveAfterBGP_MEDChanged(model: entities.Model, vpnTunnel: entities.VPNTunnel, MED_diff: int) -> entities.Model:
#     """
#     Will change the priority of all influenced routes, and propagate
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#
#     parsed: Model = Model(result)
#
#     rootRoutes: List[rules.Route] = parsed.vpnTunnelToRoutes[vpnTunnel.url]
#
#     def adjust(parsed, route): route.priority += MED_diff
#
#     _operateOnRoutesAndPropagate(parsed, rootRoutes, adjust)
#
#     return result
#
#
# def deriveAfterSubnetAdded(model: entities.Model, subnet: entities.Subnet) -> entities.Model:
#     """
#     When a subnet is added, the subnet routes are installed with highest priority, and propagated to peers.
#     if the current network or a peer has a vpn tunnel connecting to a network that is also in the model, then propagate
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#
#     result.subnets.append(subnet)
#
#     network = result.GetNetwork(subnet.network)
#     network.subnets.append(subnet.url)
#
#     parsed: Model = Model(result)
#
#     rootRoutes: List[rules.Route] = []
#
#     projectName = ParseProjectFromUrl(subnet.url)
#     for range in [subnet.ipv4_range] + subnet.secondary_ranges:
#         hexId = genHex(16)
#         route = rules.Route(
#             id=genId(),
#             name=projectName + "::default-route-" + hexId,
#             priority=0,
#             dest_range=range,
#             next_hop_network=network.url,
#             instance_filter=rules.InstanceFilter(network=network.url),
#             url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
#             route_type=rules.Route.RouteType.SUBNET
#         )
#
#         rootRoutes.append(route)
#
#     addStaticRoutesAndPropagateToPeers(parsed, rootRoutes)
#
#     if subnet.region not in network.regions:
#         network.regions.append(subnet.region)
#
#     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
#
#     return result
#
#
# def deriveAfterSubnetRemoved(model: entities.Model, subnet: entities.Subnet) -> entities.Model:
#     """
#     the pre-requisite is that no instance is under this subnet.
#     remove subnet routes in this network and peers, and remove a region, if this subnet is the last in the region
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#
#     result.subnets.remove(subnet)
#     parsed: Model = Model(result)
#
#     network = result.GetNetwork(subnet.network)
#     network.subnets.remove(subnet.url)
#
#     ipRanges = [subnet.ipv4_range] + subnet.secondary_ranges
#     rootRoutes: List[rules.Route] = list(filter(
#         lambda r: r.route_type == rules.Route.RouteType.SUBNET and r.dest_range in ipRanges,
#         network.routes))
#
#     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
#     removeRoutesAndPropagate(parsed, rootRoutes)
#
#     if subnet.region in network.regions:
#         network.regions.remove(subnet.region)
#
#     return result
#
#
# def deriveAfterIpRangesAdded(model: entities.Model, subnet: entities.Subnet,
#                              ipRanges: List[rules.Ipv4Range]) -> entities.Model:
#     """
#     Adding some secondary ipRanges to a subnet requires the range update in the subnet, and
#     installing subnet derivation_declarations to the network and peers.
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#
#     parsed: Model = Model(result)
#
#     network = result.GetNetwork(subnet.network)
#     subnet = result.GetSubnet(subnet.url)
#
#     rootRoutes: List[rules.Route] = []
#
#     projectName = ParseProjectFromUrl(subnet.url)
#     for range in ipRanges:
#         subnet.secondary_ranges.append(range)
#
#         hexId = genHex(16)
#         route = rules.Route(
#             id=genId(),
#             name=projectName + "::default-route-" + hexId,
#             priority=0,
#             dest_range=range,
#             next_hop_network=network.url,
#             instance_filter=rules.InstanceFilter(network=network.url),
#             url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
#             route_type=rules.Route.RouteType.SUBNET
#         )
#
#         rootRoutes.append(route)
#
#     # TODO propagate via BGP. depending on regional or global routing of the network.
#     addStaticRoutesAndPropagateToPeers(parsed, rootRoutes)
#
#     return result
#
#
# def deriveAfterIpRangeEnlarged(model: entities.Model, subnet: entities.Subnet,
#                                ipRanges: Dict[rules.Ipv4Range, rules.Ipv4Range]) -> entities.Model:
#     """
#     if ranges of a subnet is enlarged, then the corresponding routes are changed accordingly
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#     parsed: Model = Model(result)
#
#     network = result.GetNetwork(subnet.network)
#     subnet = result.GetSubnet(subnet.url)
#
#     rootRoutes: List[rules.Route] = list(filter(
#         lambda r: r.route_type == rules.Route.RouteType.SUBNET and r.dest_range in ipRanges,
#         network.routes))
#
#     for range, enlargedRange in ipRanges.items():
#         subnet.secondary_ranges.remove(range)
#         subnet.secondary_ranges.append(enlargedRange)
#
#     def enlarge(parsed, route):
#         route.dest_range = ipRanges[route.dest_range]
#
#     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
#     _operateOnRoutesAndPropagate(parsed, rootRoutes, enlarge)
#
#     return result
#
#
# def deriveAfterStaticRoutesAdded(model: entities.Model, routes: rules.Route) -> entities.Model:
#     """
#     Add the static route and propagate to peers
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#     parsed: Model = Model(result)
#
#     addStaticRoutesAndPropagateToPeers(parsed, routes)
#
#     return result
#
#
# def deriveAfterStaticRouteUpdated(model: entities.Model, routes: rules.Route) -> entities.Model:
#     """
#     Update the static route and propagate to peers
#     """
#     result: entities.Model = entities.Model()
#     result.CopyFrom(model)
#     parsed: Model = Model(result)
#
#     urlToRoute = dict()
#     for r in routes:
#         urlToRoute[r.url] = r
#
#     rootRoutes: List[rules.Route] = list(filter(lambda r: r.url in urlToRoute, result.routes))
#
#     def update(parsed, route):
#         route.CopyFrom(urlToRoute[route.url])
#
#     _operateOnRoutesAndPropagate(parsed, rootRoutes, update)
#
#     return result
#
# # DIGITS = [str(i) for i in range(0, 10)]
# # DIGITS16 = DIGITS + ['a', 'b', 'c', 'd', 'e', 'f']
# #
# # PEERING_ROUTE_TYPES = [
# #     rules.Route.PEERING_SUBNET,
# #     rules.Route.PEERING_STATIC,
# #     rules.Route.PEERING_DYNAMIC,
# # ]
# #
# #
# # def getNetworkViaUrl(model: entities.Model, url: str) -> entities.Network:
# #     for network in model.networks:
# #         network: entities.Network
# #         if network.url == url:
# #             return network
# #
# #
# # def genId(num=18) -> str:
# #     return "".join(random.choices(DIGITS, k=18))
# #
# #
# # def genHex(num: int) -> str:
# #     return "".join(random.choices(DIGITS16, k=num))
# #
# #
# # def getPeeringRouteType(x: rules.Route.RouteType) -> rules.Route.RouteType:
# #     return {
# #         rules.Route.RouteType.SUBNET: rules.Route.RouteType.PEERING_SUBNET,
# #         rules.Route.RouteType.STATIC: rules.Route.RouteType.PEERING_STATIC,
# #         rules.Route.RouteType.DYNAMIC: rules.Route.RouteType.PEERING_DYNAMIC
# #     }[x]
# #
# #
# # def isPeeringRouteType(t1: rules.Route.RouteType, t2: rules.Route.RouteType) -> bool:
# #     return getPeeringRouteType(t1) == t2
# #
# #
# # def _operateOnRoutesAndPropagate(parsed: Model, rootRoutes: List[rules.Route],
# #                                  op: Callable[[Model, rules.Route], None]):
# #     """
# #     Propagate dynamic routes, subnet routes, and static routes to peers. excluding:
# #         - tagged routes
# #         - routes to Internet gateway
# #         - routes that are derived from others (peering routes and non-local dynamic routes)
# #
# #     Propagate subnet routes to vpn peers.
# #         - if this network is in global routing mode, propagate all subnet routes
# #         - Else, this network is in regional routing mode, propagate subnet routes that are
# #             in the region that has a vpn tunnel to the vpn peer.
# #     """
# #     for route in rootRoutes:
# #         route: rules.Route
# #
# #         op(parsed, route)
# #
# #         # Propagate dynamic routes, subnet routes, and static routes to peers. excluding:
# #         #     - tagged routes
# #         #     - routes to Internet gateway
# #         #     - routes that are derived from others (peering routes and non-local dynamic routes)
# #         if route.next_hop_gateway: continue
# #         if route.instance_filter.attributes.tag: continue  # TODO may cause null pointer exception or something.
# #         if route.route_type == rules.Route.RouteType.DYNAMIC and route.from_local == False:
# #             continue
# #
# #         network: Network = parsed.GetNetwork(route.instance_filter.network)
# #
# #         for peerNetwork, peerProto in network.peers.values():
# #             peerNetwork: Network
# #             peerProto: entities.NetworkPeer
# #             activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE
# #
# #             if not peerProto.export_custom_routes or peerProto.state != activeState:
# #                 continue
# #
# #             peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
# #             if not peerProtoOnPeer.import_custom_routes or peerProtoOnPeer.state != activeState:
# #                 continue
# #
# #             for peerRoute in peerNetwork.routes:
# #                 peerRoute: rules.Route
# #
# #                 if isPeeringRouteType(route.route_type, peerRoute.route_type) \
# #                         and peerRoute.next_hop_peering == peerProtoOnPeer.name \
# #                         and peerRoute.dest_range == route.dest_range:
# #                     op(parsed, peerRoute)
# #                     break
# #
# #         # Propagate subnet routes to vpn peers.
# #         #     - if this network is in global routing mode, propagate all subnet routes
# #         #     - Else, this network is in regional routing mode, propagate subnet routes that are
# #         #         in the region that has a vpn tunnel to the vpn peer.
# #         if route.route_type != rules.Route.RouteType.SUBNET: return
# #         globalRouting = network.proto.routing_mode == entities.Network.RoutingMode.GLOBAL
# #
# #         routeRegion = network.rangeToSubnet[route.dest_range].proto.region
# #         vpnTunnels = []
# #
# #         if globalRouting:
# #             for region in network.regions.values():
# #                 vpnTunnels += region.vpnTunnels
# #         else:
# #             vpnTunnels = network.regions[routeRegion].vpnTunnels
# #
# #         for tunnel in vpnTunnels:
# #             _operateOnDynamicRoutes(parsed, [route.dest_range], parsed.getPeerVpnTunnel(tunnel))
# #
# #
# # def _addDynamicRoutesAndPropagateToPeers(
# #         parsed: Model, rootRoutes: List[rules.Route],
# #         regionCost: Callable[[str, str], int] = lambda r1, r2: 100  # input two regions, output the regional cost
# # ):
# #     for route in rootRoutes:
# #         route: rules.Route
# #         # delete the dependents: routes that are exported/imported via peering. No other possibilities.
# #         network: Network = parsed.GetNetwork(route.instance_filter.network)
# #         globalRouting = network.proto.routing_mode == entities.Network.RoutingMode.GLOBAL
# #
# #         if globalRouting:
# #             regions = REGION_LIST  # network.proto.regions
# #         elif route.region in network.proto.regions:
# #             regions = [route.region]
# #         else:
# #             return
# #
# #         for region in regions:
# #             id = genId()
# #
# #             derived: rules.Route = rules.Route(
# #                 id=id,
# #                 name=route.name[0:-18] + id,
# #                 priority=route.priority + regionCost(region, route.region),
# #                 dest_range=route.dest_range,
# #                 instance_filter=rules.InstanceFilter(network=network.proto.url),
# #                 next_hop_tunnel=route.next_hop_tunnel,
# #                 url=route.url,
# #                 route_type=rules.Route.RouteType.DYNAMIC,
# #                 region=region,
# #                 from_local=region == route.region
# #             )
# #
# #             parsed.model.routes.append(derived)
# #
# #         for peerNetwork, peerProto in network.peers.values():
# #             peerNetwork: Network
# #             peerProto: entities.NetworkPeer
# #             activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE
# #
# #             if not peerProto.export_custom_routes or peerProto.state != activeState:
# #                 continue
# #
# #             peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
# #             if not peerProtoOnPeer.import_custom_routes or peerProtoOnPeer.state != activeState:
# #                 continue
# #
# #             regions = []
# #             if globalRouting:
# #                 regions = peerNetwork.proto.regions
# #             elif route.region in peerNetwork.proto.regions:
# #                 regions = [route.region]
# #
# #             for region in regions:
# #                 id = genId()
# #                 derived: rules.Route = rules.Route(
# #                     id=id,
# #                     name=ParseProjectFromUrl(peerNetwork.proto.url) + "::imported-custom-route-" + id,
# #                     priority=route.priority + regionCost(region, route.region),
# #                     dest_range=route.dest_range,
# #                     instance_filter=rules.InstanceFilter(network=peerNetwork.proto.url),
# #                     next_hop_peering=peerProtoOnPeer.name,
# #                     url="imported-custom-route-" + IPv4RangeToStr(route.dest_range),
# #                     route_type=rules.Route.RouteType.PEERING_DYNAMIC,
# #                     region=region,
# #                     next_hop_region=route.region,
# #                 )
# #
# #                 parsed.model.routes.append(derived)
# #
# #
# # def addStaticRoutesAndPropagateToPeers(parsed: Model, rootRoutes: List[rules.Route]):
# #     for route in rootRoutes:
# #         route: rules.Route
# #         # delete the dependents: routes that are exported/imported via peering. No other possibilities.
# #         network: Network = parsed.GetNetwork(route.instance_filter.network)
# #
# #         if route.next_hop_gateway: continue
# #         if route.instance_filter.attributes.tag: continue  # TODO may cause null pointer exception or something.
# #
# #         parsed.model.routes.append(route)
# #
# #         for peerNetwork, peerProto in network.peers.values():
# #             peerNetwork: Network
# #             peerProto: entities.NetworkPeer
# #             activeState = entities.NetworkPeer.NetworkPeeringState.ACTIVE
# #
# #             if peerProto.state != activeState: continue
# #
# #             peerProtoOnPeer = peerNetwork.peers[network.proto.url][1]
# #             if peerProtoOnPeer.state != activeState: continue
# #
# #             projectName = ParseProjectFromUrl(peerNetwork.proto.url)
# #             hexId = genHex(16)
# #             id = genId()
# #
# #             derived: rules.Route = rules.Route(
# #                 id=id,
# #                 name=projectName + "::peering-route-" + hexId,
# #                 priority=route.priority,
# #                 dest_range=route.dest_range,
# #                 instance_filter=rules.InstanceFilter(network=peerNetwork.proto.url),
# #                 next_hop_peering=peerProtoOnPeer.name,
# #                 url="projects/%s/global/routes/peering-route-%s" % (projectName, hexId),
# #                 route_type=rules.Route.RouteType.PEERING_SUBNET,
# #             )
# #
# #             parsed.model.routes.append(derived)
# #
# #
# # def removeRoutesAndPropagate(parsed: Model, rootRoutes: List[rules.Route]):
# #     _operateOnRoutesAndPropagate(parsed, rootRoutes, lambda parsed, route: parsed.model.routes.remove(route))
# #
# #
def deriveAfterLearnedBgpAdvertisements(model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str],
                                        prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    Some prefixes are announced from the other end of the vpnTunnel. Derive routes based on that.
    Return a new model after derivation. The original model is intact.
    """
    result: entities.Model = entities.Model()
    result.CopyFrom(model)

    return result
# #
# #
# # def _addDynamicRoutes(parsed, prefixes, vpnTunnel):
# #     if isinstance(vpnTunnel, str):
# #         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
# #     MED = 100  # TODO get med from proto
# #     networkUrl = parsed.vpnGateways[vpnTunnel.vpn_gateway].network
# #     routes = []
# #     for prefix in prefixes:
# #         prefixStr = IPv4RangeToStr(prefix)
# #         id = genId()
# #         route: rules.Route = rules.Route(
# #             id=id,  # TODO what is the logic to generate id?   r"\d{18}"
# #             # name=r"[-\da-z]+::dynamic-route-\d{18}",  # TODO the router name is needed here. Just walk around
# #             name=ParseNetworkFromUrl(networkUrl) + "::dynamic-route-" + id,
# #             priority=MED,
# #             dest_range=prefix,
# #             instance_filter=rules.InstanceFilter(network=networkUrl),
# #             next_hop_tunnel=vpnTunnel.url,
# #             url="dynamic-route-" + prefixStr,
# #             route_type=rules.Route.RouteType.DYNAMIC,
# #             region=vpnTunnel.region
# #         )
# #         routes.append(route)
# #     _addDynamicRoutesAndPropagateToPeers(parsed, routes)  # TODO input a real regional cost
# #
# #
# # def _operateOnDynamicRoutes(parsed, vpnTunnel, op: Callable[[Model, rules.Route], None]):
# #     if isinstance(vpnTunnel, str):
# #         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
# #     networkUrl = parsed.vpnGateways[vpnTunnel.vpn_gateway].network
# #     routes = []
# #     for prefix in prefixes:
# #         prefixStr = IPv4RangeToStr(prefix)
# #         id = genId()
# #         route: rules.Route = rules.Route(
# #             id=id,  # TODO what is the logic to generate id?   r"\d{18}"
# #             # name=r"[-\da-z]+::dynamic-route-\d{18}",  # TODO the router name is needed here. Just walk around
# #             name=ParseNetworkFromUrl(networkUrl) + "::dynamic-route-" + id,
# #             priority=MED,
# #             dest_range=prefix,
# #             instance_filter=rules.InstanceFilter(network=networkUrl),
# #             next_hop_tunnel=vpnTunnel.url,
# #             url="dynamic-route-" + prefixStr,
# #             route_type=rules.Route.RouteType.DYNAMIC,
# #             region=vpnTunnel.region
# #         )
# #         routes.append(route)
# #     _addDynamicRoutesAndPropagateToPeers(parsed, routes)  # TODO input a real regional cost
# #
# #
# # def deriveAfterCustomRoutesAdded(model: entities.Model, vpnTunnel: entities.VPNTunnel,
# #                                  prefixes: List[rules.Ipv4Range]) -> entities.Model:
# #     """
# #     Some prefixes are announced this side. if the other side happens to be in the model, then install and propagate.
# #     But do not install any route on this side.
# #     """
# #     parsed: Model = Model(model)
# #     if isinstance(vpnTunnel, str):
# #         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
# #
# #     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
# #     if not peerTunnel: return
# #
# #     return deriveAfterLearnedBGP_Announcements(model, peerTunnel, prefixes)
# #
# #
# # def deriveAfterCustomRoutesRemoved(model: entities.Model, vpnTunnel: entities.VPNTunnel,
# #                                    prefixes: List[rules.Ipv4Range]) -> entities.Model:
# #     """
# #     Some existing prefixes are removed on this side. if the other side happens to be in the model,
# #     then remove the installed routes and propagate.
# #     But do not remove any route on this side.
# #     """
# #     parsed: Model = Model(model)
# #     if isinstance(vpnTunnel, str):
# #         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
# #
# #     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
# #     if not peerTunnel: return
# #
# #     return deriveAfterBGP_Revokations(model, peerTunnel, prefixes)
# #
# #
# # def deriveAfterBGP_Revokations(model: entities.Model, vpnTunnel: entities.VPNTunnel,
# #                                prefixes: List[rules.Ipv4Range]) -> entities.Model:
# #     """
# #     When the other side revokes some prefixes, deleted the derivation_declarations on this side.
# #     The prefixes should be existing, and no prefix aggregation is done inside this function.
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #     parsed: Model = Model(result)
# #     if isinstance(vpnTunnel, str):
# #         vpnTunnel = parsed.vpnTunnels[vpnTunnel]
# #
# #     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
# #     if not peerTunnel: return result
# #
# #     rootRoutes: List[rules.Route] = list(filter(lambda r: r.dest_range in prefixes,
# #                                                 parsed.vpnTunnelToRoutes[peerTunnel.url]))
# #
# #     removeRoutesAndPropagate(parsed, rootRoutes)
# #
# #     return result
# #
# #
# # def deriveAfterVpnTunnelRemoval(model: entities.Model, vpnTunnel: entities.VPNTunnel) -> entities.Model:
# #     """
# #     Will remove all learned routes and custom routes, and propagate the removal
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #
# #     parsed: Model = Model(result)
# #
# #     rootRoutes: List[rules.Route] = parsed.vpnTunnelToRoutes[vpnTunnel.url]
# #
# #     removeRoutesAndPropagate(parsed, rootRoutes)
# #
# #     # all routes that are installed via the peer. if the peer is in the model model
# #     peerTunnel: entities.VPNTunnel = parsed.getPeerVpnTunnel(vpnTunnel)
# #     if peerTunnel:
# #         rootRoutes = parsed.vpnTunnelToRoutes[peerTunnel.url]
# #         removeRoutesAndPropagate(parsed, rootRoutes)
# #
# #     return result
# #
# #
# # def deriveAfterBGP_MEDChanged(model: entities.Model, vpnTunnel: entities.VPNTunnel, MED_diff: int) -> entities.Model:
# #     """
# #     Will change the priority of all influenced routes, and propagate
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #
# #     parsed: Model = Model(result)
# #
# #     rootRoutes: List[rules.Route] = parsed.vpnTunnelToRoutes[vpnTunnel.url]
# #
# #     def adjust(parsed, route): route.priority += MED_diff
# #
# #     _operateOnRoutesAndPropagate(parsed, rootRoutes, adjust)
# #
# #     return result
# #
# #
# # def deriveAfterSubnetAdded(model: entities.Model, subnet: entities.Subnet) -> entities.Model:
# #     """
# #     When a subnet is added, the subnet routes are installed with highest priority, and propagated to peers.
# #     if the current network or a peer has a vpn tunnel connecting to a network that is also in the model, then propagate
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #
# #     result.subnets.append(subnet)
# #
# #     network = result.GetNetwork(subnet.network)
# #     network.subnets.append(subnet.url)
# #
# #     parsed: Model = Model(result)
# #
# #     rootRoutes: List[rules.Route] = []
# #
# #     projectName = ParseProjectFromUrl(subnet.url)
# #     for range in [subnet.ipv4_range] + subnet.secondary_ranges:
# #         hexId = genHex(16)
# #         route = rules.Route(
# #             id=genId(),
# #             name=projectName + "::default-route-" + hexId,
# #             priority=0,
# #             dest_range=range,
# #             next_hop_network=network.url,
# #             instance_filter=rules.InstanceFilter(network=network.url),
# #             url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
# #             route_type=rules.Route.RouteType.SUBNET
# #         )
# #
# #         rootRoutes.append(route)
# #
# #     addStaticRoutesAndPropagateToPeers(parsed, rootRoutes)
# #
# #     if subnet.region not in network.regions:
# #         network.regions.append(subnet.region)
# #
# #     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
# #
# #     return result
# #
# #
# # def deriveAfterSubnetRemoved(model: entities.Model, subnet: entities.Subnet) -> entities.Model:
# #     """
# #     the pre-requisite is that no instance is under this subnet.
# #     remove subnet routes in this network and peers, and remove a region, if this subnet is the last in the region
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #
# #     result.subnets.remove(subnet)
# #     parsed: Model = Model(result)
# #
# #     network = result.GetNetwork(subnet.network)
# #     network.subnets.remove(subnet.url)
# #
# #     ipRanges = [subnet.ipv4_range] + subnet.secondary_ranges
# #     rootRoutes: List[rules.Route] = list(filter(
# #         lambda r: r.route_type == rules.Route.RouteType.SUBNET and r.dest_range in ipRanges,
# #         network.routes))
# #
# #     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
# #     removeRoutesAndPropagate(parsed, rootRoutes)
# #
# #     if subnet.region in network.regions:
# #         network.regions.remove(subnet.region)
# #
# #     return result
# #
# #
# # def deriveAfterIpRangesAdded(model: entities.Model, subnet: entities.Subnet,
# #                              ipRanges: List[rules.Ipv4Range]) -> entities.Model:
# #     """
# #     Adding some secondary ipRanges to a subnet requires the range update in the subnet, and
# #     installing subnet derivation_declarations to the network and peers.
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #
# #     parsed: Model = Model(result)
# #
# #     network = result.GetNetwork(subnet.network)
# #     subnet = result.GetSubnet(subnet.url)
# #
# #     rootRoutes: List[rules.Route] = []
# #
# #     projectName = ParseProjectFromUrl(subnet.url)
# #     for range in ipRanges:
# #         subnet.secondary_ranges.append(range)
# #
# #         hexId = genHex(16)
# #         route = rules.Route(
# #             id=genId(),
# #             name=projectName + "::default-route-" + hexId,
# #             priority=0,
# #             dest_range=range,
# #             next_hop_network=network.url,
# #             instance_filter=rules.InstanceFilter(network=network.url),
# #             url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
# #             route_type=rules.Route.RouteType.SUBNET
# #         )
# #
# #         rootRoutes.append(route)
# #
# #     # TODO propagate via BGP. depending on regional or global routing of the network.
# #     addStaticRoutesAndPropagateToPeers(parsed, rootRoutes)
# #
# #     return result
# #
# #
# # def deriveAfterIpRangeEnlarged(model: entities.Model, subnet: entities.Subnet,
# #                                ipRanges: Dict[rules.Ipv4Range, rules.Ipv4Range]) -> entities.Model:
# #     """
# #     if ranges of a subnet is enlarged, then the corresponding routes are changed accordingly
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #     parsed: Model = Model(result)
# #
# #     network = result.GetNetwork(subnet.network)
# #     subnet = result.GetSubnet(subnet.url)
# #
# #     rootRoutes: List[rules.Route] = list(filter(
# #         lambda r: r.route_type == rules.Route.RouteType.SUBNET and r.dest_range in ipRanges,
# #         network.routes))
# #
# #     for range, enlargedRange in ipRanges.items():
# #         subnet.secondary_ranges.remove(range)
# #         subnet.secondary_ranges.append(enlargedRange)
# #
# #     def enlarge(parsed, route):
# #         route.dest_range = ipRanges[route.dest_range]
# #
# #     # TODO propagate via BGP. checking regional or global routing of the network, and export subnet or not.
# #     _operateOnRoutesAndPropagate(parsed, rootRoutes, enlarge)
# #
# #     return result
# #
# #
# # def deriveAfterStaticRoutesAdded(model: entities.Model, routes: rules.Route) -> entities.Model:
# #     """
# #     Add the static route and propagate to peers
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #     parsed: Model = Model(result)
# #
# #     addStaticRoutesAndPropagateToPeers(parsed, routes)
# #
# #     return result
# #
# #
# # def deriveAfterStaticRouteUpdated(model: entities.Model, routes: rules.Route) -> entities.Model:
# #     """
# #     Update the static route and propagate to peers
# #     """
# #     result: entities.Model = entities.Model()
# #     result.CopyFrom(model)
# #     parsed: Model = Model(result)
# #
# #     urlToRoute = dict()
# #     for r in routes:
# #         urlToRoute[r.url] = r
# #
# #     rootRoutes: List[rules.Route] = list(filter(lambda r: r.url in urlToRoute, result.routes))
# #
# #     def update(parsed, route):
# #         route.CopyFrom(urlToRoute[route.url])
# #
# #     _operateOnRoutesAndPropagate(parsed, rootRoutes, update)
# #
# #     return result
