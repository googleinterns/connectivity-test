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

from ipaddress import *
from unittest import TestCase

from src.utils.ip_utils import *


class Test(TestCase):
    def test_in_ipv4range(self):
        addr: IPv4Address = ip_address("10.0.0.0")
        ipRange = rules.Ipv4Range(ip=int(addr), mask=24)

        for i in range(256):
            self.assertTrue(inIPv4Range(int(addr + i), ipRange))

        for i in range(256, 512):
            self.assertFalse(inIPv4Range(int(addr + i), ipRange))

    def test_ipv4range_to_str(self):
        addr: IPv4Address = ip_address("10.0.0.0")
        ipRange = rules.Ipv4Range(ip=int(addr), mask=24)

        self.assertTrue(ipv4RangeToStr(ipRange) == "10.0.0.0/24")
