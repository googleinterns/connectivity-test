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
from src.utils.derivation_utils import genId, genHex

Destination = derivation.DestinationAndGeneration.Destination
DestinationContext = derivation.DestinationAndGeneration.DestinationContext


def CommonGenerator(derived: rules.Route, context: DestinationContext, model: entities.Model) -> rules.Route:
    derived.id = genId()
    derived.name = "derived [%s]" % derived.id
    derived.instance_filter.network = context.network
    derived.ClearField("from_local")
    return derived
