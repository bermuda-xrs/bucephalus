import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "11", "minutes": "60", "zones": "5", "skip_if_rain": "yes"}, "lights": {"on_hour": "49", "off_hour": "3", "brightness": "34"}, "sensors": {"interval_s": "27", "retries": "16", "log_level": "debug"}}')
CHANGED = json.loads('[["lights", "on_hour"], ["sensors", "retries"]]')


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
