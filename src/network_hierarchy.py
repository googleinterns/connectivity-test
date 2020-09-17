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
from dataclasses import dataclass, field
from typing import *

import proto.data_plane_pb2 as model
import proto.rules_pb2 as rules
# import proto.cloud.cluster.network.reachability as model
# import proto.cloud.cluster.network.reachability as rules

from src.utils.url_parsers import *
from src.utils.ip_utils import *

"""
The networks, instances, subnets given in the protobuf are flattened and this module reconstruct the hierarchy
e.g., putting the subnets under regions and projects. 

The classes defined here are only a hierarchical index for easier access to the network topology, and do not contain any
dataplane states, e.g. if the network peer is active, and if the peer enables importing/exporting custom routes. 

Full hierarchy:
    - Internet > Project > VPC Network <> Region
    - Region > Subnet > IP Range
    - Region > VM > NIC
    - Region > Cloud Router, VPN gateway
"""

REGION_LIST = [
    "asia-east1",
    "asia-east2",
    "asia-northeast1",
    "asia-northeast2",
    "asia-northeast3",
    "asia-south1",
    "asia-southeast1",
    "asia-southeast2",
    "australia-southeast1",
    "europe-north1",
    "europe-west1",
    "europe-west2",
    "europe-west3",
    "europe-west4",
    "europe-west6",
    "northamerica-northeast1",
    "southamerica-east1",
    "us-central1",
    "us-central2",
    "us-east1",
    "us-east4",
    "us-west1",
    "us-west2",
    "us-west3",
    "us-west4"
]


@dataclass
class Vm:
    proto: model.Instance  #
    nics: Dict[str, model.NetworkInterface] = field(default_factory=dict)  #


@dataclass
class Subnet:
    proto: model.Subnet  #
    ipRanges: List[rules.Ipv4Range] = field(default_factory=list)  #
    # nics: Dict[str, model.NetworkInterface] = field(default_factory=dict)


@dataclass
class RegionOfNetwork:
    network: 'Network'  #
    subnets: Dict[str, Subnet] = field(default_factory=dict)  #
    vms: Dict[str, Vm] = field(default_factory=dict)  #


@dataclass
class Network:
    proto: model.Network  #
    regions: Dict[str, RegionOfNetwork] = field(default_factory=dict)  #
    routes: List[rules.Route] = field(default_factory=list)  #
    firewallRules: List[rules.FirewallRule] = field(default_factory=list)  #

    peers: List[Tuple['Network', model.NetworkPeer]] = field(default_factory=list)  #

    def __post_init__(self):
        for r in REGION_LIST:
            self.regions[r] = RegionOfNetwork(network=self)

    def GetSubnetFromIP(self, ip: int) -> Subnet:
        for _, region in self.regions.items():
            for subnet in region.subnets:
                subnet: Subnet
                for ipRange in subnet.ipRanges:
                    if InIPv4Range(ip, ipRange):
                        return subnet


@dataclass
class Project:
    networks: Dict[str, Network] = field(default_factory=dict)  #


@dataclass
class DataPlane:
    projects: Dict[str, Project] = field(default_factory=dict)  #
    regions: Dict[str, List[RegionOfNetwork]] = field(default_factory=dict)

    def GetProject(self, url: str) -> Project:
        projId = ParseProjectFromUrl(url)
        return self.projects[projId]

    def GetNetwork(self, url: str) -> Network:
        networkId = ParseNetworkFromUrl(url)
        return self.GetProject(url).networks[networkId]

    def GetRegionOfNetwork(self, url: str) -> RegionOfNetwork:
        region = ParseRegionFromUrl(url)
        return self.GetNetwork(url).regions[region]

    def GetSubnet(self, url: str) -> Subnet:
        subnetId = ParseSubnetFromUrl(url)
        return self.GetRegionOfNetwork(url).subnets[subnetId]

    def Parse(self, dp: model.Project):
        """
        The networks, vms, subnets given in the protobuf are flattened and this function reconstruct the hierarchy
        e.g., putting the subnets under regions and projects.
        """
        for proj in dp.project_ids:
            self.projects[proj] = Project()

        self.ParseNetworks(dp)

        self.ParseSubnets(dp)

        self.ParsePeerNetworks(dp)

        self.ParseVms(dp)

        self.ParseRoutes(dp)

        self.ParseFirewallRules(dp)

        self.BuildRegions()

    def ParseFirewallRules(self, dp):
        for ruleProto in dp.firewall_rules:
            ruleProto: rules.Route
            self.GetNetwork(ruleProto.instance_filter.network).firewallRules.append(ruleProto)

    def ParseRoutes(self, dp):
        for routeProto in dp.routes:
            routeProto: rules.Route
            self.GetNetwork(routeProto.instance_filter.network).routes.append(routeProto)

    def BuildRegions(self):
        for r in REGION_LIST:
            self.regions[r] = []
        for proj in self.projects.values():
            for net in proj.networks.values():
                for id, regionOfNetwork in net.regions.items():
                    self.regions[id].append(regionOfNetwork)

    def ParseVms(self, dp):
        """
        The vms given in the protobuf is listed in the top level.
        This function identify the project, the network, and the region an vm is in,
        and put the vm into that region.
        """
        for vmProto in dp.instances:
            vmProto: model.Instance
            id = ParseInstanceFromUrl(vmProto.url)
            vm = Vm(proto=vmProto)
            self.GetNetwork(vmProto.network).regions[vmProto.region].vms[id] = vm

            for nic in vmProto.nics:
                vm.nics[nic.name] = nic

    def ParseNetworks(self, dp: model.Project):
        """
        The networks given in the protobuf is listed in the top level.
        This function identify the project the network is in, and put the networks into that project.
        And it also parse the subnets by calling ParseSubnets
        """
        for networkProto in dp.networks:
            networkProto: model.Network
            projId, networkId = ParseProjectFromUrl(networkProto.url), ParseNetworkFromUrl(networkProto.url)
            network = Network(proto=networkProto)
            self.projects[projId].networks[networkId] = network

    def ParseSubnets(self, dp: model.Project):
        """
        The subnets given in the protobuf is listed under a network.
        This function identify the region the subnet is in, and put the subnet into that region.
        """
        for subnetProto in dp.subnets:
            subnetProto: model.Subnet

            subnet = Subnet(proto=subnetProto)
            subnet.ipRanges.append(subnetProto.ipv4_range)
            subnet.ipRanges += subnetProto.secondary_ranges

            id = ParseSubnetFromUrl(subnetProto.url)
            region = ParseRegionFromUrl(subnetProto.url)
            network = self.GetNetwork(subnetProto.network)
            network.regions[region].subnets[id] = subnet

    def ParsePeerNetworks(self, dp: model.Project):
        """
        The peers given in the protobuf only maintains a url to the project.
        This function insert the network object into the "network" field directly into the protobuf.
        """
        for networkProto in dp.networks:
            network = self.GetNetwork(networkProto.url)
            for peerProto in networkProto.peers:
                try:
                    peer = self.GetNetwork(peerProto.peer_network)
                except KeyError:
                    peer = None

                network.peers.append((peer, peerProto))
