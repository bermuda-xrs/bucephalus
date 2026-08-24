# Aerospace calculation discipline

## 1. Normalize before computing
Convert every input to SI base units as the first step, and write each
quantity with its unit attached. km→m, km/h→m/s, hours/minutes→s,
degrees→radians. Never compute on a mixed-unit expression.

## 2. Identify the body and the radius
State which body the problem concerns and read that body's μ and R from
constants.json. When given an ALTITUDE, orbital radius = body radius +
altitude. When given a radius directly, use it as-is. Write down which
one the problem gave you before using it.

## 3. Expand the compound constants
Isp in seconds is not a velocity: exhaust velocity = Isp × g0.
Any trig argument must already be in radians.
Before writing the answer, check the magnitude against a known reference
(LEO velocity ≈ 7.7 km/s, Earth escape ≈ 11.2 km/s, GEO ≈ 3.1 km/s).
