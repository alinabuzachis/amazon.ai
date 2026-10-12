#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from ansible_collections.amazon.ai.plugins.module_utils.waiters import get_waiter
from ansible_collections.amazon.ai.plugins.module_utils.waiters import wait_for_model_package_group_deletion
from ansible_collections.amazon.ai.plugins.module_utils.waiters import wait_for_pipeline_status
from botocore.exceptions import ClientError


class SageMaker:
    def describe_model_package_group(self, **kwargs):
        return kwargs


def test_get_waiter_returns_model_package_group_deletion_waiter():
    waiter = get_waiter(SageMaker(), "model_package_group_deleted")

    assert waiter.name == "model_package_group_deleted"


@patch("ansible_collections.amazon.ai.plugins.module_utils.waiters.get_waiter")
def test_wait_for_model_package_group_deletion_uses_timeout(mock_get_waiter):
    waiter = MagicMock()
    mock_get_waiter.return_value = waiter
    module = MagicMock()
    module.params = {"wait": True}

    wait_for_model_package_group_deletion(
        MagicMock(),
        module,
        "example-group",
        wait_timeout=30,
    )

    mock_get_waiter.assert_called_once()
    waiter.wait.assert_called_once_with(
        ModelPackageGroupName="example-group",
        WaiterConfig={"Delay": 15, "MaxAttempts": 2},
    )


def test_wait_for_pipeline_status_checks_expected_properties_structurally():
    client = MagicMock()
    client.describe_pipeline.return_value = {
        "PipelineStatus": "Active",
        "PipelineDescription": "ready",
        "PipelineDefinition": '{"Steps": [{"Name": "step"}]}',
    }
    module = MagicMock()
    module.params = {"wait_timeout": 1}

    wait_for_pipeline_status(
        client,
        module,
        "example-pipeline",
        expected_properties={
            "PipelineDescription": "ready",
            "PipelineDefinition": {"Steps": [{"Name": "step"}]},
        },
    )

    client.describe_pipeline.assert_called_once_with(PipelineName="example-pipeline")
    module.fail_json.assert_not_called()


def test_wait_for_pipeline_status_describes_requested_version():
    client = MagicMock()
    client.describe_pipeline.return_value = {"PipelineStatus": "Active"}
    module = MagicMock()
    module.params = {"wait_timeout": 1}

    wait_for_pipeline_status(client, module, "example-pipeline", pipeline_version_id=3)

    client.describe_pipeline.assert_called_once_with(PipelineName="example-pipeline", PipelineVersionId=3)


def test_wait_for_pipeline_status_retries_until_expected_properties_are_visible():
    client = MagicMock()
    client.describe_pipeline.side_effect = [
        {"PipelineStatus": "Active", "PipelineDescription": "old"},
        {"PipelineStatus": "Active", "PipelineDescription": "new"},
    ]
    module = MagicMock()
    module.params = {"wait_timeout": 1}

    with patch("ansible_collections.amazon.ai.plugins.module_utils.waiters.time.sleep"):
        wait_for_pipeline_status(
            client,
            module,
            "example-pipeline",
            expected_properties={"PipelineDescription": "new"},
        )

    assert client.describe_pipeline.call_count == 2
    module.fail_json.assert_not_called()


def test_wait_for_pipeline_deletion_accepts_resource_not_found():
    client = MagicMock()
    client.describe_pipeline.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFound", "Message": "missing"}},
        "DescribePipeline",
    )
    module = MagicMock()
    module.params = {"wait_timeout": 1}

    wait_for_pipeline_status(client, module, "example-pipeline", deleted=True)

    module.fail_json.assert_not_called()


def test_wait_for_pipeline_status_fails_when_expected_properties_timeout():
    client = MagicMock()
    client.describe_pipeline.return_value = {"PipelineStatus": "Active", "PipelineDescription": "old"}
    module = MagicMock()
    module.params = {"wait_timeout": 1}
    module.fail_json.side_effect = RuntimeError("wait failed")

    with (
        patch(
            "ansible_collections.amazon.ai.plugins.module_utils.waiters.time.monotonic",
            side_effect=[0, 2],
        ),
        pytest.raises(RuntimeError, match="wait failed"),
    ):
        wait_for_pipeline_status(
            client,
            module,
            "example-pipeline",
            expected_properties={"PipelineDescription": "new"},
        )

    module.fail_json.assert_called_once()
