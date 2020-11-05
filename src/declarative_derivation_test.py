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
from src.utils.derivation_utils import getTrimmedRoutes, listBgpPeers


class TestDataPlane(unittest.TestCase):
    def common(self, fun: Callable[[entities.Model], entities.Model], pbBefore: str, pbAfter: str,
               keepPriority: bool = False):
        with open(pbBefore, "r") as f:
            before = f.read()
            before: entities.Model = text_format.Parse(before, entities.Model())

        after: entities.Model = fun(before)

        with open(pbAfter, "r") as f:
            expected = f.read()
            expected: entities.Model = text_format.Parse(expected, entities.Model())

        before = getTrimmedRoutes(before.routes, keepPriority)

        after = getTrimmedRoutes(after.routes, keepPriority)
        expected = getTrimmedRoutes(expected.routes, keepPriority)

        print("=======after=========")
        print(after)
        print("=======expected=========")
        print(expected)

        self.assertEqual(after, expected)

    def test_deriveAfterLearnedBgpAdvertisements(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterLearnedBgpAdvertisements(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
                [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
            ),
            "test_data/case1/test_project_sq2_09222020_1_clear.pb",
            "test_data/case1/test_project_sq2_09222020_2_clear.pb")

    def test_deriveAfterBgpWithdrawals(self):
        self.common(
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

        self.common(
            lambda model: derivationFunctions.deriveAfterSubnetAdded(
                model,
                subnet
            ),
            "test_data/case5/test_project_sq2_10062020_1.pb",
            "test_data/case5/test_project_sq2_10062020_2.pb")

    def test_deriveAfterSubnetRemoved(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterSubnetRemoved(
                model,
                "projects/test-project-sq2/regions/us-west2/subnetworks/added-subnet"
            ),
            "test_data/case5/test_project_sq2_10062020_2.pb",
            "test_data/case5/test_project_sq2_10062020_3.pb")

    def test_deriveAfterIpRangesAdded(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterIpRangesAdded(
                model,
                "projects/test-project-sq2/regions/us-west1/subnetworks/sn2",
                ["10.5.0.0/24"]
            ),
            "test_data/case6/test_project_sq2_10022020_1.pb",
            "test_data/case6/test_project_sq2_10022020_2.pb")

    def test_deriveAfterIpRangeEnlarged(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterIpRangeEnlarged(
                model,
                "projects/test-project-sq2/regions/us-west1/subnetworks/sn2",
                [("10.5.0.0/24", "10.5.0.0/20")]
            ),
            "test_data/case7/test_project_sq2_10052020_1.pb",
            "test_data/case7/test_project_sq2_10052020_2.pb")

    def test_deriveAfterStaticRouteAdded(self):
        self.common(
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

        self.common(
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
        self.common(
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

    def test_deriveAfterStaticRouteUpdated(self):
        route = text_format.Parse("""
                          id: "6567481593092089925"
                          name: "test-project-sq2::added-route"
                          priority: 1000
                          dest_range {
                            ip: 168165376
                            mask: 24
                          }
                          next_hop_ip: 167903235
                          instance_filter {
                            network: "projects/test-project-sq2/global/networks/n2"
                          }
                          url: "projects/test-project-sq2/global/routes/added-route"
                          route_type: STATIC
                  """, rules.Route())
        newRoute = text_format.Parse("""
                          id: "7724679716894648665"
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
        self.common(
            lambda model: derivationFunctions.deriveAfterStaticRouteUpdated(
                model,
                [(route, newRoute)]
            ),
            "test_data/case9.1/test_project_sq2_10132020_1.pb",
            "test_data/case9.1/test_project_sq2_10132020_2.pb")

        self.common(
            lambda model: derivationFunctions.deriveAfterStaticRouteUpdated(
                model,
                [(newRoute, route)]
            ),
            "test_data/case9.1/test_project_sq2_10132020_2.pb",
            "test_data/case9.1/test_project_sq2_10132020_3.pb")

    def test_deriveAfterVpnTunnelRemoval(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterVpnTunnelRemoval(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e"
            ),
            "test_data/case2/test_project_sq2_10132020_1.pb",
            "test_data/case2/test_project_sq2_10132020_2.pb")

        self.common(
            lambda model: derivationFunctions.deriveAfterVpnTunnelRemoval(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e-2"
            ),
            "test_data/case2/test_project_sq2_10132020_2.pb",
            "test_data/case2/test_project_sq2_10132020_3.pb")

    def test_deriveAfterBgpMedChanged(self):
        self.common(
            lambda model: derivationFunctions.deriveAfterBgpMedChanged(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
                50
            ),
            "test_data/case4/test_project_sq2_10062020_1.pb",
            "test_data/case4/test_project_sq2_10062020_2.pb",
            keepPriority=True)

        self.common(
            lambda model: derivationFunctions.deriveAfterBgpMedChanged(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
                -50
            ),
            "test_data/case4/test_project_sq2_10062020_2.pb",
            "test_data/case4/test_project_sq2_10062020_3.pb",
            keepPriority=True)

        self.common(
            lambda model: derivationFunctions.deriveAfterBgpMedChanged(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/te4-2",
                200
            ),
            "test_data/case4/test_project_sq2_10202020_router_s4_1.pb",
            "test_data/case4/test_project_sq2_10202020_router_s4_2.pb",
            keepPriority=True)

        self.common(
            lambda model: derivationFunctions.deriveAfterBgpMedChanged(
                model,
                "projects/test-project-sq2/regions/us-west1/vpnTunnels/te4-2",
                -200
            ),
            "test_data/case4/test_project_sq2_10202020_router_s4_2.pb",
            "test_data/case4/test_project_sq2_10202020_router_s4_3.pb",
            keepPriority=True)

    def test_integrity(self):
        with open("test_data/big_project/big_project.pb", "r") as f:
            model = f.read()
            model: entities.Model = text_format.Parse(model, entities.Model())

        rootRoutes = derivationFunctions.IdentifyRootRoutes(model)

        for root in rootRoutes:
            self.assertEqual(derivationFunctions.FindRootRoute(model, root), root)

        derived = derivationFunctions.Derive(model, rootRoutes)

        for routes in derived:
            for route in routes:
                self.assertTrue(route not in rootRoutes)

        flatten = lambda x: [y for l in x for y in flatten(l)] if type(x) is list else [x]

        allRoutes = rootRoutes + flatten(derived)

        self.assertEqual(getTrimmedRoutes(allRoutes), getTrimmedRoutes(model.routes))


    def test_findRoot(self):
        with open("test_data/big_project/big_project.pb", "r") as f:
            model = f.read()
            model: entities.Model = text_format.Parse(model, entities.Model())

        root = derivationFunctions.FindRootRoute(model, text_format.Parse(
            """
              id: "3679872279986331686"
              name: "reachability-e2e-test::peering-route-1426b0141f4aa8cc"
              priority: 0
              dest_range {
                ip: 167903488
                mask: 24
              }
              instance_filter {
                network: "projects/reachability-e2e-test/global/networks/peer1-route-test"
              }
              next_hop_peering: "reachability-e2e-test::peer1-route-test::peering1-peer-route-test"
              url: "projects/reachability-e2e-test/global/routes/peering-route-1426b0141f4aa8cc"
              route_type: PEERING_SUBNET
              creation_timestamp: "2019-11-11T14:20:57.515-08:00"
              description: "Auto generated route via peering [peering1-peer-route-test]."
            """, rules.Route()))

        self.assertEqual(root, text_format.Parse("""
            id: "6980694499687424046"
            name: "reachability-e2e-test::default-route-b56e783807428e99"
            priority: 0
            dest_range {
              ip: 167903488
              mask: 24
            }
            next_hop_network: "projects/reachability-e2e-test/global/networks/route-test"
            instance_filter {
              network: "projects/reachability-e2e-test/global/networks/route-test"
            }
            url: "projects/reachability-e2e-test/global/routes/default-route-b56e783807428e99"
            route_type: SUBNET
            creation_timestamp: "2019-11-11T14:20:49.544-08:00"
            description: "Default local route to the subnetwork 10.2.1.0/24."
        """, rules.Route()))

    def test_derive(self):
        def justDerive(_model: entities.Model) -> entities.Model:
            model: entities.Model = entities.Model()
            model.CopyFrom(_model)

            derivationFunctions.Derive(model)
            return model

        self.common(
            justDerive,
            "test_data/big_project/big_project_partial.pb",
            "test_data/big_project/big_project.pb",
            keepPriority=False)


if __name__ == '__main__':
    unittest.main()
