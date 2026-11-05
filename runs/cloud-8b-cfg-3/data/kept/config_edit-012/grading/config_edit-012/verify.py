import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "55", "minutes": "16", "zones": "26", "skip_if_rain": "yes"}, "lights": {"on_hour": "12", "off_hour": "52", "brightness": "13"}, "sensors": {"interval_s": "58", "retries": "28", "log_level": "debug"}}')
CHANGED = json.loads('[["irrigation", "zones"], ["sensors", "retries"]]')


def check():
    c = configparser.ConfigParser()
    try:
        if not c.read('garden.ini', encoding="utf-8"):
            return False, 0.0
    except configparser.Error:
        return False, 0.0
    got = {s: dict(c[s]) for s in c.sections()}
    if got == WANT:
        return True, 1.0
    hits = sum(got.get(s, {}).get(k) == WANT[s][k] for s, k in CHANGED)
    return False, 0.5 * hits / len(CHANGED)
