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

import proto.rules_pb2 as rules
import ipaddress


def inIPv4Range(ip: int, ipRange: rules.Ipv4Range) -> bool:
    """
    Determine if the IP address ip is in the IP range.
    """

    if ipRange.mask > 32 or ipRange.mask < 0:
        raise ValueError("The mask of ipRange is invalid: %d. Should be in [0,32]" % ipRange.mask)

    mask = ~((1 << (32 - ipRange.mask)) - 1)
    return ipRange.ip & mask == ip & mask


def ipv4RangeToStr(ipRange: rules.Ipv4Range) -> str:
    return str(ipaddress.IPv4Address(ipRange.ip)) + "/%d" % ipRange.mask
