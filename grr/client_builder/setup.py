#!/usr/bin/env python
"""Setup configuration for the grr-response-client-builder package."""

import configparser
import os
import shutil

from setuptools import find_packages
from setuptools import setup
from setuptools.command.sdist import sdist

THIS_DIRECTORY = os.path.dirname(os.path.realpath(__file__))
os.chdir(THIS_DIRECTORY)


def get_config():
  """Get relative path to version.ini file and the INI parser with its data."""
  rel_ini_path = "version.ini"
  ini_path = os.path.join(THIS_DIRECTORY, rel_ini_path)
  if not os.path.exists(ini_path):
    rel_ini_path = os.path.join("..", "..", "version.ini")
    ini_path = os.path.join(THIS_DIRECTORY, rel_ini_path)
    if not os.path.exists(ini_path):
      raise RuntimeError("Couldn't find version.ini")

  config = configparser.ConfigParser()
  config.read(ini_path)
  return rel_ini_path, config


REL_INI_PATH, VERSION = get_config()


class Sdist(sdist):
  """Custom sdist class that bundles GRR's version.ini."""

  def make_release_tree(self, base_dir, files):
    sdist.make_release_tree(self, base_dir, files)
    sdist_version_ini = os.path.join(base_dir, "version.ini")
    if os.path.exists(sdist_version_ini):
      os.unlink(sdist_version_ini)
    shutil.copy(
        os.path.join(THIS_DIRECTORY, "../../version.ini"), sdist_version_ini)


data_files = [REL_INI_PATH]

setup_args = dict(
    name="grr-response-client-builder",
    version=VERSION.get("Version", "packageversion"),
    description="GRR Rapid Response",
    license="Apache License, Version 2.0",
    maintainer="GRR Development Team",
    maintainer_email="grr-dev@googlegroups.com",
    url="https://github.com/google/grr",
    entry_points={
        "console_scripts": [
            "grr_client_build = %s"
            % "grr_response_client_builder.distro_entry:ClientBuild",
        ]
    },
    cmdclass={"sdist": Sdist},
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
    install_requires=[
        "distro==1.7.0",
        "grr-response-client==%s" % VERSION.get("Version", "packagedepends"),
        "grr-response-core==%s" % VERSION.get("Version", "packagedepends"),
        "fleetspeak-client-bin==0.1.13",
        "olefile==0.46",
        "PyInstaller==5.13.2",
    ],
    # Data files used by GRR. Access these via the config_lib "resource" filter.
    data_files=data_files,
)

setup(**setup_args)
