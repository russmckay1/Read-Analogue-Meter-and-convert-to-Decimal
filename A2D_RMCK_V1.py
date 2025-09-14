import cv2
import numpy as np
import os
import shutil
from datetime import datetime
import tkinter as tk
from PIL import Image, ImageTk
import paho.mqtt.client as mqtt

#----------------- CONFIG ----------------
image_name = 'latest.jpg'
archive_dir = 'archive'
output_value_file = 'value.txt'
meter_min_angle = 225
meter_max_angle = 137
meter_min_value = 0
meter_max_value = 120
image_size = (500, 500)
update_interval = 1000

mqtt_broker = "russ-mckay.dyndns.org"
mqtt_port = 1886
mqtt_topic = "meter_workorder"
mqtt_payload = "generate_workorder"

blur_kernel = 21
blur_archive = False
# -----------------------------------------

os.makedirs(archive_dir, exist_ok=True)

client = mqtt.Client()
try:
    client.connect(mqtt_broker, mqtt_port, 60)
    mqtt_connected = True
except Exception as e:
    print(f"MQTT connection failed: {e}")
    mqtt_connected = False

workorder_sent = False
last_archived_file = None

def process_image():
    global last_archived_file
    if not os.path.exists(image_name):
        return None, None

    img = cv2.imread(image_name)
    if img is None:
        return None, None

    img = cv2.resize(img, image_size)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5,5), 0)
    edges = cv2.Canny(blur, 50, 150)
    lines = cv2.HoughLinesP(edges,1,np.pi/180,threshold=80,minLineLength=80,maxLineGap=20)

    val = -1
    if lines is not None:
        longest_line = max(lines,key=lambda line: np.linalg.norm([line[0][0]-line[0][2],line[0][1]-line[0][3]]))
        x1,y1,x2,y2 = longest_line[0]
        cv2.line(img,(x1,y1),(x2,y2),(0,0,255),2)

        center = (img.shape[1]//2,img.shape[0]//2)
        d1 = np.linalg.norm([x1-center[0],y1-center[1]])
        d2 = np.linalg.norm([x2-center[0],y2-center[1]])
        needle_end = (x1,y1) if d1>d2 else (x2,y2)
        needle_vector = np.array([needle_end[0]-center[0], center[1]-needle_end[1]])
        angle_rad = np.arctan2(needle_vector[1], needle_vector[0])
        angle_deg = (90 - np.degrees(angle_rad)) % 360
        arc_span = 360 - meter_min_angle + meter_max_angle
        if angle_deg >= meter_min_angle:
            val = (angle_deg - meter_min_angle)*(meter_max_value-meter_min_value)/arc_span
        else:
            val = (angle_deg + (360 - meter_min_angle))*(meter_max_value-meter_min_value)/arc_span
        val = round(np.clip(val,meter_min_value,meter_max_value),2)

    with open(output_value_file,"w") as f:
        f.write(str(val))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_filename = f"latest_{val}_{timestamp}.jpg"
    archive_path = os.path.join(archive_dir,archive_filename)
    last_archived_file = archive_path
    try:
        if blur_archive:
            k = blur_kernel if blur_kernel %2==1 else blur_kernel+1
            img_for_archive = cv2.GaussianBlur(img,(k,k),0)
            cv2.imwrite(archive_path,img_for_archive)
            os.remove(image_name)
        else:
            shutil.move(image_name,archive_path)
    except Exception as e:
        print(f"Archiving failed: {e}")

    k = blur_kernel if blur_kernel %2==1 else blur_kernel+1
    blurred_img = cv2.GaussianBlur(img.copy(),(k,k),0)
    scale_factor = 0.7
    unblurred_resized = cv2.resize(img,(0,0),fx=scale_factor,fy=scale_factor)
    blurred_resized = cv2.resize(blurred_img,(0,0),fx=scale_factor,fy=scale_factor)
    combined = np.hstack((unblurred_resized,blurred_resized))
    combined_rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(combined_rgb)
    tk_img = ImageTk.PhotoImage(pil_img)
    return val, tk_img

def update_gui():
    global workorder_sent
    val, tk_img = process_image()
    if val is not None:
        value_label.config(text=f"Meter Value: {val}")
        image_label.config(image=tk_img)
        image_label.image = tk_img

        if val < 15 or val > 66:
            notify_label.config(
                text="❗ Reading appears to be inaccurate\nReading outside expected range.\n"
                     "This could be due to shadows or lighting issues.\nMove camera position and try again.",
                fg="red", justify="center")
            clear_button.config(state="normal")
        elif val <= 25:
            notify_label.config(text="Temperature OK ✅", fg="green")
            workorder_sent = False
            clear_button.config(state="disabled")
        else:
            msg = "Warning! Value exceeds safe working temperature ⚠️"
            color = "red"
            if mqtt_connected and not workorder_sent:
                try:
                    client.publish(mqtt_topic,mqtt_payload)
                    msg += "\nInspection work order generated in Maximo"
                    color = "blue"
                    workorder_sent = True
                except Exception as e:
                    msg += f"\nMQTT publish failed: {e}"
                    color="orange"
            notify_label.config(text=msg, fg=color, justify="center")
            clear_button.config(state="normal")

    root.after(update_interval,update_gui)

def clear_alert():
    global workorder_sent
    workorder_sent=False
    notify_label.config(text="Alerts cleared. Monitoring resumed...", fg="black")
    clear_button.config(state="disabled")

def conversion_good():
    global last_archived_file
    if last_archived_file and os.path.exists(last_archived_file):
        base, ext = os.path.splitext(last_archived_file)
        new_name = f"{base}_GOOD_{ext}"
        os.rename(last_archived_file,new_name)
        last_archived_file = new_name
        notify_label.config(text="File marked as GOOD ✅", fg="green")

def conversion_bad():
    global last_archived_file
    if last_archived_file and os.path.exists(last_archived_file):
        base, ext = os.path.splitext(last_archived_file)
        new_name = f"{base}_BAD_{ext}"
        os.rename(last_archived_file,new_name)
        last_archived_file = new_name
        notify_label.config(text="File marked as BAD ❌", fg="red")

def on_exit():
    root.destroy()

# GUI Setup
root = tk.Tk()
root.title("Analogue Meter Reader")
root.geometry("1000x900")

title_label = tk.Label(root, text="Analogue Meter Reader", font=("Arial", 20, "bold"))
title_label.pack(pady=10)

value_label = tk.Label(root, text="Waiting for image...", font=("Arial", 18))
value_label.pack(pady=10)

image_label = tk.Label(root)
image_label.pack(pady=10)

notify_label = tk.Label(root, text="System idle...", font=("Arial",16), justify="center")
notify_label.pack(pady=15)

clear_button = tk.Button(root, text="Clear Alert", font=("Arial",14,"bold"),
                         bg="lightgray", fg="black", state="disabled", command=clear_alert)
clear_button.pack(pady=5)

# Frame for question + buttons
conversion_frame = tk.Frame(root)
conversion_frame.pack(pady=5)

question_label = tk.Label(conversion_frame, text="Is returned number accurate?", font=("Arial",14))
question_label.pack(side="left", padx=5)

conversion_good_btn = tk.Button(conversion_frame, text="✔ Good", font=("Arial",14,"bold"),
                                bg="white", fg="black", width=12, command=conversion_good)
conversion_good_btn.pack(side="left", padx=5)

conversion_bad_btn = tk.Button(conversion_frame, text="❌ Bad", font=("Arial",14,"bold"),
                               bg="white", fg="black", width=12, command=conversion_bad)
conversion_bad_btn.pack(side="left", padx=5)

exit_button = tk.Button(root, text="Exit", font=("Arial",16,"bold"),
                        bg="white", fg="black", command=on_exit)
exit_button.pack(side="bottom", fill="x", padx=20, pady=20)

root.after(1000,update_gui)
root.mainloop()
