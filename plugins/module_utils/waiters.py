# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

import json
import time
from typing import Any
from typing import Dict
from typing import Optional

try:
    import botocore.waiter as core_waiter
    from botocore.exceptions import WaiterError
except ImportError:
    pass

try:
    from ansible_collections.amazon.aws.plugins.module_utils.retries import RetryingBotoClientWrapper
except ImportError:
    try:
        from ansible_collections.amazon.aws.plugins.module_utils.modules import (
            _RetryingBotoClientWrapper as RetryingBotoClientWrapper,
        )
    except ImportError:
        RetryingBotoClientWrapper = None
from ansible_collections.amazon.aws.plugins.module_utils.botocore import is_boto3_error_code
from ansible_collections.amazon.aws.plugins.module_utils.retries import AWSRetry

model_package_group_data = {
    "version": 2,
    "waiters": {
        "ModelPackageGroupDeleted": {
            "description": "Wait until a SageMaker model package group is deleted",
            "delay": 15,
            "maxAttempts": 40,
            "operation": "DescribeModelPackageGroup",
            "acceptors": [
                {
                    "matcher": "error",
                    "expected": "ValidationException",
                    "state": "success",
                }
            ],
        }
    },
}


def model_package_group_model(name: str) -> Any:
    return core_waiter.WaiterModel(waiter_config=model_package_group_data).get_waiter(name)


@AWSRetry.jittered_backoff(retries=10)
def _describe_pipeline(client, pipeline_name: str, pipeline_version_id: Optional[int] = None) -> Dict[str, Any]:
    params = dict(PipelineName=pipeline_name)
    if pipeline_version_id is not None:
        params["PipelineVersionId"] = pipeline_version_id
    return client.describe_pipeline(**params)


def wait_for_pipeline_status(
    client,
    module,
    pipeline_name: str,
    deleted: bool = False,
    expected_properties: Optional[Dict[str, Any]] = None,
    pipeline_version_id: Optional[int] = None,
) -> None:
    """Wait for a SageMaker pipeline to become active or be deleted."""
    wait_timeout = module.params.get("wait_timeout", 600)
    deadline = time.monotonic() + wait_timeout
    while True:
        try:
            pipeline = _describe_pipeline(client, pipeline_name, pipeline_version_id)
        except is_boto3_error_code("ResourceNotFound"):
            pipeline = None

        if deleted and pipeline is None:
            return
        if (
            not deleted
            and pipeline is not None
            and pipeline.get("PipelineStatus") == "Active"
            and _pipeline_properties_match(pipeline, expected_properties or {}, module, pipeline_name)
        ):
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            desired_state = "deletion" if deleted else "Active status with requested properties"
            module.fail_json(msg=f"Timed out waiting for pipeline {pipeline_name} to reach {desired_state}.")
        time.sleep(min(5, remaining))


def _pipeline_properties_match(
    pipeline: Dict[str, Any], expected_properties: Dict[str, Any], module, pipeline_name: str
) -> bool:
    for name, expected in expected_properties.items():
        actual = pipeline.get(name)
        if name == "PipelineDefinition":
            try:
                actual = json.loads(actual)
            except (TypeError, json.JSONDecodeError) as e:
                module.fail_json(msg=f"Pipeline {pipeline_name} returned an invalid definition while waiting: {e}")
        if actual != expected:
            return False
    return True


waiters_by_name = {
    (
        "SageMaker",
        "model_package_group_deleted",
    ): lambda sagemaker: core_waiter.Waiter(
        "model_package_group_deleted",
        model_package_group_model("ModelPackageGroupDeleted"),
        core_waiter.NormalizedOperationMethod(sagemaker.describe_model_package_group),
    ),
}


def get_waiter(client, waiter_name: str) -> Any:
    if RetryingBotoClientWrapper is not None and isinstance(client, RetryingBotoClientWrapper):
        return get_waiter(client.client, waiter_name)
    try:
        return waiters_by_name[(client.__class__.__name__, waiter_name)](client)
    except KeyError:
        raise NotImplementedError(
            "Waiter {0} could not be found for client {1}. Available waiters: {2}".format(
                waiter_name,
                type(client),
                ", ".join(repr(key) for key in waiters_by_name.keys()),
            )
        )


def wait_for_model_package_group_deletion(
    client, module, model_package_group_name: str, wait_timeout: int = 600
) -> None:
    """Wait until a SageMaker model package group is actually gone."""
    if not module.params.get("wait", True):
        return

    delay = 15
    max_attempts = max(1, wait_timeout // delay)
    waiter = get_waiter(client, "model_package_group_deleted")
    try:
        waiter.wait(
            ModelPackageGroupName=model_package_group_name,
            WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts},
        )
    except WaiterError:
        module.fail_json(
            msg=(
                f"Timeout waiting for model package group {model_package_group_name} to be deleted. "
                "The resource still exists after the configured wait timeout."
            )
        )
