# si_first_orbital_arithmetic
Aerospace calculations whose inputs are in params.json and whose constants are in constants.json.

## Read the inputs exactly, then convert everything to SI
- Load params.json and constants.json with json.load. Use every key exactly as the task spells it, including nested ones such as params["stage1"]["isp_s"].
- The body named in params (earth, mars, moon, sun) selects the constants: mu and radius come from constants[body], never from another body.
- Convert before computing: km to m (x 1000), km/h to m/s (/ 3.6), minutes to s (x 60), days to s (x 86400), degrees to radians (math.radians).

## Build the quantity the formula actually needs
- An altitude is not an orbital radius: r = the body's radius + the altitude in m. A value already given as a radius (a key ending in _m, or geo_radius) is used as it is.
- Specific impulse in seconds becomes exhaust velocity: v_e = isp_s * g0. Each stage uses its own m0 and mf, and the stage delta-v values add; mass dropped at separation does not travel with the next stage.
- A viscosity in Pa*s is dynamic viscosity: Reynolds = rho * v * L / mu.
- A plane change combined with a burn is one change of velocity vector (law of cosines on the two velocities), not the sum of two burns.
- Two orbital periods give the synodic period by reciprocals, 1/S = |1/T1 - 1/T2|. A Hohmann transfer takes half the period of the transfer ellipse, whose semi-major axis is the mean of the two radii.

## Check the number before writing it
- Compare the magnitude with a known reference: low Earth orbit is about 7.7 km/s and about 90 minutes; escape from Earth's surface is about 11.2 km/s; one chemical stage gives a few km/s. A result off by a factor near 1000, 60, 3.6 or 57.3 is an unconverted unit.
- Report in the unit the answer key names (period_minutes is minutes, days_to_... is days); otherwise SI base units.
- Write output/answer.json with exactly the required keys, in one run_code program that reads the inputs, computes and writes the file.
