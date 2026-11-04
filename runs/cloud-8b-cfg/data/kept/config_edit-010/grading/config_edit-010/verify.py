import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "7", "minutes": "3", "zones": "45", "skip_if_rain": "yes"}, "lights": {"on_hour": "60", "off_hour": "64", "brightness": "11"}, "sensors": {"interval_s": "20", "retries": "57", "log_level": "info"}}')
CHANGED = json.loads('[["sensors", "interval_s"], ["lights", "off_hour"]]')


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
