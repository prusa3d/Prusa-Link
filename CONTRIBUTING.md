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
