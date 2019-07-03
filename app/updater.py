import datetime
import enum
import functools
import json
import os

import boto3


class Config(enum.Enum):
    DDB_TABLE_NAME = enum.auto()
    TWITTER_API_LAMBDA_ARN = enum.auto()
    SMITE_API_LAMBDA_ARN = enum.auto()
    TABLE_EXPORT_LAMBDA_ARN = enum.auto()

    def from_env(self, env=None):
        if env is None:
            env = os.environ
        return env[self.name]


@functools.lru_cache(maxsize=1)
def get_table(env=None):
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(Config.DDB_TABLE_NAME.from_env(env))


def convert_motd_details_to_dynamodb_item(details):
    parsed = datetime.datetime.strptime(
        details["startDateTime"], "%m/%d/%Y %I:%M:%S %p"
    )
    parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return {
        "key": int(parsed.timestamp()),
        "value": json.dumps(details, separators=(",", ":"), sort_keys=True),
    }


def get_smite_motds():
    return json.load(
        boto3.client("lambda").invoke(
            FunctionName=Config.SMITE_API_LAMBDA_ARN.from_env(),
            Payload=json.dumps({"method": "get_motd"}).encode("utf-8"),
        )["Payload"]
    )


def handler(_event=None, _context=None):
    items = [convert_motd_details_to_dynamodb_item(motd) for motd in get_smite_motds()]

    requires_export = False

    for idx, item in enumerate(items[::-1], start=1):
        is_latest_motd = idx == len(items)

        stored_item = get_table().get_item(
            Key={"key": item["key"]},
            # use consistent read for latest, since we might tweet based on it
            ConsistentRead=is_latest_motd,
        )

        if stored_item.get("Item"):
            continue

        print(f"Putting {item['key']}")
        get_table().put_item(Item=item)
        requires_export = True

        if is_latest_motd:
            print("Tweeting")
            title = json.loads(item["value"])["title"]
            status = f"{title} - https://motd.today/?id={item['key']}"
            boto3.client("lambda").invoke(
                FunctionName=Config.TWITTER_API_LAMBDA_ARN.from_env(),
                InvocationType="Event",
                Payload=json.dumps({"status": status}).encode("utf-8"),
            )

    if requires_export:
        print("Exporting")
        boto3.client("lambda").invoke(
            FunctionName=Config.TABLE_EXPORT_LAMBDA_ARN.from_env(),
            InvocationType="Event",
        )

    return items
