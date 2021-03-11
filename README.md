# Prusa Link

This program is a compatibility layer between old Prusa printers (MK3(s) for the time being) and Prusa Connect.

It supports configuration of multiple directories which are going to be scanned for gcodes.
It also reports the printer SD files (short DOS names for now)
Every reported gcode file can be printed, the printer SD files using the printer firmware.
The other ones through USB (like Pronterface).

The user can command the printer directly using gcodes or high level commands such as PAUSE_PRINT, RESUME_PRINT and so on.

It also reports printer information like positions and temperatures and determines the printer state.

## Setup
The RPi pre-configuration is the same as for OctoPrint, which is described in
our [KnowledgeBase](https://help.prusa3d.com/en/article/octoprint-building-an-image-for-raspberry-pi-zero-w_2182)
Basically, you need the bluetooth not handled by the Pis main UART interface,
prevent the linux shell from being available on the Pis serial interface and you
need to connect the Pi to the internet through WiFI.

To install, make sure you have all the prerequisites:

```bash
# install system dependencies
sudo apt install git python3-pip python3-wheel pigpio libcap-dev

# install Prusa Link from GitHub, While the repo is private,
# you'll need to install an ssh deploy key
sudo pip3 install git+ssh://git@github.com/prusa3d/Prusa-Link.git
```

##Config
Prusa Link is configured using `/etc/Prusa-Link/prusa-link.ini`.

Some legacy settings are stored in `/var/tmp/Prusa-Link/config.yaml`.


##Usage
The executable is called `prusa-link` and can be used to control the daemon, if you want to run it directly, use the -f option

for more info about commandline options use:

```bash
prusa-link --help
```

To start Prusa Link on boot, add this line to `/etc/rc.local`

```
su pi -c 'prusa-link start'
```
