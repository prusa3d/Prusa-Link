# Contribution

## Developement

**On Rasbian**:

Running on foreground without install:

```sh
python3 -m prusa.link -f
```

**From desktop**:

`/dev/ttyACM0` can be USB <-> UART printer port

```sh
python3 -m prusa.link -f -s /dev/ttyACM0
```

When you install `socat` tool to RaspberryPi Zero, you can create use
virtual TCP <-> UART port.

```
socat PTY,link=$HOME/ttyAMA0,raw,wait-slave EXEC:'"ssh pi@prusa-link socat - /dev/ttyAMA0,nonblock,raw"'
# in another terminal
python3 -m prusa.link -f -s $HOME/ttyAMA0
```

**Own static files**:

```sh
PRUSA_LINK_STATIC=./my_static python3 -m prusa.link -f
```

**Communication debug**:
prusalink -f -I -i -l urllib3.connectionpool=DEBUG -l connect-printer=DEBUG

## Testing

The tests are run by CI (`.github/workflows/python-tests.yml`) on Python
3.9, 3.11 and 3.13 for every pull request and every push to `master`. Run the
same checks locally before you send a change, from the repository root and
with the virtual environment active (`source .venv/bin/activate`):

```sh
pip install -r requirements.txt -r requirements-multi.txt
pip install pylint~=2.17.7 'ruff~=0.16.10' pytest pytest-doctestplus \
    pytest-mypy pytest-cov mock
```

Install also `prusa-connect-sdk-printer` and `gcode-metadata` from GitHub
(see the workflow), otherwise `prusa.connect` cannot be imported. The
system packages `libcap-dev` and `libturbojpeg` are needed too.

**Checks** (all of them must pass):

```sh
ruff check .                        # style, see ruff.toml
PYTHONPATH=. pylint prusa/link tests
PYTHONPATH=. pytest -v --no-cov --mypy --doctest-plus --doctest-rst prusa/link
PYTHONPATH=. pytest -v --mypy --doctest-plus --doctest-rst tests
```

While developing, a single file or test is enough (`pyproject.toml` sets
`pythonpath` and the coverage report, so it works without `PYTHONPATH`):

```sh
pytest tests/test_serial_queue.py
pytest tests/test_util.py -k fat --no-cov
```

The same linters run as `pre-commit` hooks (`pre-commit install`).

**Writing tests**:

- Tests live in `tests/`, one `test_<module>.py` per tested module, with a
  docstring on the module and on every test. Common helpers are in
  `tests/util.py` and `tests/web_util.py`, shared fixtures in
  `tests/conftest.py`.
- Test the behaviour, not the implementation: do not assert how a private
  function is called (positional or keyword arguments), if the result does not
  depend on it. Use constants from the code (`STATS_EVERY`, ...) instead of
  copying their values.
- Tests must not need a printer, the network, a real serial port or the
  Raspberry Pi hardware: mock them. Do not start real threads, do not wait for
  real time (patch `sleep` and `time`) and clean up the singletons
  (`MCSingleton`) the test created.
- Pseudo terminals (`pty`) are the only exception for the serial port, those
  tests are skipped outside Linux.
- Assert what was logged (`caplog`), when the log is the only result.
- New code should come with tests; the coverage table is commented to every
  pull request (the numbers are the same for all Python versions). Do not
  lower it.
- The code must work on Python 3.9, do not use newer syntax (`match`,
  `X | Y` in annotations evaluated at runtime, ...).
