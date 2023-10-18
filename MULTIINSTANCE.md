# PrusaLink multi instance
In this mode, an instance of PrusaLink is created for each new printer
detected on any of the USB ports of the host system, allowing the user
to connect multiple printers using a single Raspberry Pi.

## Setup
The multi instance image requires the same setup as the regular one,
but there are some differences

1) The multi-instance manager does not connect to printers on the GPIO
pins as the device udev auto-detection in Linux does not work on those
2) Cameras automatically connect to the first instance only. If you wish
to use for example a camera for each printer, you'll need to manually
copy over relevant configuration
3) In this image, the manager of these PrusaLink instances is run as root.
However web interface of the instance manager is run under the user account.

### Cameras
The temporary process of connecting multiple cameras is not user friendly
and requires manual work. This will change in the future.
The process is as follows:
1) Connect all cameras you wish to use and let them connect to the first
instance
2) Open the web interface of the first instance and under cameras, save
every camera manually. This will create a configuration section for each
camera in `prusa_printer_settings.ini` of the first instance
3) Using ssh, navigate to `/etc/prusalink/prusalink1.ini` and open it
4) Turn off the camera auto-detection in the first instance by adding
this section into the file
    ```
    [cameras]
    auto_detect = False
    ```
5) Navigate to `/home/<username>/PrusaLink1` and open
`prusa_printer_settings.ini`
6) Move the section corresponding to each camera over to the instance in which
you wish to use it. The camera sections have hashes as names,
the order of which is noted in the section `[camera_order]`
7) Move the camera order entry for each camera as well.
A camera order section with a single camera in it looks like this
    ```
    [camera_order]
    1 = asdfghjkl
    ```
8) After a reboot, the cameras should be connected to the correct instances

## Running the manager
To run PrusaLink in the multi-instance mode run `prusalink-manager start`
as root. There are other options allowing you to specify which user to run the
instances and web under. The default is UID = 1000

Here's the help output of prusalink-manager

```
Multi-instance suite for PrusaLink

positional arguments:
  {start,stop,clean,rescan}
                        Available commands
    start               Start the instance managing daemon (needs root
                        privileges)
    stop                Stop any manager daemon running (needs root
                        privileges)
    clean               Danger! cleans all PrusaLink multi-instance
                        configuration
    rescan              Notify the daemon a printer has been connected

options:
  -h, --help            show this help message and exit
  -i, --info            include log messages up to the INFO level
  -d, --debug           include log messages up to the INFO level
  -u USERNAME, --username USERNAME
                        Which users to use for running and storing everything
  -p PREPEND_EXECUTABLES_WITH, --prepend-executables-with PREPEND_EXECUTABLES_WITH
                        Environment variables and path to the executables
                        directory
```
