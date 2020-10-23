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
from typing import List, Any, Dict, Callable, Union, Tuple

from google.protobuf import text_format

import proto.cloud_network_model_pb2 as entities
import proto.derivation_rules_pb2 as derivation
import proto.rules_pb2 as rules
from src.derivation_declarations.generators.root_generator import CommonGenerator
from src.utils.derivation_utils import findNetwork, listBgpPeers, REGION_LIST, toCamelCase, getTrimmedRoutes, trimRoute, \
    findVpnTunnel, findNetworkForVpnTunnel, findVpnGateway, genId, genHex, findSubnet, findPeeringVpnTunnel
from src.utils.ip_utils import ipv4StrToRange
from src.utils.url_parsers import ParseProjectFromUrl

Destination = derivation.DestinationAndGeneration.Destination
DestinationContext = derivation.DestinationAndGeneration.DestinationContext

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
DerivationRules: List[derivation.RouteDerivationRule] = []

# collects the route generators from the configuration files.
# the keys are the function names, and two functions should never share a name.
# the values are the functions. They modify the input route, and also return the same route.
RouteGenerators: Dict[str, Callable[[rules.Route, Any, entities.Model], rules.Route]] = defaultdict(
    lambda route, context, model: route
)


def initializeDerivationRules():
    """
    Load the derivation rule files and parse the rules.
    The order of rules are organized by the optional **rule_order.pb** file.
    """
    global DerivationRules
    try:
        with open("src/derivation_declarations/rule_order.pb", "r") as f:
            fileContent = f.read()
            order = list(text_format.Parse(fileContent, derivation.RuleOrder()).file_names)
    except FileNotFoundError:
        order = []

    def index(e) -> int:
        try:
            return order.index(e)
        except ValueError:
            return len(order)

    for dirName, subdirList, fileList in os.walk("src/derivation_declarations/rules"):
        print('Loading derivation rules in directory: %s ' % dirName)

        # sort the rule files such that the mentioned files appear in the specified order and before the unmentioned files
        sortedList = sorted(fileList, key=index)

        for fname in sortedList:
            if not fname.endswith(".pb"): continue
            print('Parsing derivation rules in file: %s' % fname)

            with open(os.path.join(dirName, fname), "r") as f:
                fileContent = f.read()
                rules = text_format.Parse(fileContent, derivation.RuleSet())

            DerivationRules += rules.rules


initializeDerivationRules()


def initializeRouteGenerators():
    """
    Load the generation function files and parse the generators.
    The optional **enabled_generators.pb** file provides a way to explicitly designate the desired files to parse
    instead of all files.
    """
    global RouteGenerators

    try:
        with open("src/derivation_declarations/enabled_generators.pb", "r") as f:
            fileContent = f.read()
            filesToRead = list(text_format.Parse(fileContent, derivation.EnabledGenerators()).file_names)
    except FileNotFoundError:
        filesToRead = []

    for dirName, subdirList, fileList in os.walk("src/derivation_declarations/generators"):
        print('Loading route generators in directory: %s ' % dirName)

        if filesToRead:
            filesToRead = set(fileList).intersection(filesToRead)
        else:
            filesToRead = fileList

        for fname in filesToRead:
            if not fname.endswith(".py"): continue
            print('Parsing the route generator in file: %s' % fname)
            module = importlib.import_module("src.derivation_declarations.generators." + fname[:-3])

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

    notRoot = []  # notRoot = set()  # Error due to "unhashable object"

    for route in model.routes:
        if route in notRoot: continue
        for derived in FindDerivedRoutes(model, route):
            notRoot.append(derived)

    return res


def FindRootRoute(model: entities.Model, derived: rules.Route) -> rules.Route:
    """
    Return the root route of the input route. The input route itself may be its root route.
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


def match(route: rules.Route, rule: derivation.RouteDerivationRule) -> bool:
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

    for lambdaFilter in rule.lambda_filters:
        try:
            fun = eval("lambda route: %s" % lambdaFilter.replace("```", ""))
            if fun(route): return True
        except Exception as e:
            print("Error occurred in lambda filter: %s\n%s" % (lambdaFilter, e))
            pass

    return False


def getContexts(route: rules.Route, destination: Destination, model: entities.Model) -> List[DestinationContext]:
    """
    Get the contexts to install new routes, based on the given destination.
    The destination is configurable in the rule files and has fixed builtin semantics -- the returned context type is
    defined by the destination:

     - VPC_PEERS:                   all active peering VPC networks

     - VPC_PEERS_CUSTOM_ROUTING:    all active peering VPC networks that import custom routes from the route's network,
                                    and simultaneously, the route's network export custom routes to them.

     - REGIONS_OF_VPC_PEERS_CUSTOM_ROUTING: all active peering VPC networks that import custom routes from the route's network,
                                    and simultaneously, the route's network export custom routes to them. The route is
                                    exported to all regions that the network has presence.

     - BGP_PEERS:                   all networks that hold one or more *live* BGP session with the current network, and
                                    the involved VPN tunnels in current network enable subnet advertising

     - OTHER_REGIONS_WHEN_GLOBAL_ROUTING:   all regions other than the route's region, when the current network enables
                                            global routing
    """
    res: List[DestinationContext] = []
    network = findNetwork(model, route.instance_filter.network)

    if network is None: return []

    if destination in [Destination.VPC_PEERS,
                       Destination.VPC_PEERS_CUSTOM_ROUTING,
                       Destination.REGIONS_OF_VPC_PEERS_CUSTOM_ROUTING]:
        for peer in network.peers:
            peerNetwork = findNetwork(model, peer.peer_network)
            if not peerNetwork: continue

            peerInPeer = next((peer for peer in peerNetwork.peers if peer.peer_network == network.url), None)
            if not peerInPeer: continue

            if entities.NetworkPeer.NetworkPeeringState.INACTIVE in [peer.state, peerInPeer.state]: continue

            if destination == Destination.VPC_PEERS:
                res.append(DestinationContext(network=peerNetwork.url, peer_info=peerInPeer.name))
            else:
                if not (peer.export_custom_routes and peerInPeer.import_custom_routes): continue

                if destination == Destination.VPC_PEERS_CUSTOM_ROUTING:
                    res.append(DestinationContext(network=peerNetwork.url, peer_info=peerInPeer.name))
                else:
                    for region in peerNetwork.regions:
                        res.append(
                            DestinationContext(network=peerNetwork.url, region=region, peer_info=peerInPeer.name))
    elif destination == Destination.BGP_PEERS:
        for network, region, peer_info in listBgpPeers(model, network.url):
            res.append(DestinationContext(network=network.url, region=region, peer_info=peer_info))
    elif destination == Destination.OTHER_REGIONS_WHEN_GLOBAL_ROUTING:
        if network.routing_mode == entities.Network.RoutingMode.GLOBAL:
            for region in REGION_LIST:
                if region == route.region: continue
                res.append(DestinationContext(network=network.url, region=region, peer_info=route.next_hop_tunnel))
    else:
        raise ValueError("The destination is not supported: ", destination)

    return res


def deriveRoute(model: entities.Model, route: rules.Route, context: DestinationContext, destination: Destination,
                extraRule: str = None) -> rules.Route:
    """
    A single derivation, based on the current model, the route generating the derived route, the context and destination
    for the generation.
    If the matching generation rule has a name, then the specified generation rule with name extraRule is further loaded
    and applied after all other rules.
    """
    dstName = Destination.DESCRIPTOR.values_by_number[destination].name
    functionNames = ["COMMON", dstName, ]

    derived = rules.Route()
    derived.CopyFrom(route)
    for functionName in functionNames + (["%s_%s" % (extraRule, dstName)] if extraRule else []):
        functionName = toCamelCase(functionName)
        f = RouteGenerators[functionName]
        f(derived, context, model)
    return derived


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

    def dfs(start_routes: List[rules.Route], depth: int = 0) -> List[List[rules.Route]]:
        def printWithDepth(s: str):
            for line in s.split("\n"):
                print(("| " * depth) + line)

        res = [[] for route in start_routes]

        for i, route in enumerate(start_routes):
            rule: derivation.RouteDerivationRule = None
            for _rule in DerivationRules:
                if match(route, _rule):
                    rule = _rule
                    break

            if rule is None:
                printWithDepth("Route not covered by any rules: \n" + str(rule))
                continue

            printWithDepth("========Derive Route==========\n%s" % str(route))
            printWithDepth("--------Matched Rule----------\n%s" % str(rule))

            # from this line, destination is never VPC_PEERS
            for destination in rule.destinations:
                printWithDepth("------------Destination-------------\n%s" % (str(destination)))
                contexts = getContexts(route, destination.destination, model)

                printWithDepth("------------Contexts-------------\n%s" % (str(contexts)))
                for context in contexts:
                    printWithDepth("------------Context-------------\n%s" % (str(context)))
                    derived = deriveRoute(model, route, context, destination.destination, rule.name)
                    printWithDepth("---------Derived Route----------\n%s" % (str(derived)))

                    res[i].append(derived)

                    returned = dfs([derived], depth + 1)
                    res[i] += returned[0]
        return res

    res = dfs(start_routes)

    routeIndex = defaultdict(lambda: None)  # from string of the trimmed route to the original route

    for route in model.routes:
        trimmed = trimRoute(route)
        routeIndex[str(trimmed)] = route  # use string because the protobuf itself is not hashable.

    for i in range(len(res)):
        derivedRoutes = res[i]

        for j in range(len(derivedRoutes)):
            route = derivedRoutes[j]
            trimmed = trimRoute(route)
            matched = routeIndex[str(trimmed)]
            if matched:
                res[i][j] = matched
            elif __insert_missing_derived_routes:
                model.routes.append(route)
    return res


def deriveAfterLearnedBgpAdvertisements(_model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str],
                                        prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    Some prefixes are advertised from the other end of the vpnTunnel. Derive routes based on that.
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(vpnTunnel, str):
        vpnTunnel = findVpnTunnel(model, vpnTunnel)

    gw = findVpnGateway(model, vpnTunnel.vpn_gateway)
    context = DestinationContext(network=gw.network, region=vpnTunnel.region, peer_info=vpnTunnel.url)

    routes = []

    for prefix in prefixes:
        # reuse the existing route construction functions to build a correct route
        route = rules.Route(dest_range=prefix, region=vpnTunnel.region)
        route = deriveRoute(model, route, context, Destination.BGP_PEERS)

        routes.append(route)
        model.routes.append(route)

    Derive(model, routes)

    return model


def deriveAfterBgpWithdrawals(_model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str],
                              prefixes: List[rules.Ipv4Range]) -> entities.Model:
    """
    When the other side withdrawals some prefixes, delete the derived routes on this side.
    The prefixes should be existing, and no prefix aggregation is done inside this function.
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(vpnTunnel, str):
        vpnTunnel = findVpnTunnel(model, vpnTunnel)

    routes = []
    for route in model.routes:
        if route.next_hop_tunnel == vpnTunnel.url and route.dest_range in prefixes and route.from_local:
            routes.append(route)

    print("===========Root Routes============")
    print(routes)

    for route in routes:
        derivedRoutes = FindDerivedRoutes(model, route)
        print("===========Deleting Routes============")
        derivedRoutes.append(route)
        print(derivedRoutes)
        for r in derivedRoutes:
            model.routes.remove(r)

    return model


def deriveAfterSubnetAdded(_model: entities.Model, subnet: entities.Subnet) -> entities.Model:
    """
    When a subnet is added, the associated subnet route is created and propagated
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    network = findNetwork(model, subnet.network)
    projectName = ParseProjectFromUrl(subnet.url)

    model.subnets.append(subnet)
    if subnet.region not in network.regions:
        network.regions.append(subnet.region)

    routes = []

    prefixes = [subnet.ipv4_range] + list(subnet.secondary_ranges)
    for prefix in prefixes:
        hexId = genHex(16)
        # reuse the existing route construction functions to build a correct route
        route = rules.Route(
            id=genId(),
            name=projectName + "::default-route-" + hexId,
            priority=0,
            dest_range=prefix,
            next_hop_network=network.url,
            instance_filter=rules.InstanceFilter(network=network.url),
            url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
            route_type=rules.Route.RouteType.SUBNET
        )

        routes.append(route)
        model.routes.append(route)

    Derive(model, routes)

    return model


def deriveAfterSubnetRemoved(_model: entities.Model, subnet: Union[entities.Subnet, str]) -> entities.Model:
    """
    the pre-requisite is that no instance is under this subnet.
    remove subnet routes in this network and peers, and remove a region, if this subnet is the last in the region
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(subnet, str):
        subnet = findSubnet(model, subnet)

    network = findNetwork(model, subnet.network)
    prefixes = [subnet.ipv4_range] + list(subnet.secondary_ranges)

    model.subnets.remove(subnet)
    if not next((s for s in model.subnets if s.network == subnet.network and s.region == subnet.region), None):
        network.regions.remove(subnet.region)

    routes = []
    for route in model.routes:
        if route.route_type == rules.Route.RouteType.SUBNET and route.dest_range in prefixes:
            routes.append(route)

    print("===========Root Routes============")
    print(routes)

    for route in routes:
        derivedRoutes = FindDerivedRoutes(model, route)
        print("===========Deleting Routes============")
        derivedRoutes.append(route)
        print(derivedRoutes)
        for r in derivedRoutes:
            model.routes.remove(r)

    return model


def deriveAfterIpRangesAdded(_model: entities.Model, subnet: Union[entities.Subnet, str],
                             ipRanges: List[Union[rules.Ipv4Range, str]]) -> entities.Model:
    """
    Adding some secondary ipRanges to a subnet requires the range update in the subnet, and
    installing subnet derivation_rules to the network and peers.
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(subnet, str):
        subnet = findSubnet(model, subnet)

    network = findNetwork(model, subnet.network)
    projectName = ParseProjectFromUrl(subnet.url)

    routes = []

    for prefix in ipRanges:
        if isinstance(prefix, str):
            prefix = ipv4StrToRange(prefix)

        subnet.secondary_ranges.append(prefix)

        hexId = genHex(16)
        # reuse the existing route construction functions to build a correct route
        route = rules.Route(
            id=genId(),
            name=projectName + "::default-route-" + hexId,
            priority=0,
            dest_range=prefix,
            next_hop_network=network.url,
            instance_filter=rules.InstanceFilter(network=network.url),
            url="projects/%s/global/routes/default-route-%s" % (projectName, hexId),
            route_type=rules.Route.RouteType.SUBNET
        )

        routes.append(route)
        model.routes.append(route)

    Derive(model, routes)

    return model


def deriveAfterIpRangeEnlarged(_model: entities.Model, subnet: Union[entities.Subnet, str],
                               ipRanges: List[Tuple[Union[rules.Ipv4Range, str], Union[rules.Ipv4Range, str]]]
                               ) -> entities.Model:
    """
    If ranges of a subnet is enlarged, then the corresponding routes are changed accordingly
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(subnet, str):
        subnet = findSubnet(model, subnet)

    for range, enlargedRange in ipRanges:
        if isinstance(range, str):
            range = ipv4StrToRange(range)
        if isinstance(enlargedRange, str):
            enlargedRange = ipv4StrToRange(enlargedRange)

        if subnet.ipv4_range == range:
            subnet.ipv4_range = enlargedRange
        else:  # this branch is not possible in the web interface. But we support this
            subnet.secondary_ranges.remove(range)
            subnet.secondary_ranges.append(enlargedRange)

        for route in model.routes:
            if route.route_type == rules.Route.RouteType.SUBNET and route.dest_range == range:
                derivedRoutes = FindDerivedRoutes(model, route)

                print("===========Enlarging Routes============")
                derivedRoutes.append(route)
                print(derivedRoutes)
                for r in derivedRoutes:
                    r.dest_range.ip = enlargedRange.ip
                    r.dest_range.mask = enlargedRange.mask

                break

    return model


def deriveAfterStaticRouteAdded(_model: entities.Model, route: rules.Route) -> entities.Model:
    """
    Add the static route and propagate to peers
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    model.routes.append(route)

    Derive(model, [route])

    return model


def deriveAfterStaticRouteRemoved(_model: entities.Model, route: rules.Route) -> entities.Model:
    """
    Remove the static route and propagate to peers
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    for r in FindDerivedRoutes(model, route):
        model.routes.remove(r)
    model.routes.remove(route)

    return model


def deriveAfterStaticRouteUpdated(model: entities.Model,
                                  routes: List[Tuple[rules.Route, rules.Route]]) -> entities.Model:
    """
    Update the static route and propagate to peers
    """

    for route, newRoute in routes:
        model = deriveAfterStaticRouteRemoved(model, route)
        model = deriveAfterStaticRouteAdded(model, newRoute)

    return model


def deriveAfterVpnTunnelRemoval(_model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str]) -> entities.Model:
    """
    Will remove all learned routes and custom prefixes on this side, and propagate the removal
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(vpnTunnel, str):
        vpnTunnel = findVpnTunnel(model, vpnTunnel)

    peerTunnel = findPeeringVpnTunnel(model, vpnTunnel)
    if not peerTunnel: return model

    routes = list(filter(lambda r: r.next_hop_tunnel in [vpnTunnel.url, peerTunnel.url] and r.from_local, model.routes))

    for route in routes:
        derivedRoutes = FindDerivedRoutes(model, route)
        print("===========Deleting Routes============")
        derivedRoutes.append(route)
        print(derivedRoutes)
        for r in derivedRoutes:
            model.routes.remove(r)

    model.vpn_tunnels.remove(vpnTunnel)

    return model


def deriveAfterBgpMedChanged(_model: entities.Model, vpnTunnel: Union[entities.VPNTunnel, str],
                             MED_diff: int) -> entities.Model:
    """
    Will change the priority of all influenced routes, and propagate
    """
    model: entities.Model = entities.Model()
    model.CopyFrom(_model)

    if isinstance(vpnTunnel, str):
        vpnTunnel = findVpnTunnel(model, vpnTunnel)

    peerTunnel = findPeeringVpnTunnel(model, vpnTunnel)
    if not peerTunnel: return model

    routes = list(filter(lambda r: r.next_hop_tunnel == peerTunnel.url and r.from_local, model.routes))

    for route in routes:
        derivedRoutes = FindDerivedRoutes(model, route)
        print("===========Changing Priorities of Routes============")
        derivedRoutes.append(route)
        print(derivedRoutes)
        for r in derivedRoutes:
            r.priority += MED_diff

    return model
