import enum
import os

import tweepy


class Config(enum.Enum):
    TWITTER_CONSUMER_KEY = enum.auto()
    TWITTER_CONSUMER_SECRET = enum.auto()
    TWITTER_ACCESS_KEY = enum.auto()
    TWITTER_ACCESS_SECRET = enum.auto()

    def from_env(self, env=None):
        if env is None:
            env = os.environ
        return env[self.name]


def handler(event, _context=None):
    auth = tweepy.OAuthHandler(
        Config.TWITTER_CONSUMER_KEY.from_env(),
        Config.TWITTER_CONSUMER_SECRET.from_env(),
    )
    auth.set_access_token(
        Config.TWITTER_ACCESS_KEY.from_env(), Config.TWITTER_ACCESS_SECRET.from_env()
    )
    api = tweepy.API(auth)
    api.update_status(status=event["status"])
