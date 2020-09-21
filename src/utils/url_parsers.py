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

def GetSegmentOfUrl(url: str, prefix: str) -> str:
    """
    Get the next segment from url following prefix. The return value is a segment without slash.

    For example, url = "projects/test-project/global/networks/n1",
    and prefix = "networks/", then the return value is "n1".
    """
    if not url: return ""
    if not prefix: return ""

    if not prefix.endswith("/"): prefix += "/"

    offset = url.find(prefix)
    if offset == -1: return ""

    offset += len(prefix)
    end = url.find("/", offset)
    if end == -1: end = len(url)

    return url[offset:end]


def ParseProjectFromUrl(url: str) -> str:
    return GetSegmentOfUrl(url, "projects/")


def ParseNetworkFromUrl(url: str) -> str:
    return GetSegmentOfUrl(url, "/networks/")


def ParseRegionFromUrl(url: str) -> str:
    return GetSegmentOfUrl(url, "/regions/")


def ParseSubnetFromUrl(url: str) -> str:
    return GetSegmentOfUrl(url, "/subnetworks/")


def ParseInstanceFromUrl(url: str) -> str:
    return GetSegmentOfUrl(url, "/instances/")
