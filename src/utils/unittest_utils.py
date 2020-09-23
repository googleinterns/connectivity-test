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
from typing import *

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

def getTrimmedRoutes(routes: List[rules.Route], keep_priority: bool = False)-> Set[str]:
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
        trimmed = rules.Route()
        trimmed.CopyFrom(route)

        trimmed.id = "id"
        trimmed.name = "name"
        trimmed.priority = route.priority if keep_priority else 0

        result.add(str(trimmed))

    return result