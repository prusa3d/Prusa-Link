# Contribution

## Developement

Running on foreground without install:

```sh
PRUSA_LINK_STATIC=./static PRUSA_LINK_TEMPLATES=./templates python3 -m prusa.link -f -d
```

Virtual serial port over TCP:

```sh
socat PTY,link=$HOME/ttyAMA0,raw,wait-slave EXEC:'"ssh pi@prusa-link socat - /dev/ttyAMA0,nonblock,raw"'
```