#!/usr/bin/python

# Copyright 2014 iNuron NV
#
# Licensed under the Open vStorage Modified Apache License (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.openvstorage.org/license
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Arakoon Health Check module
"""

import time
import uuid
from ovs.dal.lists.storagerouterlist import StorageRouterList
from ovs.extensions.storage.persistent.pyrakoonstore import PyrakoonStore
from ovs.extensions.db.arakoon.ArakoonInstaller import ArakoonClusterConfig
from ovs.extensions.db.arakoon.arakoon.ArakoonManagement import ArakoonManagementEx
from ovs.extensions.db.arakoon.pyrakoon.pyrakoon.compat import ArakoonNotFound, ArakoonNoMaster, ArakoonNoMasterResult
from ovs.extensions.healthcheck.utils.extension import Utils
from ovs.log.healthcheck_logHandler import HCLogHandler

try:
    from ovs.extensions.db.etcd.configuration import EtcdConfiguration
except ImportError:
    pass


class ArakoonHealthCheck:
    """
    A healthcheck for the arakoon persistent store
    """

    def __init__(self, logging=HCLogHandler(False)):
        """
        Init method for Arakoon health check module

        @param logging: ovs.log.healthcheck_logHandler

        @type logging: Class
        """

        self.module = "arakoon"
        self.utility = Utils()
        self.LOGGER = logging

    def fetch_available_clusters(self):
        """
        Fetches the available arakoon clusters of a cluster

        @return: if succeeded a list; if failed `None`

        @rtype: list
        """

        if not self.utility.etcd:
            aramex = ArakoonManagementEx()
            arakoon_clusters = aramex.listClusters()
        else:
            arakoon_clusters = list(EtcdConfiguration.list('/ovs/{0}'.format(self.module)))

        result = {}
        if len(arakoon_clusters) != 0:
            # add arakoon clusters
            for cluster in arakoon_clusters:
                # add node that is available for arakoon cluster
                nodes_per_cluster_result = {}

                if not self.utility.etcd:
                    master_node_ids = aramex.getCluster(str(cluster)).listNodes()
                else:
                    ak = ArakoonClusterConfig(str(cluster))
                    ak.load_config()
                    master_node_ids = list((node.name for node in ak.nodes))

                for node_id in master_node_ids:
                    node_info = StorageRouterList.get_by_machine_id(node_id)

                    # add node information
                    nodes_per_cluster_result.update({node_id: {
                        'hostname': node_info.name,
                        'ip-address': node_info.ip,
                        'guid': node_info.guid,
                        'pmachine_guid': node_info.pmachine_guid,
                        'node_type': node_info.node_type
                        }
                    })
                result.update({cluster: nodes_per_cluster_result})

            return result
        else:
            # no arakoon clusters on node
            self.LOGGER.logger("No installed arakoon clusters detected on this system ...", self.module, 2,
                               'arakoon_no_clusters_found', False)
            return None

    def _verify_integrity(self, arakoon_overview):
        """
        Verifies the integrity of a list of arakoons

        @param arakoon_overview: list of arakoon names

        @type arakoon_overview: list that consists of strings

        @return: (arakoonperfworking_list, arakoonnomaster_list, arakoondown_list, arakoonunknown_list)

        @rtype: tuple that consists of lists
        """

        arakoonunknown_list = []
        arakoonperfworking_list = []
        arakoonnomaster_list = []
        arakoondown_list = []

        # verify integrity of arakoon clusters
        for cluster_name, cluster_info in arakoon_overview.iteritems():

            tries = 1
            max_tries = 2  # should be 5 but .nop is taking WAY to long

            while tries <= max_tries:
                self.LOGGER.logger("Try {0} on cluster '{1}'".format(tries, cluster_name), self.module, 3,
                                   'arakoonTryCheck', False)

                key = 'ovs-healthcheck-{0}'.format(str(uuid.uuid4()))
                value = str(time.time())

                try:
                    # determine if there is a healthy cluster
                    client = PyrakoonStore(str(cluster_name))
                    client.nop()

                    # perform more complicated action to arakoon
                    client.set(key, value)
                    if client.get(key) == value:
                        client.delete(key)
                        arakoonperfworking_list.append(cluster_name)
                        break

                except ArakoonNotFound:
                    if tries == max_tries:
                        arakoondown_list.append(cluster_name)
                        break

                except (ArakoonNoMaster, ArakoonNoMasterResult):
                    if tries == max_tries:
                        arakoonnomaster_list.append(cluster_name)
                        break

                except Exception:
                    if tries == max_tries:
                        print "Exception!"
                        arakoonunknown_list.append(cluster_name)
                        break

                # finish try if failed
                tries += 1

        return arakoonperfworking_list, arakoonnomaster_list, arakoondown_list, arakoonunknown_list

    def check_arakoons(self):
        """
        Verifies/validates the integrity of all available arakoons
        """

        self.LOGGER.logger("Fetching available arakoon clusters: ", self.module, 3, 'checkArakoons', False)
        try:
            arakoon_overview = self.fetch_available_clusters()

            # fetch overview of arakoon clusters on local node
            if arakoon_overview:
                self.LOGGER.logger("{0} available Arakoons successfully fetched, starting verification of clusters ..."
                                   .format(len(arakoon_overview)), self.module, 1,
                                   'arakoon_amount_on_cluster {0}'.format(len(arakoon_overview)), False)

                ver_result = self._verify_integrity(arakoon_overview)
                if len(ver_result[0]) == len(arakoon_overview):
                    self.LOGGER.logger("ALL available Arakoon(s) their integrity are/is OK! ", self.module, 1,
                                       'arakoon_integrity')
                else:
                    # less output for unattended_mode
                    if not self.LOGGER.unattended_mode:
                        # check amount OK arakoons
                        if len(ver_result[0]) > 0:
                            self.LOGGER.logger(
                                "{0} Arakoon(s) is/are OK!: {1}".format(len(ver_result[0]), ', '.join(ver_result[0])),
                                self.module, 1, 'arakoon_some_up', False)
                        # check amount NO-MASTER arakoons
                        if len(ver_result[1]) > 0:
                            self.LOGGER.logger("{0} Arakoon(s) cannot find a MASTER: {1}".format(len(ver_result[1]),
                                                                                                 ', '.join(ver_result[1]
                                                                                                           )),
                                               self.module, 0, 'arakoon_no_master_exception'.format(len(ver_result[1])))

                        # check amount DOWN arakoons
                        if len(ver_result[2]) > 0:
                            self.LOGGER.logger("{0} Arakoon(s) seem(s) to be DOWN!: {1}".format(len(ver_result[2]),
                                                                                                ', '.join(ver_result[2]
                                                                                                          )),
                                               self.module, 0, 'arakoon_down_exception'.format(len(ver_result[2])))

                        # check amount UNKNOWN_ERRORS arakoons
                        if len(ver_result[3]) > 0:
                            self.LOGGER.logger("{0} Arakoon(s) seem(s) to have UNKNOWN ERRORS, please check the logs @"
                                               " '/var/log/ovs/arakoon.log' or"
                                               " '/var/log/upstart/ovs-arakoon-*.log': {1}".format(len(ver_result[3]),
                                                                                                   ', '.join(
                                                                                                           ver_result[3]
                                                                                                   )), self.module, 0,
                                               'arakoon_unknown_exception')
                    else:
                        self.LOGGER.logger("Some Arakoon(s) have problems, please check this!", self.module, 0,
                                           'arakoon_integrity')
            else:
                self.LOGGER.logger("No clusters found on this node, so stopping arakoon checks ...", self.module, 5,
                                   'arakoon_integrity')
        except Exception as e:
            self.LOGGER.logger("One ore more Arakoon clusters cannot be reached :(, due to: {0}".format(e),
                               self.module, 4, 'arakoon_integrity')