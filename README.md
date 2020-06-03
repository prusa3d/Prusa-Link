# Prusa Connect MK3

This program is a compatibility layer between old Prusa printers (MK3 only for the time being) ad Prusa Connect

In its current form it reports basic printer telemetry using config to lan_settings.ini placed in /boot

To install, make sure you have all of the prerequisities
`sudo apt install git python3-dev python3-venv libopenjp2-7-dev libtiff5 pigpiod libsystemd-dev python3-wheel`

ten set up your python3 environment using pip and requirements.txt
finally, turn off bluetooth and disable the linux shell on serial while enabling the serial port in raspi-settings

## Missing info

### Printer states
and how hard will it be to report them from FW
> offline: can be detected from pi
> unknown: don't know what state would this signalise
> ready: difficult to report from fw
> printing: difficult to report from fw
> paused: FW can report this
> finished: probably can be reported from FW
> error: FW cannot report this, maybe pi can switch to this state upon receiving an error
> attention: FW can maybe report this
> harvest: the printer does not know whether the build platfom is attached to it or not

### Telemetry data
and how hard is it to get the values
> progress: M73 but what mode are we in? Silent or Normal? if the info is absent from GCODE, (calc_percent_done() in Marlin_main.cpp)
> filament: is reported periodically in farm mode
> flow: M221 sets it, but how to get it? (extruder_multiply,  extrudemultiply a extruder_multiplier in Marlin_main.cpp)
> speed: M220 sets it, but how to get it? (feedmultiply in Marlin_main.cpp)
> printing_time: M31, does not stop when not printing, need to know printer state
>                M27, shown only whie SD printing, but does not get paused when the print is paused
>                farm mode reports automatically
> estimated_time: needs to know current normal/quiet mode, which the printer knows (of course) but does not report
> x|y|z|e_axis_length: We cannot do this, we don't know this
> material: We do not know anything about the material