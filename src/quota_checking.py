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
from collections import defaultdict

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules
from src.utils.ip_utils import ipv4RangeToStr

"""
Maximum number of Cloud Routers per combination of VPC network and region 	5 	

Maximum number of BGP peers for each Cloud Router in a given VPC network and region 	128 	

For a given Cloud Router, maximum number of custom route advertisements per BGP session 	200 	

Maximum number of unique destinations for learned routes that can be applied to subnets in a given region 
by all Cloud Routers in the same region 	100 	

Only applicable to VPC networks in global dynamic routing mode: Maximum number of unique destinations for 
learned routes that can be applied to subnets in a given region by Cloud Routers from different regions 	100
"""

MAX_NUM_ROUTERS_PER_REGION = 5
MAX_DEST_IP_RANGES_OF_ROUTES_PER_REGION = 200

def checkCloudRouters(model):
    cloudRouters = defaultdict(lambda: [])  # network, region -> cloud router
    for router in model.cloud_routers:
        cloudRouters[router.network_uri, router.region].append(router.uri)

    for (network, region), routers in cloudRouters.items():
        if len(routers) > MAX_NUM_ROUTERS_PER_REGION:
            print("Cloud routers exceeding quota at (%s, %s): %s" % (network, region, routers))


def checkRoutes(model):
    regionToDest = defaultdict(lambda: set())  # network, region -> destination of routes, as an IP ranges

    for route in model.routes:
        if route.route_type == rules.Route.DYNAMIC:
            regionToDest[route.instance_filter.network, route.region].add(ipv4RangeToStr(route.dest_range))

    for (network, region), dests in regionToDest.items():
        if len(dests) > MAX_DEST_IP_RANGES_OF_ROUTES_PER_REGION:
            print("Dynamic routes exceeding quota at (%s, %s): %s" % (network, region, len(dests)))


def quotaChecking(func):
    def wrapper(*args, **kwargs):
        model: entities.Model = func(*args, **kwargs)

        try:
            checkCloudRouters(model)
        except:
            pass

        checkRoutes(model)

        return model

    return wrapper
