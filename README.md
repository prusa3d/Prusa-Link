# Prusa Link

This program is a compatibility layer between old Prusa printers (MK3(s) for the time being) ad Prusa Connect

It supports configuration of multiple directories which are going to be scanned for gcodes.
It also reports the printer SD files (short DOS names for now)
Every reported gcode file can be printed, the printer SD files using the printer firmware
The other ones through USB (like Pronterface)

The user can command the printer directly using gcodes or high level commands such as
PAUSE_PRINT, RESUME_PRINT and so on

It also reports printer information like positions and temperatures and determines the printer state.

The configuration file can be found in the user directory under `.config/Prusa_link`

## Setup
Note: Try not soldering the pin 16 (BCM_23). It may save you one RPi Zero purchase down the line.
The pin is connected straight to ground and we aren't sure, if that can destroy it.
But mine died along with the pin 15 (BCM_22) used for resetting the printer

The RPi pre-configuration is the same as for OctoPrint, which is described in our
[KnowledgeBase](https://help.prusa3d.com/en/article/octoprint-building-an-image-for-raspberry-pi-zero-w_2182)
Basically, you need the bluetooth not handled by the Pis main UART interface,
prevent the linux shell from being available on the Pis serial interface and you
need to connect the Pi to the internet through wifi

To install, make sure you have all of the prerequisites:
```bash
# install system dependencies
$ sudo apt install git python3-pip libsystemd-dev python3-wheel pigpio

# install python package from git, While git is private, you need installed deploy ssh key
$ sudo pip3 install git+ssh://git@github.com/prusa3d/Prusa-Connect-MK3.git

# configure as a service
$ sudo prusa_link_install
```

After that PrusaLink should start on boot

## FW changes that would help improve PrusaLink
Tracked on JIRA. Previously [here](https://docs.google.com/spreadsheets/d/1G0u_1Gzawj-5uneZbILgja20QJlSVc8VyH3Hz-eZ4vw/edit#gid=0) on Google Docs
