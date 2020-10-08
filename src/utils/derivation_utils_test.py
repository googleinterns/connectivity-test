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
from src.utils.derivation_utils import toCamelCase


# limitations under the License.
class TestDevrivationUtils(TestCase):
    def test_snake_to_camel(self):
        self.assertEqual(toCamelCase("__COMMON"), "Common")
        self.assertEqual(toCamelCase("VPC_PEERS_NO_CUSTOM_ROUTING"), "VpcPeersNoCustomRouting")

        self.assertEqual(toCamelCase("VPC-PEERS_NO_CUSTOM_ROUTING"), "VpcPeersNoCustomRouting")

        self.assertEqual(toCamelCase("RuleName_VPC_PEERS_NO_CUSTOM_ROUTING"), "RuleNameVpcPeersNoCustomRouting")
        self.assertEqual(toCamelCase("Rule_Name_VPC_PEERS_NO_CUSTOM_ROUTING"), "RuleNameVpcPeersNoCustomRouting")
