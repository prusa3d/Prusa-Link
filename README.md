# Old Buddy

This program is a compatibility layer between old Prusa printers (MK3 only for the time being) ad Prusa Connect

In its current form it reports basic printer telemetry using config to lan_settings.ini placed in /boot and
can be sent GCODES remotely from the web. Now it also displays printer state semi-accurately

## Setup
The RPi pre-configuration is the same as for OctoPrint, which is described in our
[KnowledgeBase](https://help.prusa3d.com/en/article/octoprint-building-an-image-for-raspberry-pi-zero-w_2182)
Basically, you need the bluetooth not handled by th Pis main UART interface, stop the linux shell from being
available on the Pis serial interface and connect to the wifi

To install, make sure you have all of the prerequisites:  
`sudo apt install git htop python3-dev python3-pip libsystemd-dev python3-wheel`
`sudo pip3 install -r requirements.txt`

then install old buddy
`sudo python3 setup.py install`

and let it configure its autostart service
`sudo old_buddy_install`

After that old buddy shall start with the pi

## Missing info

### Telemetry data
and how hard is it to get the values

> We need to know if we are in silent or normal mode for progress and esttimated_time
> progress: if the info is absent from GCODE, (calc_percent_done() in Marlin_main.cpp)
> filament: (extruded ever) some variant of it is reported periodically in farm mode
> flow: M221 sets it, but how to get it? (extruder_multiply,  extrudemultiply a extruder_multiplier in Marlin_main.cpp)
> speed: M220 sets it, but how to get it? (feedmultiply in Marlin_main.cpp)
