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
from typing import List, Any, Dict, Callable

from google.protobuf import text_format

import proto.cloud_network_model_pb2 as entities
import proto.derivation_rules_pb2 as derivation_rules
import proto.rules_pb2 as rules
from src.utils.derivation_utils import findNetwork, listBgpPeers, REGION_LIST, toCamelCase, getTrimmedRoutes, trimRoute

Destination = derivation_rules.DestinationAndGeneration.Destination
DestinationContext = derivation_rules.DestinationAndGeneration.DestinationContext

"""
Upon a configuration change from the customer or automation systems, the data plane is updated according to a series
of derivation_rules, among which the route tables are more complex because a single change to a route may be propagated to other 
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
# rule files are optionally ordered by the optional `src/derivation_rules/rule_order.pb` file
DerivationRules: List[derivation_rules.RouteDerivationRule] = []

# collects the route generators from the configuration files.
# the keys are the function names, and two functions should never share a name.
# the values are the functions. They modify the input route, and also return the same route.
RouteGenerators: Dict[str, Callable[[rules.Route, Any, entities.Model], rules.Route]] = defaultdict(
    lambda route, context, model: route
)


def initializeDerivationRules():
    global DerivationRules
    for dirName, subdirList, fileList in os.walk("src/derivation_rules"):
        print('Loading derivation rules in directory: %s. ' % dirName)

        try:
            with open("src/derivation_rules/rule_order.pb", "r") as f:
                fileContent = f.read()
                order = list(text_format.Parse(fileContent, derivation_rules.RuleOrder()).file_names)
        except FileNotFoundError:
            order = []

        def index(e) -> int:
            try:
                return order.index(e)
            except ValueError:
                return len(order)

        # sort the rule files such that the mentioned files appear in the specified order and before the unmentioned files
        sortedList = sorted(fileList, key=index)

        for fname in sortedList:
            if not fname.endswith(".pb") or fname == "rule_order.pb": continue
            print('Parsing derivation rules in file: %s' % fname)

            with open(os.path.join(dirName, fname), "r") as f:
                fileContent = f.read()
                rules = text_format.Parse(fileContent, derivation_rules.RuleSet())

            DerivationRules += rules.rules


initializeDerivationRules()


def initializeRouteGenerators():
    global RouteGenerators

    for dirName, subdirList, fileList in os.walk("src/derivation_rules"):
        print('Loading route generators in directory: %s. ' % dirName)

        for fname in fileList:
            if not fname.endswith(".py"): continue
            print('Parsing the route generator in file: %s' % fname)
            module = importlib.import_module("src.derivation_rules." + fname[:-3])

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

     - VPC_PEERS_CUSTOM_ROUTING:     all active peering VPC networks that import custom routes from the route's network,
                                    and simultaneously, the route's network export custom routes to them

     - BGP_PEERS_GLOBAL_ROUTING:     all networks that establish a *live* BGP session to the route's network, and they
                                    are in global routing mode

     - BGP_PEERS_REGIONAL_ROUTING:   all networks that establish a *live* BGP session to the route's network, and they
                                    are in regional routing mode
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
    elif destination in [Destination.BGP_PEERS_GLOBAL_ROUTING, Destination.BGP_PEERS_REGIONAL_ROUTING]:
        for network, region, peer_info in listBgpPeers(model, network.url):

            regions = [region]
            if destination == Destination.BGP_PEERS_GLOBAL_ROUTING:
                regions = REGION_LIST

            for region in regions:
                res.append(DestinationContext(network=network.url, region=region, peer_info=peer_info))
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
