#!/usr/bin/env python
"""Tests for the worker."""

import threading
from unittest import mock

from absl import app

from grr_response_core.lib.rdfvalues import client as rdf_client
from grr_response_core.lib.rdfvalues import mig_protodict
from grr_response_core.lib.rdfvalues import protodict as rdf_protodict
from grr_response_proto import objects_pb2
from grr_response_server import data_store
from grr_response_server import foreman
from grr_response_server import worker_lib
from grr.test_lib import action_mocks
from grr.test_lib import flow_test_lib
from grr.test_lib import test_lib


class GrrWorkerTest(flow_test_lib.FlowTestsBaseclass):
  """Tests the GRR Worker."""

  def testMessageHandlers(self):
    client_id = self.SetupClient(100)

    done = threading.Event()

    def handle(l):
      worker_lib.ProcessMessageHandlerRequests(l)
      done.set()

    data_store.REL_DB.RegisterMessageHandler(
        handle, worker_lib.GRRWorker.message_handler_lease_time, limit=1000
    )

    startup_info = rdf_client.StartupInfo()
    startup_info.client_info.client_version = 4321
    emb = mig_protodict.ToProtoEmbeddedRDFValue(
        rdf_protodict.EmbeddedRDFValue(startup_info)
    )

    data_store.REL_DB.WriteMessageHandlerRequests([
        objects_pb2.MessageHandlerRequest(
            client_id=client_id,
            handler_name="ClientStartupHandler",
            request_id=12345,
            request=emb,
        )
    ])

    self.assertTrue(done.wait(10))

    result = data_store.REL_DB.ReadClientStartupInfo(client_id)
    self.assertEqual(result.client_info.client_version, 4321)

    data_store.REL_DB.UnregisterMessageHandler(timeout=60)

    # Make sure there are no leftover requests.
    self.assertEqual(data_store.REL_DB.ReadMessageHandlerRequests(), [])

  def testCPULimitForFlows(self):
    """This tests that the client actions are limited properly."""
    client_id = self.SetupClient(0)

    client_mock = action_mocks.CPULimitClientMock(
        user_cpu_usage=[10], system_cpu_usage=[10], network_usage=[1000]
    )

    flow_test_lib.StartAndRunFlow(
        flow_test_lib.CPULimitFlow,
        client_mock,
        creator=self.test_username,
        client_id=client_id,
        cpu_limit=1000,
        network_bytes_limit=10000,
    )

    self.assertEqual(client_mock.storage["cpulimit"], [1000, 980, 960])
    self.assertEqual(client_mock.storage["networklimit"], [10000, 9000, 8000])

  def testForemanMessageHandler(self):
    with mock.patch.object(foreman.Foreman, "AssignTasksToClient") as instr:
      # Send a message to the Foreman.
      client_id = "C.1100110011001100"

      data_store.REL_DB.WriteMessageHandlerRequests([
          objects_pb2.MessageHandlerRequest(
              client_id=client_id,
              handler_name="ForemanHandler",
              request_id=12345,
              request=mig_protodict.ToProtoEmbeddedRDFValue(
                  rdf_protodict.EmbeddedRDFValue(rdf_protodict.DataBlob())
              ),
          )
      ])

      done = threading.Event()

      def handle(l):
        worker_lib.ProcessMessageHandlerRequests(l)
        done.set()

      data_store.REL_DB.RegisterMessageHandler(
          handle, worker_lib.GRRWorker.message_handler_lease_time, limit=1000
      )
      try:
        self.assertTrue(done.wait(10))

        # Make sure there are no leftover requests.
        self.assertEqual(data_store.REL_DB.ReadMessageHandlerRequests(), [])

        instr.assert_called_once_with(client_id)
      finally:
        data_store.REL_DB.UnregisterMessageHandler(timeout=60)


def main(argv):
  test_lib.main(argv)


if __name__ == "__main__":
  app.run(main)
