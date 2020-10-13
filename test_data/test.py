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

from google.protobuf import text_format
import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

if __name__ == '__main__':
    with open("combined_model_0922_clear.pb", "r") as f:
        str = f.read()

        topo: entities.Model = text_format.Parse(str, entities.Model())

        copy = entities.Model()
        copy.CopyFrom(topo)

        subnet = entities.Subnet()

        subnet.id = "Deep copy"
        copy.subnets.append(subnet)
        subnet.id = "Shallow copy"

        print(copy.subnets[-1].id)
        copy.subnets[-1].id = 'Should show this'
        print(copy.subnets[-1].id)

        subnet = entities.Subnet()
        print(subnet)
        subnet2 = entities.Subnet()
        subnet2.CopyFrom(subnet)

        print(subnet.__str__(), subnet2.__str__())
        print("id" + subnet.id)

        print(copy.__str__() == topo.__str__())
