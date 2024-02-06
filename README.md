# PrusaLink

PrusaLink is a compatibility layer between 8-bit Prusa 3D printers
(MK2.5, MK2.5S, MK3, MK3S and MK3S+) and PrusaConnect, which lets you
control and monitor your 3D printer from anywhere.
Get more info at [connect.prusa3d.com](https://connect.prusa3d.com/)

PrusaLink also provides a local web interface:
[Prusa-Link-Web](https://github.com/prusa3d/Prusa-Link-Web)


## Setup
To use PrusaLink please follow our
[Setup Guide](https://help.prusa3d.com/guide/prusalink-and-prusa-connect-mk3-s-_221744)

### Login
If you wish to log into the console environment and haven't changed the
credentials, you'll need these default ones:

```
username: jo
password: raspberry
```

## Dev Setup
If using the Raspberry Pi pins, follow the guide above for the hardware
preparation. Pins can be used even on regular (non-Zero) Pis
through Dupont jumper cables. Just make sure those make proper contact
with the Einsy board. A connection over USB is also possible,
making PrusaLink compatible with pretty much any Linux system,
but since the RPi has been used as a reference, please excuse the Debian
specific instructions.

If using the Pi, create your micro SD card the usual way,
a Lite image will do nicely.
Just in case, here's a guide: https://www.youtube.com/watch?v=ntaXWS8Lk34

### UART over GPIO pins
On some RPis, the main UART is handling Bluetooth, so the printer
communication would get handled by a miniUART, which doesn't work for us.
To disable Bluetooth, add these lines into `config.txt` which is located in
the Pi's boot partition.
```ini
[all]
enable_uart=1
dtoverlay=disable-bt
```

### Installation
PrusaLink needs libpcap headers installed to name its OS threads.
Git and Pip are needed for installation, while pigpio is only needed if the
RPi GPIO pins are to be used.

```bash
sudo apt install git python3-pip pigpio libcap-dev libmagic1 libturbojpeg0 libatlas-base-dev python3-numpy libffi-dev libopenblas0

# If you are using different distro (e.g. Ubuntu), use libturbojpeg library
# instead of libturbojpeg0

# for the Raspberry Pi camera module support
# pre-installed on the newer Raspberry Pi OS images post September 2022
sudo apt install -y python3-libcamera --no-install-recommends

pip install PrusaLink

# Or install straight from GitHub
pip install git+https://github.com/prusa3d/gcode-metadata.git
pip install git+https://github.com/prusa3d/Prusa-Connect-SDK-Printer.git
pip install git+https://github.com/prusa3d/Prusa-Link.git
```

## Config
PrusaLink behavior can be altered using command arguments and configuration
files. The default configuration path is `/etc/prusalink/prusalink.ini` and
does not get created automatically. The configuration documentation can be
found under `prusa/link/data/prusalink.ini`. The executable argument
documentation is provided in the standard help text screen shown after
running `prusalink --help`

The `prusa_printer_settings.ini` file is created by the PrusaLink wizard,
and can be downloaded from the PrusaConnect settings page once you
 register your printer.

### Configuring PrusaLink on the SD card
If you need to manually configure PrusaLink on the SD created from our image,
it now comes with an auto-copy script. Put your `prusalink.ini` or
`prusa_printer_settings.ini` files into the boot portion of the SD,
*That's the only one that shows up under Windows or Mac,*
and they will get copied over to their default locations on the next boot.

### Permission denied
Make sure the user you're running PrusaLink under is a member of the group
**dialout**. To add it, run

```sudo usermod -a -G dialout <username>```

then log out and in with that user.

### Access on port 80
PrusaLink has a local web interface, to make it accessible
on the default port 80, either start it as root and configure the user to which
it should de-elevate itself after the web server is up, or start it as a normal
user on port 8080 - or any other, then redirect the port 80 to the port
PrusaLink is listening on using these commands.

### Running behind a reverse-proxy
If you got a proxy that changes the URI path, add the
X-Forwarded-Prefix header. PrusaLink will use it to construct the correct
URLs for the web interface.

```bash
# use -i to specify the interface affected
iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080
```
PrusaLink advertises itself on the local network. This makes it visible
in PrusaSlicer under Physical Printers -> Browse. To advertise port 80,
the instance has to be able to ping itself. This can be done by setting up a
similar redirect on the loopback interface
```bash
iptables -t nat -I OUTPUT -p tcp -o <loopback device name> -d localhost --dport 80 -j REDIRECT --to-ports 8080
```

### Multi-instance
If you want to connect multiple printers to a single pi, have a look at
[MULTIINSTANCE.md](MULTIINSTANCE.md)

## Usage
By default, the executable starts the daemon process and exits.
The executable is called `prusalink` and can be used to control the daemon,
if you want to run it in your terminal instead, use the `-f` option
To get the most recent help screen use `prusalink --help`, here's
what it says in 0.7.0
```
usage: prusalink [-h] [-f] [-c <file>] [-p <FILE>] [-a <ADDRESS>] [-t <PORT>]
                 [-I] [-s <PORT>] [-i] [-d] [-l MODULE_LOG_LEVEL] [--profile]
                 [command]

PrusaLink daemon.

positional arguments:
  command               daemon action (start|stop|restart|status) (default:
                        start)

options:
  -h, --help            show this help message and exit
  -f, --foreground      run as script on foreground
  -c <file>, --config <file>
                        path to config file (default:
                        /etc/prusalink/prusalink.ini)
  -p <FILE>, --pidfile <FILE>
                        path to pid file
  -a <ADDRESS>, --address <ADDRESS>
                        IP listening address (host or IP)
  -t <PORT>, --tcp-port <PORT>
                        TCP/IP listening port
  -I, --link-info       /link-info debug page
  -s <PORT>, --serial-port <PORT>
                        Serial (printer's) port or 'auto'
  -i, --info            more verbose logging level INFO is set
  -d, --debug           DEBUG logging level is set
  -l MODULE_LOG_LEVEL, --module-log-level MODULE_LOG_LEVEL
                        sets the log level of any submodule(s). use
                        <module_path>=<log_level>
  --profile             Use cProfile for profiling application.
```
