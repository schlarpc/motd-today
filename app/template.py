import argparse
import inspect
import os.path

import packmodule
from awacs.helpers.trust import get_lambda_assumerole_policy
from troposphere import GetAtt, Join, Parameter, Ref, Select, Split, Template
from troposphere.awslambda import Code, Environment, Function, Permission
from troposphere.cloudformation import Stack
from troposphere.dynamodb import AttributeDefinition, KeySchema, Table
from troposphere.events import Rule, Target
from troposphere.iam import Role
from troposphere.logs import LogGroup

from . import exporter, smite, twitter, updater


def log_group_for_function(function):
    return LogGroup(
        function.title + "LogGroup",
        LogGroupName=Join("/", ["/aws/lambda", Ref(function)]),
    )


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("website_template", type=os.path.abspath)
    parser.add_argument("website_parameters", nargs="*")
    return parser.parse_args()


def create_template(website_template, website_parameters):
    template = Template()

    runtime = template.add_parameter(
        Parameter("LambdaRuntime", Default="python3.11", Type="String")
    )

    smite_developer_id = template.add_parameter(
        Parameter("SmiteDeveloperId", Type="String")
    )

    smite_auth_key = template.add_parameter(
        Parameter("SmiteAuthKey", Type="String", NoEcho=True)
    )

    twitter_consumer_key = template.add_parameter(
        Parameter("TwitterConsumerKey", Type="String")
    )

    twitter_consumer_secret = template.add_parameter(
        Parameter("TwitterConsumerSecret", Type="String", NoEcho=True)
    )

    twitter_access_key = template.add_parameter(
        Parameter("TwitterAccessKey", Type="String")
    )

    twitter_access_secret = template.add_parameter(
        Parameter("TwitterAccessSecret", Type="String", NoEcho=True)
    )

    table = template.add_resource(
        Table(
            "StorageTable",
            AttributeDefinitions=[
                AttributeDefinition(AttributeName="key", AttributeType="N")
            ],
            KeySchema=[KeySchema(AttributeName="key", KeyType="HASH")],
            BillingMode="PAY_PER_REQUEST",
            DeletionPolicy="Retain",
        )
    )

    website = template.add_resource(
        Stack("Website", TemplateURL=website_template, Parameters=website_parameters)
    )

    role = template.add_resource(
        Role(
            "LambdaRole",
            AssumeRolePolicyDocument=get_lambda_assumerole_policy(),
            ManagedPolicyArns=[
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
                "arn:aws:iam::aws:policy/AmazonS3FullAccess",
                "arn:aws:iam::aws:policy/AWSLambdaFullAccess",
            ],
        )
    )

    smite_api_function = template.add_resource(
        Function(
            "SmiteApiFunction",
            Code=Code(ZipFile=inspect.getsource(smite)),
            Handler="index.handler",
            MemorySize=256,
            Timeout=30,
            Runtime=Ref(runtime),
            Role=GetAtt(role, "Arn"),
            Environment=Environment(
                Variables={
                    smite.Config.SMITE_DEVELOPER_ID.name: Ref(smite_developer_id),
                    smite.Config.SMITE_AUTH_KEY.name: Ref(smite_auth_key),
                }
            ),
        )
    )
    smite_api_logs = template.add_resource(log_group_for_function(smite_api_function))

    twitter_api_function = template.add_resource(
        Function(
            "TwitterApiFunction",
            Code=Code(ZipFile=inspect.getsource(twitter)),
            Handler="index.handler",
            MemorySize=256,
            Timeout=30,
            Runtime=Ref(runtime),
            Role=GetAtt(role, "Arn"),
            Environment=Environment(
                Variables={
                    twitter.Config.TWITTER_CONSUMER_KEY.name: Ref(twitter_consumer_key),
                    twitter.Config.TWITTER_CONSUMER_SECRET.name: Ref(
                        twitter_consumer_secret
                    ),
                    twitter.Config.TWITTER_ACCESS_KEY.name: Ref(twitter_access_key),
                    twitter.Config.TWITTER_ACCESS_SECRET.name: Ref(
                        twitter_access_secret
                    ),
                }
            ),
        )
    )
    twitter_api_logs = template.add_resource(
        log_group_for_function(twitter_api_function)
    )

    table_export_function = template.add_resource(
        Function(
            "TableExportFunction",
            Code=Code(ZipFile=packmodule.pack(inspect.getsource(exporter))),
            Handler="index.handler",
            MemorySize=512,
            Timeout=30,
            Runtime=Ref(runtime),
            Role=GetAtt(role, "Arn"),
            Environment=Environment(
                Variables={
                    exporter.Config.DDB_TABLE_NAME.name: Ref(table),
                    exporter.Config.S3_BUCKET_NAME.name: Select(
                        5, Split(":", GetAtt(website, "Outputs.ContentBucketArn"))
                    ),
                    exporter.Config.SMITE_API_LAMBDA_ARN.name: GetAtt(
                        smite_api_function, "Arn"
                    ),
                }
            ),
        )
    )
    table_export_logs = template.add_resource(
        log_group_for_function(table_export_function)
    )

    update_check_function = template.add_resource(
        Function(
            "UpdateCheckFunction",
            Code=Code(ZipFile=inspect.getsource(updater)),
            Handler="index.handler",
            MemorySize=256,
            Timeout=30,
            Runtime=Ref(runtime),
            Role=GetAtt(role, "Arn"),
            Environment=Environment(
                Variables={
                    updater.Config.TWITTER_API_LAMBDA_ARN.name: GetAtt(
                        twitter_api_function, "Arn"
                    ),
                    updater.Config.SMITE_API_LAMBDA_ARN.name: GetAtt(
                        smite_api_function, "Arn"
                    ),
                    updater.Config.TABLE_EXPORT_LAMBDA_ARN.name: GetAtt(
                        table_export_function, "Arn"
                    ),
                    updater.Config.DDB_TABLE_NAME.name: Ref(table),
                }
            ),
        )
    )

    update_check_logs = template.add_resource(
        log_group_for_function(update_check_function)
    )

    update_check_rule = template.add_resource(
        Rule(
            "UpdateCheckRule",
            ScheduleExpression="rate(5 minutes)",
            Targets=[
                Target(
                    Id=Ref(update_check_function),
                    Arn=GetAtt(update_check_function, "Arn"),
                )
            ],
            DependsOn=[
                update_check_logs,
                table_export_logs,
                smite_api_logs,
                twitter_api_logs,
            ],
        )
    )

    template.add_resource(
        Permission(
            "UpdateCheckPermission",
            Action="lambda:InvokeFunction",
            FunctionName=Ref(update_check_function),
            SourceArn=GetAtt(update_check_rule, "Arn"),
            Principal="events.amazonaws.com",
        )
    )

    return template


if __name__ == "__main__":
    args = get_args()
    print(
        create_template(
            args.website_template,
            dict(p.split("=", 1) for p in args.website_parameters),
        ).to_json()
    )
