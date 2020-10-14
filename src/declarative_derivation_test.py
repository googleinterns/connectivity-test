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
import ipaddress
import unittest
from typing import Callable

from google.protobuf import text_format

from google.protobuf import text_format

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

import src.declarative_derivation as derivationFunctions
from src.utils.derivation_utils import getTrimmedRoutes


class TestDataPlane(unittest.TestCase):
    def test(self, fun: Callable[[entities.Model], entities.Model], pbBefore: str, pbAfter: str):
        with open(pbBefore, "r") as f:
            before = f.read()
            before: entities.Model = text_format.Parse(before, entities.Model())

        after: entities.Model = fun(before)

        with open(pbAfter, "r") as f:
            expected = f.read()
            expected: entities.Model = text_format.Parse(expected, entities.Model())

        before = getTrimmedRoutes(before.routes)

        after = getTrimmedRoutes(after.routes)
        expected = getTrimmedRoutes(expected.routes)

        print("=======after=========")
        print(after)
        print("=======expected=========")
        print(expected)

        self.assertEqual(after, expected)

    def test_deriveAfterLearnedBgpAdvertisements(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterLearnedBgpAdvertisements(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
                [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
            ),
            "test_data/case1/test_project_sq2_09222020_1_clear.pb",
            "test_data/case1/test_project_sq2_09222020_2_clear.pb")

    def test_deriveAfterBgpWithdrawals(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterBgpWithdrawals(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
                [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
            ),
            "test_data/case1/test_project_sq2_09222020_2_clear.pb",
            "test_data/case1/test_project_sq2_09222020_3.pb")

    def test_deriveAfterSubnetAdded(self):
        subnetStr = """
          id: "1955644932992580395"
          name: "test-project-sq2::added-subnet"
          network: "projects/test-project-sq2/global/networks/n2"
          ipv4_range {
            ip: 168689664
            mask: 24
          }
          region: "us-west2"
          secondary_ranges {
            ip: 168755200
            mask: 24
          }
          private_google_access: false
          url: "projects/test-project-sq2/regions/us-west2/subnetworks/added-subnet"
        """

        subnet: entities.Subnet = text_format.Parse(subnetStr, entities.Subnet())

        self.test(
            lambda model: derivationFunctions.deriveAfterSubnetAdded(
                model,
                subnet
            ),
            "test_data/case5/test_project_sq2_10062020_1.pb",
            "test_data/case5/test_project_sq2_10062020_2.pb")

    def test_deriveAfterSubnetRemoved(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterSubnetRemoved(
                model,
                "projects/test-project-sq2/regions/us-west2/subnetworks/added-subnet"
            ),
            "test_data/case5/test_project_sq2_10062020_2.pb",
            "test_data/case5/test_project_sq2_10062020_3.pb")

    def test_deriveAfterIpRangesAdded(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterIpRangesAdded(
                model,
                "projects/test-project-sq2/regions/us-west1/subnetworks/sn2",
                ["10.5.0.0/24"]
            ),
            "test_data/case6/test_project_sq2_10022020_1.pb",
            "test_data/case6/test_project_sq2_10022020_2.pb")

    def test_deriveAfterIpRangeEnlarged(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterIpRangeEnlarged(
                model,
                "projects/test-project-sq2/regions/us-west1/subnetworks/sn2",
                {"10.5.0.0/24": "10.5.0.0/20"}
            ),
            "test_data/case7/test_project_sq2_10052020_1.pb",
            "test_data/case7/test_project_sq2_10052020_2.pb")

    def test_deriveAfterStaticRouteAdded(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterStaticRouteAdded(
                model,
                text_format.Parse("""
                  id: "6845072321650144437"
                  name: "test-project-sq2::added-static-route"
                  priority: 1000
                  dest_range {
                    ip: 168230912
                    mask: 24
                  }
                  next_hop_ip: 167903235
                  instance_filter {
                    network: "projects/test-project-sq2/global/networks/n2"
                  }
                  url: "projects/test-project-sq2/global/routes/added-static-route"
                  route_type: STATIC
                  """, rules.Route())
            ),
            "test_data/case8.0/test_project_sq2_10062020_1.pb",
            "test_data/case8.0/test_project_sq2_10062020_2.pb")

        self.test(
            lambda model: derivationFunctions.deriveAfterStaticRouteAdded(
                model,
                text_format.Parse("""
                  id: "7345242974329025248"
                  name: "test-project-sq2::added-route"
                  priority: 1000
                  dest_range {
                    ip: 168165376
                    mask: 24
                  }
                  next_hop_ip: 167903235
                  instance_filter {
                    network: "projects/test-project-sq2/global/networks/n2"
                    attributes {
                      tag: "test-tag"
                    }
                  }
                  url: "projects/test-project-sq2/global/routes/added-route"
                  route_type: STATIC
                  """, rules.Route())
            ),
            "test_data/case8.2/test_project_sq2_10062020_1.pb",
            "test_data/case8.2/test_project_sq2_10062020_2.pb")

    def test_deriveAfterStaticRouteRemoved(self):
        self.test(
            lambda model: derivationFunctions.deriveAfterStaticRouteRemoved(
                model,
                text_format.Parse("""
                  id: "6845072321650144437"
                  name: "test-project-sq2::added-static-route"
                  priority: 1000
                  dest_range {
                    ip: 168230912
                    mask: 24
                  }
                  next_hop_ip: 167903235
                  instance_filter {
                    network: "projects/test-project-sq2/global/networks/n2"
                  }
                  url: "projects/test-project-sq2/global/routes/added-static-route"
                  route_type: STATIC
                  """, rules.Route())
            ),
            "test_data/case8.0/test_project_sq2_10062020_2.pb",
            "test_data/case8.0/test_project_sq2_10062020_3.pb")


if __name__ == '__main__':
    unittest.main()
