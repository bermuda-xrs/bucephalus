import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "22", "minutes": "39", "zones": "23", "skip_if_rain": "no"}, "lights": {"on_hour": "7", "off_hour": "76", "brightness": "28"}, "sensors": {"interval_s": "13", "retries": "14", "log_level": "warn"}}')
CHANGED = json.loads('[["lights", "off_hour"], ["irrigation", "minutes"]]')


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
