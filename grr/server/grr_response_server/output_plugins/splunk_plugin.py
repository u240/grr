#!/usr/bin/env python
"""OutputPlugin that sends Flow results to Splunk Http Event Collector.

Configuration values for this plugin can be found in
core/grr_response_core/config/output_plugins.py

The spec for HTTP Event Collector is taken from https://docs.splunk.com
/Documentation/Splunk/8.0.1/Data/FormateventsforHTTPEventCollector
"""

import json
from typing import Any
from urllib import parse as urlparse

import requests

from google.protobuf import json_format
from grr_response_core import config
from grr_response_core.lib import rdfvalue
from grr_response_core.lib.rdfvalues import protodict as rdf_protodict
from grr_response_core.lib.rdfvalues import structs as rdf_structs
from grr_response_proto import output_plugin_pb2
from grr_response_server import data_store
from grr_response_server import export
from grr_response_server import output_plugin
from grr_response_server.export_converters import base
from grr_response_server.gui.api_plugins import flow as api_flow
from grr_response_server.gui.api_plugins import mig_flow
from grr_response_server.rdfvalues import flow_objects as rdf_flow_objects
from grr_response_server.rdfvalues import mig_objects

HTTP_EVENT_COLLECTOR_PATH = "services/collector/event"

JsonDict = dict[str, Any]


class SplunkConfigurationError(Exception):
  """Error indicating a wrong or missing Splunk configuration."""


class SplunkOutputPluginArgs(rdf_structs.RDFProtoStruct):
  """An RDF wrapper class for the arguments of SplunkOutputPlugin."""

  protobuf = output_plugin_pb2.SplunkOutputPluginArgs
  rdf_deps = []


def _ToDict(rdfval: rdf_structs.RDFProtoStruct) -> JsonDict:
  return json_format.MessageToDict(rdfval.AsPrimitiveProto(), float_precision=8)


class SplunkOutputPlugin(output_plugin.OutputPlugin):
  """OutputPlugin that sends Flow results to Splunk Http Event Collector."""

  name = "splunk"
  description = "Send flow results to Splunk."
  args_type = SplunkOutputPluginArgs

  def __init__(self, *args, **kwargs):
    """See base class."""
    super().__init__(*args, **kwargs)

    url = config.CONFIG["Splunk.url"]
    self._verify_https = config.CONFIG["Splunk.verify_https"]
    self._token = config.CONFIG["Splunk.token"]
    self._source = config.CONFIG["Splunk.source"]
    self._sourcetype = config.CONFIG["Splunk.sourcetype"]
    # Allow the Flow creator to override the index, fall back to configuration.
    self._index = self.args.index or config.CONFIG["Splunk.index"]

    if not url:
      raise SplunkConfigurationError(
          "Cannot start SplunkOutputPlugin, because Splunk.url is not "
          "configured. Set it to the base URL of your Splunk installation, "
          "e.g. 'https://mysplunkserver.example.com:8088'."
      )

    if not self._token:
      raise SplunkConfigurationError(
          "Cannot start SplunkOutputPlugin, because Splunk.token "
          "is not configured. You can get this authentication "
          "token when configuring a new HEC input in your Splunk "
          "installation."
      )

    self._url = urlparse.urljoin(url, HTTP_EVENT_COLLECTOR_PATH)

  def ProcessResponses(
      self,
      state: rdf_protodict.AttributedDict,
      responses: list[rdf_flow_objects.FlowResult],
  ) -> None:
    """See base class."""
    client_id = self._GetClientId(responses)
    flow_id = self._GetFlowId(responses)

    client = self._GetClientMetadata(client_id)
    flow = self._GetFlowMetadata(client_id, flow_id)

    events = [self._MakeEvent(response, client, flow) for response in responses]
    self._SendEvents(events)

  def _GetClientId(self, responses: list[rdf_flow_objects.FlowResult]) -> str:
    client_ids = {msg.client_id for msg in responses}
    if len(client_ids) > 1:
      raise AssertionError(
          (
              "ProcessResponses received messages from different Clients {},"
              " which violates OutputPlugin constraints."
          ).format(client_ids)
      )
    return client_ids.pop()

  def _GetFlowId(self, responses: list[rdf_flow_objects.FlowResult]) -> str:
    flow_ids = {msg.flow_id for msg in responses}
    if len(flow_ids) > 1:
      raise AssertionError(
          (
              "ProcessResponses received messages from different Flows {},"
              " which violates OutputPlugin constraints."
          ).format(flow_ids)
      )
    return flow_ids.pop()

  def _GetClientMetadata(self, client_id: str) -> base.ExportedMetadata:
    info = data_store.REL_DB.ReadClientFullInfo(client_id)
    info = mig_objects.ToRDFClientFullInfo(info)
    metadata = export.GetMetadata(client_id, info)
    metadata.timestamp = None  # timestamp is sent outside of metadata.
    return metadata

  def _GetFlowMetadata(self, client_id: str, flow_id: str) -> api_flow.ApiFlow:
    flow_obj = data_store.REL_DB.ReadFlowObject(client_id, flow_id)
    proto_api_flow = api_flow.InitApiFlowFromFlowObject(flow_obj)
    rdf_api_flow = mig_flow.ToRDFApiFlow(proto_api_flow)
    return rdf_api_flow

  def _MakeEvent(
      self,
      message: rdf_flow_objects.FlowResult,
      client: base.ExportedMetadata,
      flow: api_flow.ApiFlow,
  ) -> JsonDict:

    if message.timestamp:
      time = message.timestamp.AsSecondsSinceEpoch()
    else:
      time = rdfvalue.RDFDatetime.Now().AsSecondsSinceEpoch()

    event = {
        "time": time,
        "host": client.hostname or message.client_id,
        "source": self._source,
        "sourcetype": self._sourcetype,
        "event": {
            "client": _ToDict(client),
            "flow": _ToDict(flow),
            "resultType": message.payload.__class__.__name__,
            "result": _ToDict(message.payload),
        },
    }

    if self.args.annotations:
      event["event"]["annotations"] = list(self.args.annotations)

    if self._index:
      event["index"] = self._index

    return event

  def _SendEvents(self, events: list[JsonDict]) -> None:
    headers = {"Authorization": "Splunk {}".format(self._token)}

    # Batch multiple events in one request, separated by two newlines.
    data = "\n\n".join(json.dumps(event) for event in events)

    response = requests.post(
        url=self._url, verify=self._verify_https, data=data, headers=headers
    )
    response.raise_for_status()
