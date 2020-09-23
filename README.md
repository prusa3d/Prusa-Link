# Old Buddy

This program is a compatibility layer between old Prusa printers (MK3 only for the time being) ad Prusa Connect

In its current form it reports basic printer telemetry using config to lan_settings.ini placed in /boot and
can be sent GCODES remotely from the web. Now it also displays printer state semi-accurately

## Setup
The RPi pre-configuration is the same as for OctoPrint, which is described in our
[KnowledgeBase](https://help.prusa3d.com/en/article/octoprint-building-an-image-for-raspberry-pi-zero-w_2182)
Basically, you need the bluetooth not handled by the Pis main UART interface,
prevent the linux shell from being available on the Pis serial interface and you
need to connect the Pi to the internet through wifi

To install, make sure you have all of the prerequisites:
```bash
# install system dependencies
$ sudo apt install git python3-pip libsystemd-dev python3-wheel

# install python package from git, While git is private, you need installed deploy ssh key
$ sudo pip3 install git+ssh://git@github.com/prusa3d/Prusa-Connect-MK3.git

# configure as a service
$ sudo prusa_link_install
```

After that old buddy shall start with the pi

## Missing/usefull telemetry gathering gcodes
Tracked here: https://docs.google.com/spreadsheets/d/1G0u_1Gzawj-5uneZbILgja20QJlSVc8VyH3Hz-eZ4vw/edit#gid=0
