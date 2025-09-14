Python script to read an analogue meter and convert the results into a number.
The script looks for a file called latest.jpg, converts the size to a preset constant 500 * 500 size, then blurs the image, calibrates the dead zone out of the meter and converts the meter to a number.
The system detects bad conversions and also asks the user to decide if the image is good or bad. The image is saved in an archive directory and the name includes a timestamp and quality. 
An MQtt message is produced is the value is above a predefined level.
The conversion is good when the image is placed in the centre of the screen and there are no lines other than the pointer visible. The system is also suseptable to shaows and poor lighting.
This is version 1 of the reader - also working on a more advanced version where we can find the meter and automatically position it in the frame prior to analysis.
