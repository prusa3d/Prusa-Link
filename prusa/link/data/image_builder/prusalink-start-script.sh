# Forward the port 80 to 8080 even on the loopback, so we van ping ourselves
iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -I OUTPUT -p tcp -o lo -d localhost --dport 80 -j REDIRECT --to-ports 8080

set_up_port () {
   # Sets the baudrate and cancels the hangup at the end of a connection
   stty -F "$1" 115200 -hupcl || true
}

message() {
   printf "M117 $2\n" > "$1" || true
}

wifi_nic_name=$(find /sys/class/net -follow -maxdepth 2 -name wireless 2> /dev/null | cut -d / -f 5)
if [ $? -eq 0 ] && [ -n "$wifi_nic_name" ]; then
    /sbin/iwconfig "$wifi_nic_name" power off
    if [ $? -eq 0 ]; then
        printf "Turned off power management for $wifi_nic_name\n"
    fi
fi

username=$(id -nu 1000)

set_up_port "/dev/ttyAMA0"
message "/dev/ttyAMA0" "Starting PrusaLink";

/home/$username/.local/bin/prusalink-boot
rm -f /home/$username/prusalink.pid
export PYTHONOPTIMIZE=2
su $username -c "/home/$username/.local/bin/prusalink -i start"
