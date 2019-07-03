import datetime
import enum
import functools
import gzip
import json
import os
import re

import boto3


class Config(enum.Enum):
    DDB_TABLE_NAME = enum.auto()
    S3_BUCKET_NAME = enum.auto()
    SMITE_API_LAMBDA_ARN = enum.auto()

    def from_env(self, env=None):
        if env is None:
            env = os.environ
        return env[self.name]


@functools.lru_cache(maxsize=1)
def get_table(env=None):
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(Config.DDB_TABLE_NAME.from_env(env))


gamemode_players = {
    "Arena": 5,
    "Joust (3x3)": 3,
    "Conquest": 5,
    "Assault": 5,
    "Siege": 4,
    "Joust": 1,
    "Unknown": 5,
    "Clash": 5,
    "Duel": 1,
}

gamemode_normalize = {
    "Arena_V3": "Arena",
    "Joust (3x3)": "Joust",
    "Joust 3v3": "Joust",
    "Conquest (3x3)": "Conquest",
    "Conquest (3v3)": "Conquest",
    "Season 6 Conquest": "Conquest",
    "Siege (5v5)": "Siege",
    "ARAM- Asgard": "Assault",
    "ARAM- Asgard (Old)": "Assault",
    "ARAM- Asgard (2019)": "Assault",
}


def clean_description(desc):
    return re.sub(r"((?<=:)(?=[^ ])|\s+)", " ", desc.replace("\u2019", "'")).strip()


def clean_motd(motd):
    clean = {}

    parsed_time = datetime.datetime.strptime(
        motd["startDateTime"], "%m/%d/%Y %I:%M:%S %p"
    )
    parsed_time = parsed_time.replace(tzinfo=datetime.timezone.utc)
    clean["startTime"] = int(parsed_time.timestamp())

    if motd["name"] != motd["title"]:
        clean["internalName"] = motd["name"]
    clean["name"] = motd["title"]

    description_parts = [
        part.replace("</li>", "") for part in motd["description"].split("<li>")
    ]
    description_parts = [part for part in description_parts if part]
    clean["description"] = clean_description(description_parts[0])
    clean["rules"] = []
    clean["unparsedRules"] = []

    clean["gameMode"] = gamemode_normalize.get(motd["gameMode"], motd["gameMode"])

    for rule in map(clean_description, description_parts[1:]):
        clean["rules"].append(rule)
        try:
            if ":" in rule:
                key, value = [x.strip() for x in rule.split(":", 1)]
            else:
                key, value = rule.strip(), None
            if key == "Starting Gold":
                clean["startingGold"] = int(value.replace(",", ""))
            elif (
                key == "Starting/Maximum Cooldown Reduction"
                or key == "Cooldown Reduction"
            ):
                percent = int(
                    value.replace("%", "")
                    .replace("(no use in stacking more CDR)", "")
                    .strip()
                )
                clean["startingCDR"] = percent
                clean["maximumCDR"] = percent
            elif key in ("Starting Cooldown Reduction", "Starting Cooldown"):
                clean["startingCDR"] = int(value.replace("%", ""))
            elif key in ("Maximum Cooldown Reduction", "Maximum Cooldown"):
                clean["maximumCDR"] = int(value.replace("%", ""))
            elif key == "Gods" or key == "God":
                if value == "Owned":
                    clean["godChoice"] = "Owned"
                elif value == "All":
                    clean["godChoice"] = "All"
                else:
                    clean["godChoice"] = "Limited"
            elif key == "Selection":
                clean["godSelection"] = value
            elif key == "Map":
                if not clean["gameMode"] or clean["gameMode"] == "Unknown":
                    clean["gameMode"] = gamemode_normalize.get(value, value)
            elif key == "Infinite Mana":
                clean["infiniteMana"] = True
            elif key == "Starting Level":
                clean["startingLevel"] = int(value)
            elif key == "Increased XP and Gold Spooling":
                clean["fastXPSpooling"] = True
                clean["fastGoldSpooling"] = True
            elif key == "Gp5":
                clean["fastGoldSpooling"] = True
            elif re.match(r"^(First )?suggested by", key, re.I):
                pass
            elif (
                key.lower()
                == "take a random god and 100,000 gold into a hyper speed 5v5 joust."
            ):
                clean["startingGold"] = 100_000
                clean["teamSize"] = 5
                clean["gameMode"] = "Joust"
            elif key in ("Base Heal Disabled", "Fountain Healing Disabled"):
                clean["noBaseHealing"] = True
            elif key == "Only brute minions count when entering an enemy portal":
                clean["arenaScoringPortalBrutesOnly"] = True
            elif key == "Minion deaths don't remove enemy tickets.":
                clean["arenaScoringNoMinionsKills"] = True
            elif key == "Starting Ticket Count":
                clean["arenaScoringStartingTickets"] = int(value)
            else:
                clean["unparsedRules"].append(rule)
        except Exception:
            clean["unparsedRules"].append(rule)

    if clean.get("godChoice") == "Limited" and motd["team1GodsCSV"]:
        clean["allowedGods"] = []
        for csv_key in ("team1GodsCSV", "team2GodsCSV"):
            team_gods = sorted(
                set(
                    [
                        int(god_id)
                        for god_id in motd[csv_key].split(",")
                        if motd[csv_key]
                    ]
                )
            )
            if team_gods not in clean["allowedGods"]:
                clean["allowedGods"].append(team_gods)

    if motd["maxPlayers"] and gamemode_players[clean["gameMode"]] != int(
        motd["maxPlayers"]
    ):
        clean["teamSize"] = int(motd["maxPlayers"])

    return clean


def get_gods():
    gods = json.load(
        boto3.client("lambda").invoke(
            FunctionName=Config.SMITE_API_LAMBDA_ARN.from_env(),
            Payload=json.dumps({"method": "get_gods"}).encode("utf-8"),
        )["Payload"]
    )
    result = {}
    for god in gods:
        result[int(god["id"])] = {
            "name": god["Name"],
            "icon": god["godIcon_URL"].replace("http://", "https://"),
        }
    return result


def scan_table(table):
    pagination = {}
    while True:
        response = get_table().scan(
            Select="ALL_ATTRIBUTES", ConsistentRead=True, **pagination
        )
        yield from response.get("Items", [])
        if response.get("LastEvaluatedKey"):
            pagination["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        else:
            break


def handler(_event=None, _context=None):
    items = scan_table(get_table())
    motds = sorted(
        (clean_motd(json.loads(item["value"])) for item in items),
        key=lambda x: x["startTime"],
        reverse=True,
    )
    data = {"motds": motds, "gods": get_gods()}

    boto3.client("s3").put_object(
        Bucket=Config.S3_BUCKET_NAME.from_env(),
        Key="data.json",
        Body=gzip.compress(json.dumps(data).encode("utf-8")),
        ContentType="application/json",
        ContentEncoding="gzip",
    )
