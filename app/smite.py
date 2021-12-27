import datetime
import enum
import functools
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request


class Config(enum.Enum):
    SMITE_DEVELOPER_ID = enum.auto()
    SMITE_AUTH_KEY = enum.auto()

    def from_env(self, env=None):
        if env is None:
            env = os.environ
        return env[self.name]


class SmiteClient:
    _BASE_URL = "https://api.smitegame.com/smiteapi.svc/"
    _RESPONSE_FORMAT = "json"
    _SESSION_TIMEOUT = 15 * 60

    def __init__(self, dev_id, auth_key, lang=1):
        """
        :param dev_id: Your private developer ID supplied by Hi-rez. Can be requested here:
            https://fs12.formsite.com/HiRez/form48/secure_index.html
        :param auth_key: Your authorization key
        :param lang: the language code needed by some queries, default to english.
        """
        self.dev_id = str(dev_id)
        self.auth_key = str(auth_key)
        self.lang = lang
        self.session = {}
        self.session_timestamp = None

    def _make_request(self, method_name, *args, check_session=True):
        if check_session and (not self.session or not self._cheap_test_session()):
            self.session = self._create_session()
            self.session_timestamp = time.time()
        url = self._build_request_url(method_name, *args)
        response = urllib.request.urlopen(url).read().decode("utf-8")
        return json.loads(response)

    def _build_request_url(self, method_name, *args):
        signature, timestamp = self._create_signature_and_timestamp(method_name)
        session_id = self.session.get("session_id")
        path = [
            method_name + self._RESPONSE_FORMAT,
            self.dev_id,
            signature,
            session_id,
            timestamp,
        ]
        path += [urllib.parse.quote(str(param)) for param in (args or [])]
        url = self._BASE_URL + "/".join(filter(None, path))
        return url

    def _create_signature_and_timestamp(self, method_name):
        now = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
        sig = hashlib.md5(
            (self.dev_id + method_name + self.auth_key + now).encode("utf-8")
        ).hexdigest()
        return (sig, now)

    def _create_session(self):
        self.session = {}
        return self._make_request("createsession", check_session=False)

    def _cheap_test_session(self):
        return self.session and (time.time() - self.session_timestamp) < (
            self._SESSION_TIMEOUT - 60
        )

    def _test_session(self):
        return self.session and "successful" in self._make_request(
            "testsession", check_session=False
        )

    def get_gods(self):
        return self._make_request("getgods", self.lang)

    def get_motd(self):
        return self._make_request("getmotd")


@functools.lru_cache(maxsize=1)
def get_smite_client():
    return SmiteClient(
        Config.SMITE_DEVELOPER_ID.from_env(), Config.SMITE_AUTH_KEY.from_env()
    )


def handler(event, _context=None):
    client = get_smite_client()
    return getattr(client, event["method"])()
