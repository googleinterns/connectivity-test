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

from unittest import *
from google.protobuf import text_format

from src.data_plane_derivation import *
from src.network_hierarchy import *
from src.utils.unittest_utils import getTrimmedRoutes

class TestDataPlane(TestCase):
    def test_deriveAfterLearnedBGP_Announcements(self):
        with open("test_data/case1/test_project_sq2_09222020_1_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        new: entities.Model = deriveAfterLearnedBGP_Announcements(
            topo,
            "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
            [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
        )

        with open("test_data/case1/test_project_sq2_09222020_2_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        generated = getTrimmedRoutes(new.routes)
        expected = getTrimmedRoutes(topo.routes)

        self.assertEqual(generated, expected)

    def test_deriveAfterCustomRoutesAdded(self):
        with open("test_data/case1/test_project_sq2_09222020_1_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        new: entities.Model = deriveAfterCustomRoutesAdded(
            topo,
            "projects/test-project-sq2/regions/us-west1/vpnTunnels/te4",
            [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
        )

        with open("test_data/case1/test_project_sq2_09222020_2_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        generated = getTrimmedRoutes(new.routes)
        expected = getTrimmedRoutes(topo.routes)

        self.assertEqual(generated, expected)

    def test_deriveAfterBGP_Revokations(self):
        with open("test_data/case1/test_project_sq2_09222020_2_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        new: entities.Model = deriveAfterBGP_Revokations(
            topo,
            "projects/test-project-sq2/regions/us-west1/vpnTunnels/te4",
            [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
        )

        with open("test_data/case1/test_project_sq2_09222020_3.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        generated = getTrimmedRoutes(new.routes)
        expected = getTrimmedRoutes(topo.routes)

        self.assertEqual(generated, expected)

    def test_deriveAfterCustomRoutesRemoved(self):
        with open("test_data/case1/test_project_sq2_09222020_2_clear.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        new: entities.Model = deriveAfterCustomRoutesRemoved(
            topo,
            "projects/test-project-sq2/regions/us-west1/vpnTunnels/t4e",
            [rules.Ipv4Range(ip=int(ipaddress.IPv4Address("10.5.0.0")), mask=24)]
        )

        with open("test_data/case1/test_project_sq2_09222020_3.pb", "r") as f:
            topo = f.read()
            topo: entities.Model = text_format.Parse(topo, entities.Model())

        generated = getTrimmedRoutes(new.routes)
        expected = getTrimmedRoutes(topo.routes)

        self.assertEqual(generated, expected)

if __name__ == '__main__':
    main()