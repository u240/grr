#!/usr/bin/env python
"""Tests for building and repacking clients."""

import datetime
import io
import os
from unittest import mock

from absl import app
import yaml

from grr_response_client_builder import build_helpers
from grr_response_core import config
from grr_response_core.lib import config_parser
from grr_response_core.lib.rdfvalues import client as rdf_client
from grr.test_lib import test_lib


class BuildTests(test_lib.GRRBaseTest):
  """Tests for building functionality."""

  def testWriteBuildYaml(self):
    """Test build.yaml is output correctly."""
    context = [
        "Target:LinuxDeb", "Platform:Linux", "Target:Linux", "Arch:amd64"
    ]
    expected = {
        "Client.build_environment":
            "cp27-cp27mu-linux_x86_64",
        "Template.build_type":
            "Release",
        "Template.build_context":
            context,
        "Template.version_major":
            str(config.CONFIG.Get("Source.version_major")),
        "Template.version_minor":
            str(config.CONFIG.Get("Source.version_minor")),
        "Template.version_revision":
            str(config.CONFIG.Get("Source.version_revision")),
        "Template.version_release":
            str(config.CONFIG.Get("Source.version_release")),
        "Template.arch":
            u"amd64"
    }

    fd = io.StringIO()

    with mock.patch.object(rdf_client.Uname, "FromCurrentSystem") as fcs:
      fcs.return_value.signature.return_value = "cp27-cp27mu-linux_x86_64"

      before_time = datetime.datetime.now(datetime.timezone.utc)
      build_helpers.WriteBuildYaml(fd, context=context)
      after_time = datetime.datetime.now(datetime.timezone.utc)

    result = yaml.safe_load(fd.getvalue())

    build_time = datetime.datetime.fromisoformat(result["Client.build_time"])
    self.assertBetween(build_time, before_time, after_time)
    del result["Client.build_time"]

    self.assertEqual(result, expected)

  def testGenClientConfig(self):
    with test_lib.ConfigOverrider({"Client.build_environment": "test_env"}):

      data = build_helpers.GetClientConfig(["Client Context"], validate=True)

      parser = config_parser.YamlConfigFileParser("")
      raw_data = parser.RawDataFromBytes(data.encode("utf-8"))

      self.assertIn("Client.deploy_time", raw_data)

  def testGenClientConfig_ignoreBuilderContext(self):
    with test_lib.PreserveConfig():
      # Define a secondary config with special values for the ClientBuilder
      # context.
      str_override = """
        Test Context:
          Client.company_name: foo
          ClientBuilder Context:
            Client.company_name: bar
      """
      parser = config_parser.YamlConfigFileParser("")
      override = parser.RawDataFromBytes(str_override.encode("utf-8"))
      config.CONFIG.MergeData(override)
      # Sanity-check that the secondary config was merged into the global
      # config.
      self.assertEqual(config.CONFIG["Client.company_name"], "foo")

      context = ["Test Context", "ClientBuilder Context", "Client Context"]
      str_client_config = build_helpers.GetClientConfig(context)
      client_config = parser.RawDataFromBytes(str_client_config.encode("utf-8"))
      # Settings particular to the ClientBuilder context should not carry over
      # into the generated client config.
      self.assertEqual(client_config["Client.company_name"], "foo")

  def testRepackerDummyClientConfig(self):
    """Ensure our dummy client config can pass validation.

    This config is used to exercise repacking code in integration testing, here
    we just make sure it will pass validation.
    """
    new_config = config.CONFIG.MakeNewConfig()
    new_config.Initialize()
    new_config.LoadSecondaryConfig(
        os.path.join(config.CONFIG["Test.data_dir"], "dummyconfig.yaml"))
    context = ["Test Context", "ClientBuilder Context", "Client Context"]
    build_helpers.ValidateEndConfig(new_config, context=context)


def main(argv):
  test_lib.main(argv)


if __name__ == "__main__":
  app.run(main)
