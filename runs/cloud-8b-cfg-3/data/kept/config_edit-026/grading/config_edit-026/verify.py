import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "54", "minutes": "24", "zones": "46", "skip_if_rain": "yes"}, "lights": {"on_hour": "21", "off_hour": "38", "brightness": "14"}, "sensors": {"interval_s": "50", "retries": "59", "log_level": "warn"}}')
CHANGED = json.loads('[["lights", "on_hour"], ["sensors", "interval_s"]]')


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
