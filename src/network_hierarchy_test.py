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
from unittest import TestCase

from google.protobuf import text_format

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules
from src.network_hierarchy import *


# limitations under the License.
class TestDataPlane(TestCase):
    def test_parse(self):
        with open("test_data/test_project_sq2_09182020_2_ClearDep.pb", "r") as f:
            str = f.read()

            topo: entities.Model = text_format.Parse(str, entities.Model())

            model = Model(topo)

            for p in model.projects.values():
                for n in p.networks.values():
                    n.proto.name += "!!!!!!!!!!!test"

            print(topo)
            print(model)
