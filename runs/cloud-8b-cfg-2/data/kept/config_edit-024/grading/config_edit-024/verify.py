import configparser
import json

WANT = json.loads('{"irrigation": {"start_hour": "19", "minutes": "49", "zones": "30", "skip_if_rain": "no"}, "lights": {"on_hour": "33", "off_hour": "36", "brightness": "21"}, "sensors": {"interval_s": "18", "retries": "16", "log_level": "debug"}}')
CHANGED = json.loads('[["lights", "on_hour"], ["lights", "off_hour"]]')


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
