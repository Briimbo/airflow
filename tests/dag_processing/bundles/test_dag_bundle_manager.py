# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from airflow.dag_processing.bundles.base import BaseDagBundle
from airflow.dag_processing.bundles.manager import DagBundlesManager
from airflow.exceptions import AirflowConfigException
from airflow.models.dagbundle import DagBundleModel
from airflow.utils.session import create_session

from tests_common.test_utils.db import clear_db_dag_bundles


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param(None, {"dags-folder"}, id="default"),
        pytest.param("{}", set(), id="empty dict"),
        pytest.param(
            "[]",
            set(),
            id="empty list",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "name": "my-bundle",
                        "classpath": "airflow.dag_processing.bundles.local.LocalDagBundle",
                        "kwargs": {"local_folder": "/tmp/hihi", "refresh_interval": 1},
                    }
                ]
            ),
            {"my-bundle"},
            id="remove_dags_folder_default_add_bundle",
        ),
        pytest.param(
            "[]",
            set(),
            id="remove_dags_folder_default",
        ),
        pytest.param("1", "Bundle config is not a list", id="int"),
        pytest.param("abc", "Unable to parse .* as valid json", id="not_json"),
    ],
)
def test_parse_bundle_config(value, expected):
    """Test that bundle_configs are read from configuration."""
    envs = {"AIRFLOW__DAG_BUNDLES__BACKENDS": value} if value else {}
    cm = nullcontext()
    exp_fail = False
    if isinstance(expected, str):
        exp_fail = True
        cm = pytest.raises(AirflowConfigException, match=expected)

    with patch.dict(os.environ, envs), cm:
        bundle_manager = DagBundlesManager()
        names = set(x.name for x in bundle_manager.get_all_dag_bundles())

    if not exp_fail:
        assert names == expected


class BasicBundle(BaseDagBundle):
    def refresh(self):
        pass

    def get_current_version(self):
        pass

    def path(self):
        pass


BASIC_BUNDLE_CONFIG = [
    {
        "name": "my-test-bundle",
        "classpath": "tests.dag_processing.bundles.test_dag_bundle_manager.BasicBundle",
        "kwargs": {"refresh_interval": 1},
    }
]


def test_get_bundle():
    """Test that get_bundle builds and returns a bundle."""

    with patch.dict(os.environ, {"AIRFLOW__DAG_BUNDLES__BACKENDS": json.dumps(BASIC_BUNDLE_CONFIG)}):
        bundle_manager = DagBundlesManager()

        with pytest.raises(ValueError, match="'bundle-that-doesn't-exist' is not configured"):
            bundle_manager.get_bundle(name="bundle-that-doesn't-exist", version="hello")
        bundle = bundle_manager.get_bundle(name="my-test-bundle", version="hello")
    assert isinstance(bundle, BasicBundle)
    assert bundle.name == "my-test-bundle"
    assert bundle.version == "hello"
    assert bundle.refresh_interval == 1

    # And none for version also works!
    with patch.dict(os.environ, {"AIRFLOW__DAG_BUNDLES__BACKENDS": json.dumps(BASIC_BUNDLE_CONFIG)}):
        bundle = bundle_manager.get_bundle(name="my-test-bundle")
    assert isinstance(bundle, BasicBundle)
    assert bundle.name == "my-test-bundle"
    assert bundle.version is None


@pytest.fixture
def clear_db():
    clear_db_dag_bundles()
    yield
    clear_db_dag_bundles()


@pytest.mark.db_test
def test_sync_bundles_to_db(clear_db):
    def _get_bundle_names_and_active():
        with create_session() as session:
            return (
                session.query(DagBundleModel.name, DagBundleModel.active).order_by(DagBundleModel.name).all()
            )

    # Initial add
    with patch.dict(os.environ, {"AIRFLOW__DAG_BUNDLES__BACKENDS": json.dumps(BASIC_BUNDLE_CONFIG)}):
        manager = DagBundlesManager()
        manager.sync_bundles_to_db()
    assert _get_bundle_names_and_active() == [("my-test-bundle", True)]

    # simulate bundle config change
    # note: airflow will detect config changes when they are in env vars
    manager = DagBundlesManager()
    manager.sync_bundles_to_db()
    assert _get_bundle_names_and_active() == [("dags-folder", True), ("my-test-bundle", False)]

    # Re-enable one that reappears in config
    with patch.dict(os.environ, {"AIRFLOW__DAG_BUNDLES__BACKENDS": json.dumps(BASIC_BUNDLE_CONFIG)}):
        manager = DagBundlesManager()
        manager.sync_bundles_to_db()
    assert _get_bundle_names_and_active() == [("dags-folder", False), ("my-test-bundle", True)]