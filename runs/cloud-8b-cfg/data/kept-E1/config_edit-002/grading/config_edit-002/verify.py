import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "32", "minutes": "24", "zones": "29", "skip_if_rain": "no"}, "lights": {"on_hour": "11", "off_hour": "33", "brightness": "52"}, "sensors": {"interval_s": "26", "retries": "49", "log_level": "info"}}')
CHANGED = json.loads('[["irrigation", "start_hour"], ["lights", "on_hour"]]')


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
