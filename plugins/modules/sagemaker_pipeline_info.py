#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

DOCUMENTATION = r"""
---
module: sagemaker_pipeline_info
short_description: Gather information about Amazon SageMaker Pipelines
version_added: "2.0.0"
author:
    - Jan Likar (@janlikar)
description:
    - Retrieve information about a named Amazon SageMaker Pipeline or list pipeline definitions.
    - This module does not retrieve pipeline execution information.
attributes:
    check_mode:
        description: Can run in check mode without changing the target.
        support: full
    diff_mode:
        description: Does not support diff mode.
        support: none
options:
    pipeline_name:
        description:
            - The name or ARN of the pipeline to retrieve.
            - If omitted, the module lists pipeline summaries.
        type: str
        aliases: ['name']
    pipeline_version_id:
        description:
            - The historical pipeline version to describe.
            - Requires O(pipeline_name).
        type: int
    pipeline_name_prefix:
        description:
            - Only list pipelines whose names begin with this prefix.
        type: str
    sort_by:
        description:
            - The field used to sort listed pipelines.
            - When O(pipeline_name_prefix) is supplied, only V(Name) is supported.
        type: str
        choices: ['Name', 'CreationTime']
        default: Name
    sort_order:
        description:
            - The sort order for listed pipelines.
        type: str
        choices: ['Ascending', 'Descending']
        default: Ascending
    max_results:
        description:
            - The maximum number of pipeline summaries returned per service page.
            - All pages are read and returned.
        type: int
extends_documentation_fragment:
    - amazon.ai.common.modules
    - amazon.ai.region.modules
    - amazon.ai.boto3
notes:
    - Required IAM actions include sagemaker:DescribePipeline and sagemaker:ListPipelines.
seealso:
    - module: amazon.ai.sagemaker_pipeline
"""

EXAMPLES = r"""
- name: Get details about a specific pipeline
  amazon.ai.sagemaker_pipeline_info:
    pipeline_name: example-pipeline

- name: Get a historical pipeline version
  amazon.ai.sagemaker_pipeline_info:
    pipeline_name: example-pipeline
    pipeline_version_id: 2

- name: List pipelines with a name prefix
  amazon.ai.sagemaker_pipeline_info:
    pipeline_name_prefix: example
    sort_by: Name
    sort_order: Descending
"""

RETURN = r"""
pipeline:
    description: The detailed pipeline definition returned by SageMaker.
    type: dict
    returned: when O(pipeline_name) is supplied, empty when the pipeline does not exist
    sample:
        pipeline_name: example-pipeline
        pipeline_status: Active
    contains:
        pipeline_arn:
            description: The ARN of the pipeline.
            returned: when O(pipeline_name) is supplied and the pipeline exists
            type: str
            sample: "arn:aws:sagemaker:us-east-1:123456789012:pipeline/example-pipeline"
        pipeline_name:
            description: The name of the pipeline.
            returned: when O(pipeline_name) is supplied and the pipeline exists
            type: str
            sample: example-pipeline
        pipeline_definition:
            description: The effective JSON definition of the pipeline.
            returned: when O(pipeline_name) is supplied and the pipeline exists
            type: str
            sample: '{"Version":"2020-12-01","Steps":[{"Name":"FailForExample","Type":"Fail","Arguments":{"ErrorMessage":"Example failure"}}]}'
        role_arn:
            description: The IAM role ARN used by the pipeline.
            returned: when O(pipeline_name) is supplied and the pipeline exists
            type: str
            sample: "arn:aws:iam::123456789012:role/SageMakerPipelineRole"
        pipeline_status:
            description: The current status of the pipeline.
            returned: when O(pipeline_name) is supplied and the pipeline exists
            type: str
            sample: Active
pipelines:
    description: Pipeline summaries returned by SageMaker.
    type: list
    elements: dict
    returned: when O(pipeline_name) is not supplied
    sample:
        - pipeline_name: example-pipeline
          pipeline_display_name: Example pipeline
    contains:
        pipeline_arn:
            description: The ARN of the pipeline.
            returned: when O(pipeline_name) is omitted and this pipeline is listed
            type: str
            sample: "arn:aws:sagemaker:us-east-1:123456789012:pipeline/example-pipeline"
        pipeline_name:
            description: The name of the pipeline.
            returned: when O(pipeline_name) is omitted and this pipeline is listed
            type: str
            sample: example-pipeline
        role_arn:
            description: The IAM role ARN used by the pipeline.
            returned: when O(pipeline_name) is omitted and this pipeline is listed
            type: str
            sample: "arn:aws:iam::123456789012:role/SageMakerPipelineRole"
"""

try:
    import botocore
except ImportError:
    pass  # Handled by AnsibleAWSModule

from ansible_collections.amazon.ai.plugins.module_utils.sagemaker import describe_pipeline
from ansible_collections.amazon.ai.plugins.module_utils.sagemaker import list_pipelines

from ansible.module_utils.common.dict_transformations import camel_dict_to_snake_dict
from ansible.module_utils.common.dict_transformations import snake_dict_to_camel_dict

from ansible_collections.amazon.aws.plugins.module_utils.exceptions import AnsibleAWSError
from ansible_collections.amazon.aws.plugins.module_utils.modules import AnsibleAWSModule
from ansible_collections.amazon.aws.plugins.module_utils.retries import AWSRetry
from ansible_collections.amazon.aws.plugins.module_utils.transformation import scrub_none_parameters


def main() -> None:
    argument_spec = dict(
        pipeline_name=dict(type="str", aliases=["name"]),
        pipeline_version_id=dict(type="int"),
        pipeline_name_prefix=dict(type="str"),
        sort_by=dict(type="str", choices=["Name", "CreationTime"], default="Name"),
        sort_order=dict(type="str", choices=["Ascending", "Descending"], default="Ascending"),
        max_results=dict(type="int"),
    )
    module = AnsibleAWSModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[["pipeline_name", "pipeline_name_prefix"]],
    )
    if module.params.get("pipeline_version_id") is not None and module.params.get("pipeline_name") is None:
        module.fail_json(msg="pipeline_version_id requires pipeline_name.")
    if module.params.get("pipeline_version_id") is not None and module.params["pipeline_version_id"] < 1:
        module.fail_json(msg="pipeline_version_id must be greater than or equal to 1.")
    if module.params.get("max_results") is not None and not 1 <= module.params["max_results"] <= 100:
        module.fail_json(msg="max_results must be between 1 and 100.")
    if module.params.get("pipeline_name_prefix") is not None and module.params["sort_by"] != "Name":
        module.fail_json(msg="sort_by must be Name when pipeline_name_prefix is supplied.")

    try:
        try:
            client = module.client("sagemaker", retry_decorator=AWSRetry.jittered_backoff())
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as e:
            module.fail_json_aws(e, msg="Failed to connect to AWS.")

        pipeline_name = module.params.get("pipeline_name")
        if pipeline_name is not None:
            pipeline = describe_pipeline(
                client,
                pipeline_name,
                pipeline_version_id=module.params.get("pipeline_version_id"),
            )
            if pipeline is None:
                module.exit_json(changed=False, pipeline=dict())
            module.exit_json(changed=False, pipeline=camel_dict_to_snake_dict(pipeline))

        param_names = ("pipeline_name_prefix", "sort_by", "sort_order", "max_results")
        params = {key: module.params.get(key) for key in param_names if module.params.get(key) is not None}
        params = snake_dict_to_camel_dict(scrub_none_parameters(params), capitalize_first=True)
        pipelines = [camel_dict_to_snake_dict(summary) for summary in list_pipelines(client, **params)]
        module.exit_json(changed=False, pipelines=pipelines)
    except AnsibleAWSError as e:
        module.fail_json_aws_error(e)


if __name__ == "__main__":
    main()
