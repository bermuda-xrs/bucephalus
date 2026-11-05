import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "52", "minutes": "48", "zones": "39", "skip_if_rain": "no"}, "lights": {"on_hour": "58", "off_hour": "46", "brightness": "36"}, "sensors": {"interval_s": "52", "retries": "15", "log_level": "debug"}}')
CHANGED = json.loads('[["irrigation", "start_hour"], ["lights", "brightness"]]')


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
