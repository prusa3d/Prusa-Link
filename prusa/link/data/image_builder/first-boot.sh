set_up_port () {
   # Sets the baudrate and cancels the hangup at the end of a connection
   stty -F "$1" 115200 -hupcl || true
}

message() {
   printf "M117 $2\n" > "$1" || true
}

set_up_port "/dev/ttyAMA0"
message "/dev/ttyAMA0" "Please wait < 10min";

for i in {0..5}; do
 set_up_port "/dev/ttyACM$i"
done

sleep 8

for i in {0..5}; do
 message "/dev/ttyACM$i" "Please wait < 10min"
done

# This generates the host keys for the ssh server to work
ssh-keygen -A
