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

from src.network_hierarchy import *


# limitations under the License.
class TestDataPlane(TestCase):
    def test_parse(self):
        with open("test_data/combined_model_09022020.pb", "r") as f:
            str = f.read()

            topo: model.Project = text_format.Parse(str, model.Project())

            dp = DataPlane()
            dp.Parse(topo)

            print(topo)
