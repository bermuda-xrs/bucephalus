import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "30", "minutes": "29", "zones": "76", "skip_if_rain": "yes"}, "lights": {"on_hour": "58", "off_hour": "26", "brightness": "18"}, "sensors": {"interval_s": "40", "retries": "57", "log_level": "info"}}')
CHANGED = json.loads('[["lights", "on_hour"], ["irrigation", "zones"]]')


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
