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
from collections import defaultdict
from dataclasses import dataclass, field
from typing import *

import proto.cloud_network_model_pb2 as entities
import proto.rules_pb2 as rules

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
    # - Region > VPN Gateway > VPN Tunnel
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
    "europe-west5",
    "europe-west6",
    "northamerica-northeast1",
    "southamerica-east1",
    "us-central1",
    "us-central2",
    "us-east1",
    "us-east2",
    "us-east4",
    "us-west1",
    "us-west2",
    "us-west3",
    "us-west4"
]


@dataclass
class Vm:
    proto: entities.Instance  # point to the Instance proto
    nics: Dict[str, entities.NetworkInterface] = field(default_factory=dict)  # key is name of the nic


@dataclass
class Subnet:
    proto: entities.Subnet  # point to the Subnet proto
    ipRanges: List[rules.Ipv4Range] = field(default_factory=list)

    # a nic may present in many subnets by having secondary IPs. This field collects all visible nics in the subnet
    # key is url of the nic: the url of the vm instance + "/interfaces/<nic_name>"
    nics: Dict[str, entities.NetworkInterface] = field(default_factory=dict)


@dataclass
class RegionOfNetwork:
    subnets: Dict[str, Subnet] = field(default_factory=dict)  # key is name of the subnet
    vms: Dict[str, Vm] = field(default_factory=dict)  # key is name of the instance


@dataclass
class Network:
    proto: entities.Network  # point to the Network proto
    regions: Dict[str, RegionOfNetwork] = \
        field(default_factory=lambda: defaultdict(lambda: RegionOfNetwork()))  # key is name of the region
    routes: List[rules.Route] = field(default_factory=list)
    firewallRules: List[rules.FirewallRule] = field(default_factory=list)

    # Key is the url of the peer network
    # Each element is a pair. The first is the peer network object, and the second is the NetworkPeer proto
    peers: Dict[str, Tuple['Network', entities.NetworkPeer]] = field(default_factory=dict)

    def GetSubnetFromIP(self, ip: int) -> Subnet:
        for region in self.regions.values():
            for subnet in region.subnets.values():
                subnet: Subnet
                for ipRange in subnet.ipRanges:
                    if InIPv4Range(ip, ipRange):
                        return subnet


@dataclass
class Project:
    networks: Dict[str, Network] = field(default_factory=dict)  # key is name of the network


@dataclass
class Model:
    model: entities.Model
    projects: Dict[str, Project] = field(default_factory=dict)  # key is name of the project
    regions: Dict[str, List[RegionOfNetwork]] = \
        field(default_factory=lambda: defaultdict(list))  # key is name of the region

    vpnGateways: Dict[Union[str, int], entities.VPNGateway] = \
        field(default_factory=dict)  # key is url or IP of the gateway
    vpnTunnelsOfGateway: Dict[str, List[entities.VPNTunnel]] = \
        field(default_factory=lambda: defaultdict(list))  # key is url of the gateway the tunnels are attached to
    vpnTunnels: Dict[str, entities.VPNTunnel] = field(default_factory=dict)  # key is url of the tunnel
    vpnTunnelToRoutes: Dict[str, List[rules.Route]] = \
        field(default_factory=lambda: defaultdict(list))  # key is url of the tunnel

    def __post_init__(self):
        self.Parse()

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

    def GetSubnetFromIP(self, ip: int) -> Subnet:
        for project in self.projects.values():
            for network in project.networks.values():
                subnet = network.GetSubnetFromIP(ip)
                if subnet: return subnet

    def GetVpnGateway(self, urlOrIp: Union[str, int]) -> entities.VPNGateway:
        return self.vpnGateways[urlOrIp]

    def getPeerVpnTunnel(self, vpnTunnel: entities.VPNTunnel) -> entities.VPNTunnel:
        """
        If the peer of a vpn tunnel also exists in the model, return the peer tunnel proto. If not, return None.
        The peer tunnel is determined by VPN gateway IPs and the interface indexes.
        """
        vgw: entities.VPNGateway = self.GetVpnGateway(vpnTunnel.vpn_gateway)
        peerGw: entities.VPNGateway = self.GetVpnGateway(vpnTunnel.peer_ip)
        if peerGw is None: return None

        for tunnel in self.vpnTunnelsOfGateway[peerGw.url]:
            tunnel: entities.VPNTunnel

            if tunnel.peer_ip in vgw.ip and tunnel.vpn_gateway_interface == vpnTunnel.vpn_gateway_interface:
                return tunnel

    def Parse(self):
        """
        The networks, vms, subnets given in the protobuf are flattened and this function reconstruct the hierarchy
        e.g., putting the subnets under regions and projects.
        """
        for proj in self.model.project_ids:
            self.projects[proj] = Project()

        self.ParseNetworks()

        self.ParseSubnets()

        self.ParsePeerNetworks()

        self.ParseVms()

        self.ParseRoutes()

        self.ParseFirewallRules()

        self.BuildRegions()

        self.ParseVpn()

    def ParseFirewallRules(self):
        for ruleProto in self.model.firewall_rules:
            ruleProto: rules.Route
            self.GetNetwork(ruleProto.instance_filter.network).firewallRules.append(ruleProto)

    def ParseRoutes(self):
        for routeProto in self.model.routes:
            routeProto: rules.Route
            self.GetNetwork(routeProto.instance_filter.network).routes.append(routeProto)

            if routeProto.next_hop_tunnel:
                url = routeProto.next_hop_tunnel
                self.vpnTunnelToRoutes[url].append(routeProto)

    def BuildRegions(self):
        """
        A region is not a part of a network/project because it holds many subnets.
        While a project/network may span many regions.
        To completely fulfill the partial order, aggregate all regionOfNetwork info into the top level region field
        """
        for proj in self.projects.values():
            for net in proj.networks.values():
                for id, regionOfNetwork in net.regions.items():
                    self.regions[id].append(regionOfNetwork)

    def ParseVms(self):
        """
        The vms given in the protobuf is listed in the top level.
        This function identify the project, the network, and the region an vm is in,
        and put the vm into that region.
        """
        for vmProto in self.model.instances:
            vmProto: entities.Instance
            id = ParseInstanceFromUrl(vmProto.url)
            vm = Vm(proto=vmProto)
            self.GetNetwork(vmProto.network).regions[vmProto.region].vms[id] = vm

            for nic in vmProto.nics:
                nic: entities.NetworkInterface
                vm.nics[nic.name] = nic

                subnet = self.GetSubnetFromIP(nic.ip)
                subnet.nics[vmProto.url + "/interfaces/" + nic.name] = nic

    def ParseNetworks(self):
        """
        The networks given in the protobuf is listed in the top level.
        This function identify the project the network is in, and put the networks into that project.
        And it also parse the subnets by calling ParseSubnets
        """
        for networkProto in self.model.networks:
            networkProto: entities.Network
            projId, networkId = ParseProjectFromUrl(networkProto.url), ParseNetworkFromUrl(networkProto.url)
            network = Network(proto=networkProto)
            self.projects[projId].networks[networkId] = network

    def ParseSubnets(self):
        """
        The subnets given in the protobuf is listed under a network.
        This function identify the region the subnet is in, and put the subnet into that region.
        """
        for subnetProto in self.model.subnets:
            subnetProto: entities.Subnet

            subnet = Subnet(proto=subnetProto)
            subnet.ipRanges.append(subnetProto.ipv4_range)
            subnet.ipRanges += subnetProto.secondary_ranges

            id = ParseSubnetFromUrl(subnetProto.url)
            region = ParseRegionFromUrl(subnetProto.url)
            network = self.GetNetwork(subnetProto.network)
            network.regions[region].subnets[id] = subnet

    def ParsePeerNetworks(self):
        """
        The peers given in the protobuf only maintains a url to the project.
        This function insert the network object into the "network" field aside the protobuf.
        And if the peer network is absent in the given data plane, put None.
        """
        for networkProto in self.model.networks:
            network = self.GetNetwork(networkProto.url)
            for peerProto in networkProto.peers:
                try:
                    peer = self.GetNetwork(peerProto.peer_network)
                except KeyError:
                    peer = None

                network.peers[peerProto.peer_network] = (peer, peerProto)

    def ParseVpn(self):
        """
        In plain proto, to get the network of a VPN tunnel should first resolve the gateway, and then locate the network.
        This function build an index to faster the lookup
        """
        for gw in self.model.vpn_gateways:
            gw: entities.VPNGateway

            self.vpnGateways[gw.url] = gw

            for ip in gw.ip:  # the name is misleading. May be two IPs for HA VPN.
                self.vpnGateways[ip] = gw

        for tunnel in self.model.vpn_tunnels:
            tunnel: entities.VPNTunnel

            self.vpnTunnelsOfGateway[tunnel.vpn_gateway].append(tunnel)
            self.vpnTunnels[tunnel.url] = tunnel
